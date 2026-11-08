"""Pre-trade risk controls.

Every limit is explicit, pre-registered and testable. A check either passes,
warns, scales the offending orders down, or blocks the whole decision. The
report is attached to the decision record so the reasoning is auditable.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .config import SPEC

PASS, WARN, BLOCK = "pass", "warn", "block"
_RANK = {PASS: 0, WARN: 1, BLOCK: 2}


@dataclass(frozen=True)
class RiskLimits:
    max_weight: float = SPEC.w_max          # per-position cap (strategies may pass a tighter one)
    max_gross: float = 1.0                  # no leverage
    max_turnover_one_way: float = 0.75      # per rebalance, fraction of equity
    max_participation: float = 0.10         # order notional / trailing average dollar volume
    min_order_notional: float = 500.0       # dust filter, dollars
    min_cash: float = 0.0                   # cash after trades and estimated costs
    max_staleness_days: int = 5             # calendar days between signal date and now
    drawdown_halt: float = -0.25            # paper/live book drawdown that suppresses new risk
    cost_reserve_bps: float = 10.0          # equity held back from the targets to pay fills and costs
    allow_fractional: bool = False


@dataclass
class Order:
    ticker: str
    side: str            # BUY | SELL
    shares: float
    price: float
    adv: float | None = None
    note: str = ""
    reason: str = "rebalance"
    lot_plan: list[tuple[int, float]] | None = None   # specific lots to relieve (sells)

    @property
    def notional(self) -> float:
        return self.shares * self.price

    @property
    def signed_notional(self) -> float:
        return self.notional if self.side == "BUY" else -self.notional

    @property
    def participation(self) -> float | None:
        return None if not self.adv or self.adv <= 0 else self.notional / self.adv

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(notional=round(self.notional, 2),
                 participation=None if self.participation is None else round(self.participation, 4),
                 lot_plan=None if self.lot_plan is None else [(int(i), round(float(u), 6)) for i, u in self.lot_plan])
        return d


def truncate_lot_plan(plan: list[tuple[int, float]] | None, units: float) -> list[tuple[int, float]] | None:
    """Shrink a lot plan to ``units`` (keeps the planner's priority order)."""
    if plan is None:
        return None
    out, rem = [], units
    for i, u in plan:
        take = min(u, rem)
        if take > 1e-12:
            out.append((i, take))
        rem -= take
    return out


@dataclass
class RiskCheck:
    name: str
    status: str
    value: object = None
    limit: object = None
    detail: str = ""


@dataclass
class RiskReport:
    checks: list[RiskCheck] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    dropped: list[Order] = field(default_factory=list)
    halted: bool = False

    @property
    def status(self) -> str:
        return max((c.status for c in self.checks), key=lambda s: _RANK[s], default=PASS)

    @property
    def blocked(self) -> bool:
        return self.status == BLOCK

    def to_dict(self) -> dict:
        return {"status": self.status, "halted": self.halted,
                "checks": [asdict(c) for c in self.checks],
                "orders": [o.to_dict() for o in self.orders],
                "dropped": [o.to_dict() for o in self.dropped]}


def orders_from_targets(targets: pd.Series, positions: dict[str, float], prices: pd.Series, cash: float,
                        adv: pd.Series | None = None, limits: RiskLimits = RiskLimits()) -> list[Order]:
    """Whole-share orders that move ``positions`` (shares) toward ``targets`` (weights)."""
    tickers = sorted(set(targets.index) | set(positions))
    equity = cash + sum(positions.get(t, 0.0) * float(prices[t]) for t in positions if t in prices.index)
    equity *= 1.0 - limits.cost_reserve_bps / 1e4   # leave room for costs so cash never goes negative
    orders: list[Order] = []
    for t in tickers:
        px = float(prices.get(t, np.nan))
        if not np.isfinite(px) or px <= 0:
            if targets.get(t, 0.0) > 0:
                raise ValueError(f"no price for {t}")
            continue
        target_shares = float(targets.get(t, 0.0)) * equity / px
        delta = target_shares - positions.get(t, 0.0)
        shares = abs(delta) if limits.allow_fractional else math.floor(abs(delta) + 1e-9)
        if shares <= 0:
            continue
        side = "BUY" if delta > 0 else "SELL"
        if side == "SELL":
            shares = min(shares, positions.get(t, 0.0))
        a = None if adv is None else (float(adv[t]) if t in adv.index and np.isfinite(adv[t]) else None)
        orders.append(Order(ticker=t, side=side, shares=shares, price=px, adv=a))
    # sells first so that cash is available for buys
    return sorted(orders, key=lambda o: (o.side != "SELL", o.ticker))


def pre_trade_checks(orders: list[Order], targets: pd.Series, equity: float, cash: float,
                     est_cost_bps: float = 5.0, limits: RiskLimits = RiskLimits(),
                     cap: float | None = None, staleness_days: int | None = None,
                     drawdown: float | None = None) -> RiskReport:
    rep = RiskReport()
    cap = limits.max_weight if cap is None else cap

    # -- portfolio-level constraints on the targets ------------------------- #
    neg = targets[targets < -1e-12]
    rep.checks.append(RiskCheck("long_only", BLOCK if len(neg) else PASS, len(neg), 0,
                                ", ".join(neg.index) if len(neg) else ""))
    wmax = float(targets.max()) if len(targets) else 0.0
    rep.checks.append(RiskCheck("max_weight", BLOCK if wmax > cap + 1e-9 else PASS, round(wmax, 4), cap,
                                str(targets.idxmax()) if len(targets) else ""))
    gross = float(targets.clip(lower=0).sum())
    rep.checks.append(RiskCheck("gross_exposure", BLOCK if gross > limits.max_gross + 1e-9 else PASS,
                                round(gross, 4), limits.max_gross))

    # -- staleness and kill switch ------------------------------------------ #
    if staleness_days is not None:
        rep.checks.append(RiskCheck("signal_staleness_days", BLOCK if staleness_days > limits.max_staleness_days else PASS,
                                    staleness_days, limits.max_staleness_days))
    if drawdown is not None:
        halted = drawdown <= limits.drawdown_halt
        rep.halted = halted
        rep.checks.append(RiskCheck("drawdown_halt", WARN if halted else PASS, round(drawdown, 4), limits.drawdown_halt,
                                    "new risk suppressed: buys removed" if halted else ""))
        if halted:
            buys = [o for o in orders if o.side == "BUY"]
            rep.dropped.extend(buys)
            orders = [o for o in orders if o.side == "SELL"]

    # -- order-level constraints -------------------------------------------- #
    kept: list[Order] = []
    scaled: list[str] = []
    dust: list[str] = []
    for o in orders:
        if o.notional < limits.min_order_notional:
            o.note = "below min notional"
            rep.dropped.append(o)
            dust.append(o.ticker)
            continue
        p = o.participation
        if p is not None and p > limits.max_participation:
            max_shares = limits.max_participation * o.adv / o.price
            new_shares = max_shares if limits.allow_fractional else math.floor(max_shares)
            if new_shares <= 0:
                o.note = "participation limit leaves no size"
                rep.dropped.append(o)
                continue
            o.note = f"scaled from {o.shares:g} to {new_shares:g} shares (participation {p:.1%} > {limits.max_participation:.0%})"
            o.shares = new_shares
            o.lot_plan = truncate_lot_plan(o.lot_plan, new_shares)
            scaled.append(o.ticker)
        kept.append(o)
    rep.checks.append(RiskCheck("participation", WARN if scaled else PASS, len(scaled), limits.max_participation,
                                f"scaled: {', '.join(scaled)}" if scaled else ""))
    if dust:
        rep.checks.append(RiskCheck("min_order_notional", PASS, len(dust), limits.min_order_notional,
                                    f"dropped dust orders: {', '.join(dust)}"))

    turnover = sum(o.notional for o in kept) / (2.0 * equity) if equity > 0 else 0.0
    rep.checks.append(RiskCheck("turnover_one_way", BLOCK if turnover > limits.max_turnover_one_way else PASS,
                                round(turnover, 4), limits.max_turnover_one_way))
    est_costs = sum(o.notional for o in kept) * est_cost_bps / 1e4
    cash_after = cash - sum(o.signed_notional for o in kept) - est_costs
    rep.checks.append(RiskCheck("cash_after_trades", BLOCK if cash_after < limits.min_cash - 1e-6 else PASS,
                                round(cash_after, 2), limits.min_cash, f"estimated costs {est_costs:,.0f}"))
    rep.orders = kept
    return rep
