"""Execution policy and the planner shared by the research simulator and the paper broker.

Given targets, current holdings (with lots) and prices, ``plan`` returns the
dollar trades that implement a policy: calendar full rebalance or drift bands,
optional tax-loss harvesting into pre-registered substitutes, cash deployment,
no-leverage funding and a participation cap. Both engines call this function,
so research and execution cannot drift apart. Thresholds are in SPEC section 14.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import SPEC, SUBSTITUTES
from .tax import Lot, select_lots


@dataclass(frozen=True)
class ExecutionPolicy:
    name: str = "P0"
    lot_method: str = "FIFO"                 # FIFO | HIFO | TAXOPT
    band: float | None = None                # relative drift band; None = trade every group to target
    cash_deploy_threshold: float = 0.01      # deploy cash above this share of equity (band mode)
    harvest: bool = False
    harvest_min_loss_pct: float = 0.05       # of lot basis
    harvest_min_loss_equity_bps: float = 5.0 # lot loss must also be >= this many bps of equity
    harvest_no_recent_buy_days: int = 30     # skip harvesting a fund bought this recently
    no_rebuy_days: int = 31                  # after a loss sale, do not repurchase the same fund
    substitutes: tuple[tuple[str, str], ...] = ()
    max_participation: float | None = None   # per-order cap as a fraction of trailing dollar volume

    @property
    def subs(self) -> dict[str, str]:
        return dict(self.substitutes)

    @property
    def is_full_rebalance(self) -> bool:
        return self.band is None and not self.harvest and self.max_participation is None

    def with_(self, **kw) -> "ExecutionPolicy":
        from dataclasses import replace
        return replace(self, **kw)


def presets(lot_method_p0: str = SPEC.lot_method, substitutes: dict[str, str] | None = None) -> dict[str, ExecutionPolicy]:
    subs = tuple((substitutes or SUBSTITUTES).items())
    p0 = ExecutionPolicy(name="P0 calendar, FIFO", lot_method=lot_method_p0)
    p1 = p0.with_(name="P1 TAXOPT lots", lot_method="TAXOPT")
    p2 = p1.with_(name="P2 + drift band 20%", band=0.20)
    p3 = p2.with_(name="P3 + loss harvesting", harvest=True, substitutes=subs)
    return {p.name: p for p in (p0, p1, p2, p3)}


@dataclass
class HoldingView:
    ticker: str
    price: float
    lots: list[tuple[int, float, float, pd.Timestamp]] = field(default_factory=list)  # (index, units, basis, date)
    last_buy: pd.Timestamp | None = None
    last_loss_sale: pd.Timestamp | None = None

    @property
    def units(self) -> float:
        return sum(u for _, u, _, _ in self.lots)

    @property
    def value(self) -> float:
        return self.units * self.price


@dataclass
class Trade:
    ticker: str
    dollars: float                      # +buy / -sell at planning prices
    reason: str
    lot_plan: list[tuple[int, float]] | None = None
    capped: bool = False
    requested: float | None = None      # dollars before any cap/scaling

    @property
    def units(self) -> float:
        return 0.0

    def to_dict(self) -> dict:
        return {"ticker": self.ticker, "dollars": round(self.dollars, 2), "reason": self.reason,
                "capped": self.capped, "requested": None if self.requested is None else round(self.requested, 2),
                "lots": None if self.lot_plan is None else [(i, round(u, 6)) for i, u in self.lot_plan]}


def _recent(date: pd.Timestamp, other: pd.Timestamp | None, days: int) -> bool:
    return other is not None and 0 <= (date - other).days < days


def plan(date: pd.Timestamp, targets: pd.Series, holdings: dict[str, HoldingView], cash: float,
         prices: pd.Series, policy: ExecutionPolicy, adv: pd.Series | None = None,
         long_term_days: int = SPEC.long_term_days) -> list[Trade]:
    """Dollar trades implementing ``policy`` for one decision date."""
    date = pd.Timestamp(date)
    targets = targets[targets.index.isin(prices.index)].astype(float)
    subs = policy.subs
    value = {t: h.value for t, h in holdings.items()}          # mutable projection through the stages
    equity = cash + sum(value.values())
    if equity <= 0:
        return []

    # exposure groups: target ticker (+ its substitute); held leftovers become zero-target groups
    members_of: dict[str, list[str]] = {}
    for x in targets.index:
        mem = [x]
        s = subs.get(x)
        if s and s in prices.index:
            mem.append(s)
        members_of[x] = mem
    covered = {m for ms in members_of.values() for m in ms}
    for t in holdings:
        if t not in covered and value.get(t, 0.0) > 0:
            members_of[t] = [t]
            targets = pd.concat([targets, pd.Series({t: 0.0})])
    buys: dict[str, float] = {}
    sells: dict[str, float] = {}
    reasons: dict[str, str] = {}
    lot_plans: dict[str, list[tuple[int, float]]] = {}
    pending_loss_sale: set[str] = set()

    def add_buy(t: str, d: float, why: str) -> None:
        if d <= 0:
            return
        buys[t] = buys.get(t, 0.0) + d
        reasons.setdefault(t, why)
        value[t] = value.get(t, 0.0) + d

    def add_sell(t: str, d: float, why: str, lots: list[tuple[int, float]] | None = None) -> None:
        d = min(d, value.get(t, 0.0))
        if d <= 0:
            return
        sells[t] = sells.get(t, 0.0) + d
        reasons.setdefault(t, why)
        value[t] = value.get(t, 0.0) - d
        if lots:
            lot_plans[t] = lot_plans.get(t, []) + lots

    def restricted(t: str) -> bool:
        """Cannot buy ``t`` now: loss-sold within the no-rebuy window (harvesting policies only)."""
        if not policy.harvest:
            return False
        h = holdings.get(t)
        last = h.last_loss_sale if h else None
        return t in pending_loss_sale or _recent(date, last, policy.no_rebuy_days)

    def vehicle_for_buy(members: list[str]) -> str | None:
        held = sorted((m for m in members if value.get(m, 0.0) > 0), key=lambda m: -value[m])
        for m in held + [m for m in members if m not in held]:
            if not restricted(m):
                return m
        return None

    # ---- stage 1: harvesting ------------------------------------------------- #
    if policy.harvest:
        for x, members in members_of.items():
            for m in members:
                h = holdings.get(m)
                if h is None or not h.lots or len(members) < 2:
                    continue
                other = [o for o in members if o != m][0]
                if _recent(date, h.last_buy, policy.harvest_no_recent_buy_days) or restricted(other):
                    continue
                qualifying = []
                for i, u, basis, _ in h.lots:
                    loss = u * h.price - basis
                    if loss <= -max(policy.harvest_min_loss_pct * basis, policy.harvest_min_loss_equity_bps * equity / 1e4):
                        qualifying.append((i, u))
                if not qualifying:
                    continue
                dollars = sum(u for _, u in qualifying) * h.price
                add_sell(m, dollars, "harvest", qualifying)
                pending_loss_sale.add(m)
                add_buy(other, dollars, "harvest")

    # ---- stage 2: rebalance (full or band) ------------------------------------ #
    for x, members in members_of.items():
        tgt = float(targets.get(x, 0.0)) * equity
        cur = sum(value.get(m, 0.0) for m in members)
        if policy.band is not None and tgt > 0:
            w, wt = cur / equity, tgt / equity
            if wt * (1 - policy.band) <= w <= wt * (1 + policy.band):
                continue
        delta = tgt - cur
        if abs(delta) < 1e-9:
            continue
        if delta > 0:
            v = vehicle_for_buy(members)
            if v is not None:
                add_buy(v, delta, "rebalance")
        else:
            need = -delta
            # sell the smaller holding first (consolidates the group), then the larger
            for m in sorted((m for m in members if value.get(m, 0.0) > 0), key=lambda m: value[m]):
                take = min(need, value[m])
                add_sell(m, take, "rebalance")
                need -= take
                if need <= 1e-9:
                    break

    # ---- stage 3: cash deployment (band mode) --------------------------------- #
    if policy.band is not None:
        target_cash = max(0.0, 1.0 - float(targets.clip(lower=0).sum())) * equity
        projected = cash + sum(sells.values()) - sum(buys.values())
        floor = max(policy.cash_deploy_threshold * equity, target_cash)
        excess = projected - floor
        if excess > 1e-6:
            short = []
            for x, members in members_of.items():
                tgt = float(targets.get(x, 0.0)) * equity
                cur = sum(value.get(m, 0.0) for m in members)
                if tgt > cur:
                    short.append((x, members, tgt - cur))
            for x, members, gap in sorted(short, key=lambda r: -r[2] / max(float(targets.get(r[0], 0.0)) * equity, 1e-9)):
                if excess <= 1e-6:
                    break
                v = vehicle_for_buy(members)
                if v is None:
                    continue
                d = min(gap, excess)
                add_buy(v, d, "deploy")
                excess -= d

    # ---- stage 4: no leverage ------------------------------------------------- #
    available = cash + sum(sells.values())
    total_buys = sum(buys.values())
    if total_buys > 0 and total_buys > available + 1e-9:
        scale = max(available, 0.0) / total_buys
        for t in buys:
            buys[t] *= scale

    # ---- stage 5: participation cap and lot plans ----------------------------- #
    trades: list[Trade] = []
    for t in sorted(set(buys) | set(sells)):
        net = buys.get(t, 0.0) - sells.get(t, 0.0)
        if abs(net) < 1e-9:
            continue
        tr = Trade(ticker=t, dollars=net, reason=reasons.get(t, "rebalance"), requested=net)
        if policy.max_participation is not None and adv is not None:
            a = float(adv.get(t, float("nan")))
            if a == a and a > 0 and abs(net) > policy.max_participation * a:
                tr.dollars = policy.max_participation * a * (1 if net > 0 else -1)
                tr.capped = True
        if tr.dollars < 0:
            h = holdings.get(t)
            units_to_sell = -tr.dollars / prices[t]
            planned = lot_plans.get(t, [])
            planned_units = sum(u for _, u in planned)
            if planned_units > units_to_sell + 1e-12:         # cap truncated a harvest sale
                plan_, rem = [], units_to_sell
                for i, u in planned:
                    take = min(u, rem)
                    if take > 1e-12:
                        plan_.append((i, take))
                    rem -= take
                planned = plan_
            elif units_to_sell - planned_units > 1e-12 and h is not None:
                used = {i for i, _ in planned}
                rest = [Lot(d, u, b) for i, u, b, d in h.lots if i not in used]
                rest_idx = [i for i, _, _, _ in h.lots if i not in used]
                extra = select_lots(rest, units_to_sell - planned_units, policy.lot_method, prices[t], date, long_term_days)
                planned = planned + [(rest_idx[j], u) for j, u in extra]
            tr.lot_plan = planned
        trades.append(tr)
    # sells first so the engines have the cash to fund the buys
    return sorted(trades, key=lambda tr: (tr.dollars > 0, tr.ticker))
