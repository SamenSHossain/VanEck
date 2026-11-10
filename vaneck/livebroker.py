"""Live broker: a remote venue for execution plus the local shadow ledger for lots, taxes and history.

Before every decision the shadow is reconciled against the venue; a break
blocks the decision unless it is explicitly adopted (recorded as a transfer).
After every submission the venue's fills are written into the shadow so lot
selection, wash-sale state and the equity history stay exact.
"""
from __future__ import annotations

import logging

import pandas as pd

from .broker import Fill, PaperBroker
from .config import SPEC, Spec
from .data import MarketData
from .execution import HoldingView
from .reconcile import ReconcileReport, reconcile
from .risk import Order

log = logging.getLogger(__name__)


class LiveBroker:
    def __init__(self, remote, shadow: PaperBroker, md: MarketData, spec: Spec = SPEC,
                 cash_tol_frac: float = 0.001, adopt_breaks: bool = False):
        self.remote = remote
        self.shadow = shadow
        self.md = md
        self.spec = spec
        self.cash_tol_frac = cash_tol_frac
        self.adopt_breaks = adopt_breaks
        self.last_reconcile: ReconcileReport | None = None

    # -- reconciliation ------------------------------------------------------------ #
    def sync(self, date=None, adopt: bool | None = None) -> ReconcileReport:
        date = pd.Timestamp(date) if date is not None else self.shadow.as_of
        remote_pos = self.remote.positions()
        remote_cash = self.remote.cash()
        remote_equity = self.remote.equity(date)
        prices = self.shadow.prices(date)
        rep = reconcile(self.shadow.positions(), remote_pos, prices, self.shadow.cash(), remote_cash, date,
                        cash_tol=max(1.0, self.cash_tol_frac * remote_equity))
        if not rep.clean and (self.adopt_breaks if adopt is None else adopt):
            adj = self.shadow.adopt(remote_pos, remote_cash, date, note="adopted from venue after reconciliation break")
            log.warning("adopted venue state after break: %s", adj)
            rep = reconcile(self.shadow.positions(), remote_pos, prices, self.shadow.cash(), remote_cash, date,
                            cash_tol=max(1.0, self.cash_tol_frac * remote_equity))
        if rep.clean:
            self.shadow.mark(date, remote_equity)
        self.last_reconcile = rep
        return rep

    # -- Broker protocol used by live.rebalance -------------------------------------- #
    @property
    def initialized(self) -> bool:
        return self.shadow.initialized

    @property
    def as_of(self) -> pd.Timestamp:
        return self.shadow.as_of

    def advance_to(self, date) -> None:
        self.shadow.advance_to(date)

    def positions(self) -> dict[str, float]:
        return self.shadow.positions()

    def cash(self) -> float:
        return self.shadow.cash()

    def equity(self, date=None) -> float:
        return self.shadow.equity(date)

    def prices(self, date=None) -> pd.Series:
        return self.shadow.prices(date)

    def weights(self, date=None) -> pd.Series:
        return self.shadow.weights(date)

    def drawdown(self) -> float:
        return self.shadow.drawdown()

    def holding_views(self, date=None) -> dict[str, HoldingView]:
        return self.shadow.holding_views(date)

    def loss_sale_dates(self) -> dict[str, pd.Timestamp]:
        return self.shadow.loss_sale_dates()

    def equity_curve(self) -> pd.Series:
        return self.shadow.equity_curve()

    def fills(self):
        return self.shadow.fills()

    def realized_costs(self) -> float:
        return self.shadow.realized_costs()

    @property
    def book(self) -> dict | None:
        return self.shadow.book

    def _require(self) -> dict:
        return self.shadow._require()

    def submit(self, orders: list[Order], date, run_id: str | None = None) -> list[Fill]:
        fills = self.remote.submit(orders, date, run_id=run_id or "manual")
        lot_plans = {o.ticker: o.lot_plan for o in orders if o.side == "SELL"}
        self.shadow.apply_fills(fills, date, lot_plans)
        self.sync(date)
        return fills
