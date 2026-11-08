"""The decision pipeline: data -> health -> targets -> orders -> risk -> record -> (execute).

``rebalance`` is idempotent per (strategy, signal date): a second submission is
refused unless forced. Every call, dry-run or not, writes a decision record.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from . import audit
from .baselines import equal_weight, sixty_forty_classic, sixty_forty_vaneck
from .broker import PaperBroker
from .calendar import Split
from .config import ROOT, SPEC, Spec
from .costs import CostModel
from .data import MarketData
from .execution import ExecutionPolicy, plan
from .health import HealthLimits, check_market_data
from .regimes import regime_at
from .risk import Order, RiskLimits, orders_from_targets, pre_trade_checks, truncate_lot_plan
from .signals import SIGNALS
from .strategies import OPTIMIZERS, make_strategy, strategy_name
from .study import TRADEABLES

log = logging.getLogger(__name__)
PAPER_DIR = ROOT / "paper"


def strategy_registry(spec: Spec = SPEC) -> dict[str, tuple]:
    """name -> (weight_fn, position cap used by the risk checks)."""
    reg = {
        "EqualWeight": (equal_weight, spec.scaffold_cap),
        "60/40 VanEck": (sixty_forty_vaneck, 0.40),
        "60/40 Classic": (sixty_forty_classic, 0.60),
    }
    for sig in SIGNALS:
        for opt in OPTIMIZERS:
            for ov in (False, True):
                reg[strategy_name(sig, opt, ov)] = (make_strategy(sig, opt, ov, spec), spec.w_max)
    return reg


def slug(name: str) -> str:
    return name.replace("/", "-").replace(" ", "_").replace("+", "_")


def decision_split(md: MarketData, as_of=None, spec: Spec = SPEC) -> Split:
    """Signal on the last bar at or before ``as_of``; execute on the next trading day."""
    idx = md.index
    sig = idx[-1] if as_of is None else idx[idx <= pd.Timestamp(as_of)][-1]
    pos = idx.get_loc(sig)
    exec_date = idx[pos + 1] if pos + 1 < len(idx) else sig + pd.offsets.BDay(1)
    return Split(train_start=sig - pd.DateOffset(years=spec.train_years), signal_date=sig,
                 exec_date=exec_date, test_end=exec_date)


def find_decision(decisions_dir: Path, strategy: str, signal_date: pd.Timestamp, status: str = "submitted") -> Path | None:
    pattern = f"{signal_date.date()}_{slug(strategy)}_{status}.json"
    p = decisions_dir / pattern
    return p if p.exists() else None


def orders_from_plan(date, targets: pd.Series, broker: PaperBroker, prices: pd.Series, adv: pd.Series,
                     policy: ExecutionPolicy, limits: RiskLimits, spec: Spec = SPEC) -> list[Order]:
    """Whole-share orders from the shared execution planner (same rules as the research engine)."""
    cash = broker.cash() * (1.0 - limits.cost_reserve_bps / 1e4)
    trades = plan(date, targets, broker.holding_views(date), cash, prices, policy, adv, spec.long_term_days)
    orders = []
    held = broker.positions()
    for tr in trades:
        px = float(prices[tr.ticker])
        shares = abs(tr.dollars) / px
        shares = shares if limits.allow_fractional else int(shares + 1e-9)
        if tr.dollars < 0:
            shares = min(shares, held.get(tr.ticker, 0.0))
        if shares <= 0:
            continue
        a = float(adv.get(tr.ticker, float("nan")))
        orders.append(Order(ticker=tr.ticker, side="BUY" if tr.dollars > 0 else "SELL", shares=shares, price=px,
                            adv=None if a != a else a, reason=tr.reason,
                            lot_plan=truncate_lot_plan(tr.lot_plan, shares) if tr.dollars < 0 else None,
                            note="capped by planner" if tr.capped else ""))
    return sorted(orders, key=lambda o: (o.side != "SELL", o.ticker))


def rebalance(md: MarketData, strategy: str, broker: PaperBroker, decisions_dir: Path, spec: Spec = SPEC,
              limits: RiskLimits = RiskLimits(), as_of=None, dry_run: bool = True, force: bool = False,
              now=None, health_limits: HealthLimits = HealthLimits(), cost_model: CostModel | None = None,
              policy: ExecutionPolicy | None = None) -> dict:
    cost_model = cost_model or CostModel(spec)
    reg = strategy_registry(spec)
    if strategy not in reg:
        raise KeyError(f"unknown strategy {strategy}; choose from {sorted(reg)}")
    fn, cap = reg[strategy]
    run_id = audit.new_run_id()
    split = decision_split(md, as_of, spec)
    mdw = md.window(end=split.signal_date)                      # nothing below sees past the signal date
    now_ts = pd.Timestamp(now) if now is not None else (split.exec_date if as_of is not None else pd.Timestamp.now())
    health = check_market_data(mdw, spec, now=now_ts, limits=health_limits)

    win = md.window(end=split.signal_date, start=split.train_start)
    targets = pd.Series(fn(win, split), dtype=float).reindex(TRADEABLES).fillna(0.0)
    regime = regime_at(win.macro, spec)

    if broker.as_of < split.signal_date:
        broker.advance_to(split.signal_date)
    positions, cash = broker.positions(), broker.cash()
    prices = mdw.close.iloc[-1]
    equity = broker.equity(split.signal_date)
    adv = cost_model.adv_table(mdw).iloc[-1]
    if policy is not None:
        orders = orders_from_plan(split.signal_date, targets, broker, prices.dropna(), adv, policy, limits, spec)
    else:
        orders = orders_from_targets(targets, positions, prices, cash, adv, limits)
    staleness = int((now_ts.normalize() - split.signal_date.normalize()).days)
    est_bps = float(pd.Series([cost_model.one_way_bps(o.ticker, o.adv or 0.0) for o in orders]).mean()) if orders else 0.0
    risk = pre_trade_checks(orders, targets, equity, cash, est_cost_bps=est_bps, limits=limits, cap=cap,
                            staleness_days=staleness, drawdown=broker.drawdown())

    duplicate = find_decision(decisions_dir, strategy, split.signal_date)
    notes = []
    if duplicate and not force:
        status = "duplicate"
        notes.append(f"already submitted for this signal date: {duplicate.name}")
    elif health.blocked or risk.blocked:
        status = "blocked"
    elif dry_run:
        status = "dry_run"
    elif split.exec_date not in md.index:
        status = "awaiting_exec_bar"
        notes.append(f"execution bar {split.exec_date.date()} not in data yet; re-run with --submit once it is")
    else:
        status = "submitted"

    fills, executed = [], None
    if status == "submitted":
        fills = [f.__dict__ for f in broker.submit(risk.orders, split.exec_date)]
        notes.append(f"{len(fills)} fills at the {split.exec_date.date()} close")
        executed = {k: round(v, 6) for k, v in broker.weights(split.exec_date).items() if k != "CASH"}

    decision = {
        "run_id": run_id, "created_utc": audit.utc_now(), "strategy": strategy, "status": status,
        "signal_date": str(split.signal_date.date()), "exec_date": str(split.exec_date.date()),
        "regime": regime, "equity": equity, "cash": cash, "policy": None if policy is None else policy.name,
        "current_weights": {k: round(v, 6) for k, v in broker.weights(split.signal_date).items()},
        "targets": {k: round(v, 6) for k, v in targets.items() if v > 0},
        "executed_weights": executed,
        "health": health.to_dict(), "risk": risk.to_dict(), "fills": fills, "notes": notes,
        "manifest": audit.run_manifest("rebalance", spec, run_id=run_id, extra={"dry_run": dry_run, "force": force}),
    }
    decisions_dir.mkdir(parents=True, exist_ok=True)
    path = decisions_dir / f"{split.signal_date.date()}_{slug(strategy)}_{status}.json"
    path.write_text(json.dumps(decision, indent=2, default=str))
    with (decisions_dir / "log.md").open("a") as fh:
        fh.write(f"| {decision['created_utc']} | {strategy} | {split.signal_date.date()} | {regime} | {status} | "
                 f"{health.status} | {risk.status} | {len(risk.orders)} orders | {len(fills)} fills | {run_id} |\n")
    log.info("decision %s %s signal=%s status=%s health=%s risk=%s orders=%d", run_id, strategy,
             split.signal_date.date(), status, health.status, risk.status, len(risk.orders))
    return decision


def replay(md: MarketData, strategy: str, book_path: Path, decisions_dir: Path, start, end, cash: float,
           spec: Spec = SPEC, limits: RiskLimits = RiskLimits(), cost_model: CostModel | None = None,
           policy: ExecutionPolicy | None = None) -> list[dict]:
    """Operational rehearsal: run the monthly cycle through the paper broker over a past window."""
    from .calendar import rebalance_schedule
    sched = rebalance_schedule(md.index, start, end, spec.exec_lag_days)
    broker = PaperBroker(book_path, md, cost_model, spec)
    if book_path.exists():
        book_path.unlink()
        broker = PaperBroker(book_path, md, cost_model, spec)
    first_signal = sched["signal_date"].iloc[0]
    broker.init(cash, first_signal, name=f"replay {strategy}")
    out = []
    for _, row in sched.iterrows():
        out.append(rebalance(md, strategy, broker, decisions_dir, spec, limits, as_of=row["signal_date"],
                             dry_run=False, cost_model=cost_model, policy=policy))
    broker.advance_to(md.index[md.index <= pd.Timestamp(end)][-1])
    return out
