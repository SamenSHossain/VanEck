"""Daily portfolio simulator with costs, tax lots, an execution policy and a lookahead guard.

``build_targets`` turns a weight function into a table of target weights, one
row per execution date, where the function only ever sees ``md.window(signal_date)``.
``simulate`` then implements those targets at the close of each execution date
through the shared execution planner (``vaneck.execution.plan``), so the research
engine and the paper broker trade by the same rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .calendar import Split
from .config import CASH, SPEC, Spec
from .costs import CostModel
from .data import MarketData
from .execution import ExecutionPolicy, HoldingView, plan
from .tax import TaxLedger

WeightFn = Callable[[MarketData, Split], "pd.Series | dict[str, float]"]


@dataclass
class SimResult:
    name: str
    taxed: bool
    nav: pd.Series
    gross_value: pd.Series
    cash: pd.Series
    weights: pd.DataFrame
    targets: pd.DataFrame
    trades: pd.DataFrame
    turnover: pd.Series
    costs: pd.Series
    taxes_paid: pd.Series
    realized: pd.DataFrame
    rf: pd.Series
    initial_cost: float
    ledger: TaxLedger
    harvests: pd.Series | None = None
    capped: pd.Series | None = None
    policy: ExecutionPolicy | None = None

    @property
    def returns(self) -> pd.Series:
        return self.nav.pct_change(fill_method=None).dropna()


def build_targets(md: MarketData, splits: list[Split], weight_fn: WeightFn,
                  tradeables: list[str]) -> pd.DataFrame:
    """Evaluate ``weight_fn`` on a strictly-past window for every split."""
    rows = {}
    for s in splits:
        win = md.window(end=s.signal_date, start=s.train_start)
        assert win.end <= s.signal_date, "lookahead guard violated"
        w = pd.Series(weight_fn(win, s), dtype=float)
        rows[s.exec_date] = w
    tgt = pd.DataFrame(rows).T.reindex(columns=list(tradeables)).fillna(0.0)
    tgt.index.name = "exec_date"
    if (tgt.sum(axis=1) > 1.0 + 1e-9).any():
        raise ValueError("target weights exceed 100%")
    if (tgt < -1e-12).any().any():
        raise ValueError("negative target weight (long-only)")
    return tgt


def simulate(md: MarketData, targets: pd.DataFrame, spec: Spec = SPEC, taxed: bool = False,
             cost_model: CostModel | None = None, end=None, initial: float | None = None,
             name: str = "", policy: ExecutionPolicy | None = None) -> SimResult:
    """Run the daily simulation.

    ``targets``: rows = execution dates (trading days), columns = tickers (a
    ``CASH`` column is ignored: cash is the residual). Rebalances happen only on
    dates present in ``targets``. ``policy`` defaults to the main study's
    calendar full rebalance with the spec's lot method.
    """
    cost_model = cost_model or CostModel(spec)
    policy = policy or ExecutionPolicy(lot_method=spec.lot_method)
    initial = float(initial if initial is not None else spec.aum)
    base = [c for c in targets.columns if c != CASH]
    extra = [s for p, s in policy.subs.items() if p in base and s in md.adj_close.columns and s not in base]
    tickers = base + extra
    targets = targets[base].sort_index()
    if not targets.index.isin(md.index).all():
        raise ValueError("every execution date must be a trading day in the data")
    start = targets.index[0]
    end = pd.Timestamp(end) if end is not None else md.index[-1]
    days = md.index[(md.index >= start) & (md.index <= end)]
    n_days, n = len(days), len(tickers)
    col = {t: j for j, t in enumerate(tickers)}

    px = md.adj_close[tickers].reindex(days).to_numpy(dtype=float)
    adj_prev = md.adj_close[tickers].shift(1).reindex(days).to_numpy(dtype=float)
    close_prev = md.close[tickers].shift(1).reindex(days).to_numpy(dtype=float)
    dist = md.distributions[tickers].reindex(days).fillna(0.0).to_numpy(dtype=float)
    rf = md.cash_yield(spec).reindex(days).ffill().fillna(0.0)
    y = rf.to_numpy(dtype=float)
    adv = cost_model.adv_table(md)[tickers].reindex(days).to_numpy(dtype=float)
    vol = cost_model.vol_table(md)[tickers].reindex(days).to_numpy(dtype=float) if spec.impact_coef else None
    tgt = targets.reindex(days)
    is_exec = tgt.notna().any(axis=1).to_numpy()
    tgt_arr = tgt.fillna(0.0).to_numpy(dtype=float)

    ledger = TaxLedger(spec, taxed=taxed)
    units = np.zeros(n)
    cash = initial
    nav = np.empty(n_days)
    gross = np.empty(n_days)
    cash_arr = np.empty(n_days)
    weights = np.zeros((n_days, n))
    trades, costs, turnover, taxes_paid, harvests, capped = {}, {}, {}, {}, {}, {}
    last_settled_year: int | None = None
    initial_cost = 0.0

    for i in range(n_days):
        d = days[i]
        if i > 0:
            cash *= 1.0 + y[i - 1] / 252.0
        if dist[i].any():
            for j in np.flatnonzero(dist[i] > 0):
                if units[j] > 0 and np.isfinite(close_prev[i, j]) and close_prev[i, j] > 0:
                    ledger.distribution(tickers[j], d, units[j] * adj_prev[i, j] * dist[i, j] / close_prev[i, j])
        if is_exec[i]:
            if last_settled_year is None:
                last_settled_year = d.year
                ledger.settled_through = d.year - 1
            elif d.year > last_settled_year:
                tax = ledger.settle(d)
                cash -= tax
                if taxed:
                    taxes_paid[d] = tax
                last_settled_year = d.year
            price = px[i]
            w = pd.Series(tgt_arr[i], index=base)
            bad = [t for t in base if w[t] > 0 and not np.isfinite(price[col[t]])]
            if bad:
                raise ValueError(f"{d.date()}: target on unpriced ticker(s) {bad}")
            prices = pd.Series(price, index=tickers).dropna()
            holdings = {t: HoldingView(t, float(price[col[t]]), ledger.lot_views(t), ledger.last_buy.get(t),
                                       ledger.last_loss_sale.get(t))
                        for t in tickers if units[col[t]] > 0 and np.isfinite(price[col[t]])}
            value_before = float(np.nansum(np.where(np.isfinite(price), units * price, 0.0)) + cash)
            adv_s = pd.Series(adv[i], index=tickers) if policy.max_participation is not None else None
            plan_ = plan(d, w, holdings, cash, prices, policy, adv_s, spec.long_term_days)
            row = np.zeros(n)
            total_cost = 0.0
            n_harv = n_cap = 0
            for tr in plan_:
                j = col[tr.ticker]
                p_ = price[j]
                if tr.dollars < 0:
                    u = min(-tr.dollars / p_, units[j])
                    if u <= 0:
                        continue
                    proceeds = u * p_
                    ledger.sell(tr.ticker, d, u, proceeds, lot_plan=tr.lot_plan)
                    units[j] -= u
                    cash += proceeds
                    notional = -proceeds
                else:
                    u = tr.dollars / p_
                    ledger.buy(tr.ticker, d, u, tr.dollars)
                    units[j] += u
                    cash -= tr.dollars
                    notional = tr.dollars
                c = cost_model.trade_cost(tr.ticker, notional, adv[i, j], None if vol is None else vol[i, j])
                cash -= c
                total_cost += c
                row[j] += notional
                n_harv += tr.reason == "harvest" and tr.dollars < 0
                n_cap += tr.capped
            units[np.abs(units) < 1e-12] = 0.0
            trades[d] = row
            costs[d] = total_cost
            harvests[d] = n_harv
            capped[d] = n_cap
            if i == 0:
                initial_cost = total_cost
            else:
                turnover[d] = float(np.abs(row).sum() / (2.0 * value_before))
        pos = np.where(np.isfinite(px[i]), units * px[i], 0.0)
        g = pos.sum() + cash
        gross[i] = g
        cash_arr[i] = cash
        weights[i] = pos / g if g > 0 else 0.0
        nav[i] = g - (ledger.accrued_liability() if taxed else 0.0)

    wdf = pd.DataFrame(weights, index=days, columns=tickers)
    wdf[CASH] = pd.Series(cash_arr, index=days) / pd.Series(gross, index=days)
    return SimResult(
        name=name, taxed=taxed,
        nav=pd.Series(nav, index=days, name=name or "nav"),
        gross_value=pd.Series(gross, index=days, name="gross"),
        cash=pd.Series(cash_arr, index=days, name="cash"),
        weights=wdf, targets=targets,
        trades=pd.DataFrame(trades, index=tickers).T,
        turnover=pd.Series(turnover, dtype=float, name="turnover"),
        costs=pd.Series(costs, dtype=float, name="costs"),
        taxes_paid=pd.Series(taxes_paid, dtype=float, name="taxes_paid"),
        realized=pd.DataFrame(ledger.log),
        rf=rf, initial_cost=initial_cost, ledger=ledger,
        harvests=pd.Series(harvests, dtype=float, name="harvests"),
        capped=pd.Series(capped, dtype=float, name="capped"),
        policy=policy,
    )
