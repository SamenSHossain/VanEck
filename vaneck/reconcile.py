"""Position and cash reconciliation between the local shadow ledger and the broker."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd


@dataclass
class Break:
    symbol: str
    local_qty: float
    broker_qty: float
    diff_qty: float
    diff_value: float
    kind: str   # qty | missing_local | missing_broker


@dataclass
class ReconcileReport:
    as_of: str
    breaks: list[Break] = field(default_factory=list)
    cash_local: float = 0.0
    cash_broker: float = 0.0
    cash_diff: float = 0.0
    cash_break: bool = False
    n_positions_local: int = 0
    n_positions_broker: int = 0

    @property
    def clean(self) -> bool:
        return not self.breaks and not self.cash_break

    @property
    def status(self) -> str:
        return "ok" if self.clean else "break"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status
        return d

    def to_markdown(self) -> str:
        lines = [f"Reconciliation as of {self.as_of}: **{self.status.upper()}**",
                 f"cash local {self.cash_local:,.2f} vs broker {self.cash_broker:,.2f} (diff {self.cash_diff:+,.2f})"
                 + (" BREAK" if self.cash_break else ""), ""]
        if self.breaks:
            lines += ["| symbol | local | broker | diff qty | diff value | kind |", "|---|---|---|---|---|---|"]
            for b in self.breaks:
                lines.append(f"| {b.symbol} | {b.local_qty:g} | {b.broker_qty:g} | {b.diff_qty:+g} | "
                             f"{b.diff_value:+,.2f} | {b.kind} |")
        else:
            lines.append(f"{self.n_positions_broker} positions agree.")
        return "\n".join(lines)


def reconcile(local_positions: dict[str, float], broker_positions: dict[str, float], prices: pd.Series,
              local_cash: float, broker_cash: float, as_of, qty_tol: float = 1e-6,
              cash_tol: float = 1.0) -> ReconcileReport:
    rep = ReconcileReport(as_of=str(pd.Timestamp(as_of).date()), cash_local=local_cash, cash_broker=broker_cash,
                          cash_diff=broker_cash - local_cash, cash_break=abs(broker_cash - local_cash) > cash_tol,
                          n_positions_local=sum(1 for v in local_positions.values() if abs(v) > qty_tol),
                          n_positions_broker=sum(1 for v in broker_positions.values() if abs(v) > qty_tol))
    for sym in sorted(set(local_positions) | set(broker_positions)):
        lq, bq = float(local_positions.get(sym, 0.0)), float(broker_positions.get(sym, 0.0))
        if abs(lq - bq) <= qty_tol:
            continue
        px = float(prices.get(sym, float("nan")))
        kind = "missing_local" if abs(lq) <= qty_tol else "missing_broker" if abs(bq) <= qty_tol else "qty"
        rep.breaks.append(Break(sym, lq, bq, bq - lq, (bq - lq) * px if px == px else float("nan"), kind))
    return rep
