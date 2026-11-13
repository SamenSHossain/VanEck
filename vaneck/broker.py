"""Execution layer: a ``Broker`` protocol and a paper broker with a share-based book.

The paper book is deliberately *not* the research simulator: it holds whole
shares and cash, receives dividends as cash on the ex-date, applies splits, and
fills at the close of the execution date with the cost model's slippage. The
monitor reconciles it against the research engine; the two agreeing is an
engineering check, not a given.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, cast

import numpy as np
import pandas as pd

from .config import SPEC, Spec
from .costs import CostModel
from .data import MarketData
from .execution import HoldingView
from .risk import Order
from .tax import TaxLedger


@dataclass
class Fill:
    date: str
    ticker: str
    side: str
    shares: float
    price: float
    notional: float
    cost: float


class Broker(Protocol):
    def positions(self) -> dict[str, float]: ...
    def cash(self) -> float: ...
    def equity(self, date=None) -> float: ...
    def submit(self, orders: list[Order], date) -> list[Fill]: ...


class PaperBroker:
    def __init__(self, path: Path, md: MarketData, cost_model: CostModel | None = None, spec: Spec = SPEC):
        self.path = Path(path)
        self.md = md
        self.spec = spec
        self.cost_model = cost_model or CostModel(spec)
        self.book: dict | None = json.loads(self.path.read_text()) if self.path.exists() else None
        book = self.book or {}
        self.ledger = TaxLedger.from_dict(book["tax"], spec) if "tax" in book else TaxLedger(spec, taxed=True)
        self._adv: pd.DataFrame | None = None

    # -- lifecycle ------------------------------------------------------------ #
    @property
    def initialized(self) -> bool:
        return self.book is not None

    def init(self, cash: float, as_of, name: str = "paper", data_vintage: str | None = None) -> None:
        as_of = pd.Timestamp(as_of)
        if as_of not in self.md.index:
            raise ValueError(f"{as_of.date()} is not a trading day in the data")
        self.book = {
            "name": name, "created": str(as_of.date()), "as_of": str(as_of.date()),
            "initial_cash": float(cash), "cash": float(cash), "positions": {},
            "fills": [], "cash_flows": [], "marks": {str(as_of.date()): float(cash)},
            "peak_equity": float(cash),
            "data_vintage": data_vintage or str(self.md.index[-1].date()),
        }
        self.ledger = TaxLedger(self.spec, taxed=True)
        self.save()

    def save(self) -> None:
        b = self._require()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        b["tax"] = self.ledger.to_dict()
        b["realized_by_year"] = {str(k): v for k, v in self.ledger.realized_by_year.items()}
        b["tax_liability_accrued"] = self.ledger.accrued_liability()
        self.path.write_text(json.dumps(b, indent=2, default=str))

    def holding_views(self, date=None) -> dict[str, HoldingView]:
        px = self.prices(date)
        return {t: HoldingView(t, float(px[t]), self.ledger.lot_views(t), self.ledger.last_buy.get(t),
                               self.ledger.last_loss_sale.get(t))
                for t, sh in self.positions().items() if sh > 0}

    def loss_sale_dates(self) -> dict[str, pd.Timestamp]:
        """Latest loss-sale date of every fund, including funds no longer held (the planner's no-rebuy window)."""
        return dict(self.ledger.last_loss_sale)

    def _require(self) -> dict:
        if self.book is None:
            raise RuntimeError(f"paper book {self.path} not initialised (run `paper init`)")
        return self.book

    # -- state ------------------------------------------------------------------ #
    def positions(self) -> dict[str, float]:
        return {k: float(v) for k, v in self._require()["positions"].items() if abs(v) > 1e-12}

    def cash(self) -> float:
        return float(self._require()["cash"])

    @property
    def as_of(self) -> pd.Timestamp:
        return pd.Timestamp(self._require()["as_of"])

    def prices(self, date=None) -> pd.Series:
        date = self.as_of if date is None else pd.Timestamp(date)
        return self.md.close.loc[date]

    def equity(self, date=None) -> float:
        px = self.prices(date)
        return self.cash() + sum(sh * float(px[t]) for t, sh in self.positions().items())

    def weights(self, date=None) -> pd.Series:
        px = self.prices(date)
        eq = self.equity(date)
        w = pd.Series({t: sh * float(px[t]) / eq for t, sh in self.positions().items()}, dtype=float)
        w["CASH"] = self.cash() / eq
        return w

    def equity_curve(self) -> pd.Series:
        marks = self._require()["marks"]
        s = pd.Series({pd.Timestamp(k): float(v) for k, v in marks.items()}).sort_index()
        return s.rename("paper_equity")

    def drawdown(self) -> float:
        b = self._require()
        return self.equity() / float(b["peak_equity"]) - 1.0 if b["peak_equity"] > 0 else 0.0

    def fills(self) -> pd.DataFrame:
        f = self._require()["fills"]
        return pd.DataFrame(f) if f else pd.DataFrame(columns=[x for x in Fill.__dataclass_fields__])

    def realized_costs(self) -> float:
        return float(sum(f["cost"] for f in self._require()["fills"]))

    # -- time ------------------------------------------------------------------- #
    def advance_to(self, date) -> None:
        """Roll the book through cash yield, dividends and splits up to ``date`` inclusive."""
        b = self._require()
        date = pd.Timestamp(date)
        start = self.as_of
        if date <= start:
            return
        if date > pd.Timestamp(self.spec.study_end) and any(abs(float(s)) > 1e-9 for s in b["positions"].values()):
            # imported here: holdout depends on the study modules
            from .holdout import HoldoutSealed, is_sealed
            if is_sealed():
                raise HoldoutSealed(
                    f"this book holds positions, and valuing them after {self.spec.study_end} would measure performance "
                    "inside the sealed hold-out (SPEC section 19). For a dry run on newer bars use a fresh book "
                    "(--init-cash with --book and --decisions in a new folder); open the hold-out first for anything else.")
        days = self.md.index[(self.md.index > start) & (self.md.index <= date)]
        y = self.md.cash_yield(self.spec)
        vintage = pd.Timestamp(b["data_vintage"])
        pos = b["positions"]
        for d in days:
            prev = self.md.index[cast(int, self.md.index.get_loc(d)) - 1]
            b["cash"] *= 1.0 + float(cast(float, y.loc[prev])) / 252.0
            for t in list(pos):
                sh = pos[t]
                if sh == 0:
                    continue
                div = float(cast(float, self.md.distributions.at[d, t])) if t in self.md.distributions.columns else 0.0
                if div > 0:
                    amt = sh * div
                    b["cash"] += amt
                    self.ledger.income(d, amt)
                    b["cash_flows"].append({"date": str(d.date()), "ticker": t, "type": "dividend", "amount": amt})
                if self.md.splits is not None and d > vintage:
                    r = float(cast(float, self.md.splits.at[d, t])) if t in self.md.splits.columns else 0.0
                    if r > 0:
                        pos[t] = sh * r
                        for lot in self.ledger.lots[t]:
                            lot.units *= r
                        b["cash_flows"].append({"date": str(d.date()), "ticker": t, "type": "split", "ratio": r})
            b["as_of"] = str(d.date())
            eq = self.equity(d)
            b["marks"][str(d.date())] = eq
            b["peak_equity"] = max(float(b["peak_equity"]), eq)
        self.save()

    # -- shadow-ledger operations (used when a remote venue executes) ------------ #
    def apply_fills(self, fills: list[Fill], date, lot_plans: dict[str, list[tuple[int, float]] | None] | None = None) -> None:
        """Record fills executed elsewhere: positions, cash, lots. Costs are whatever the venue reported."""
        b = self._require()
        date = pd.Timestamp(date)
        pos = b["positions"]
        lot_plans = lot_plans or {}
        for f in fills:
            if f.side == "BUY":
                b["cash"] -= f.notional + f.cost
                pos[f.ticker] = pos.get(f.ticker, 0.0) + f.shares
                self.ledger.buy(f.ticker, date, f.shares, f.notional)
            else:
                held = pos.get(f.ticker, 0.0)
                shares = min(f.shares, held)
                b["cash"] += f.notional - f.cost
                pos[f.ticker] = held - shares
                plan = lot_plans.get(f.ticker)
                if plan is not None:
                    from .risk import truncate_lot_plan
                    plan = truncate_lot_plan(plan, shares)
                self.ledger.sell(f.ticker, date, shares, f.notional * (shares / f.shares if f.shares else 1.0), lot_plan=plan)
                if abs(pos[f.ticker]) < 1e-9:
                    del pos[f.ticker]
            b["fills"].append(asdict(f))
        if date > self.as_of:
            b["as_of"] = str(date.date())
        self.save()

    def mark(self, date, equity: float) -> None:
        """Mark the book with an externally observed equity (the venue's account value)."""
        b = self._require()
        date = pd.Timestamp(date)
        b["marks"][str(date.date())] = float(equity)
        b["peak_equity"] = max(float(b["peak_equity"]), float(equity))
        if date > self.as_of:
            b["as_of"] = str(date.date())
        self.save()

    def adopt(self, positions: dict[str, float], cash: float, date, note: str = "") -> dict:
        """Align the shadow book with the venue, treating differences as transfers (nothing realised)."""
        b = self._require()
        date = pd.Timestamp(date)
        px = self.prices(date)
        pos = b["positions"]
        adjustments = {}
        for sym in sorted(set(pos) | set(positions)):
            want, have = float(positions.get(sym, 0.0)), float(pos.get(sym, 0.0))
            diff = want - have
            if abs(diff) < 1e-9:
                continue
            price = float(px.get(sym, float("nan")))
            if diff > 0:
                self.ledger.buy(sym, date, diff, diff * (price if price == price else 0.0))
            else:
                self.ledger.transfer_out(sym, -diff)
            if abs(want) < 1e-9:
                pos.pop(sym, None)
            else:
                pos[sym] = want
            adjustments[sym] = diff
        cash_diff = float(cash) - float(b["cash"])
        b["cash"] = float(cash)
        b["cash_flows"].append({"date": str(date.date()), "ticker": "", "type": "adjustment", "amount": cash_diff,
                                "positions": adjustments, "note": note})
        self.save()
        return {"positions": adjustments, "cash": cash_diff}

    # -- execution ------------------------------------------------------------- #
    def submit(self, orders: list[Order], date, run_id: str | None = None) -> list[Fill]:
        """Fill ``orders`` at the close of ``date`` (a trading day in the data)."""
        b = self._require()
        date = pd.Timestamp(date)
        if date not in self.md.index:
            raise ValueError(f"no bar for execution date {date.date()}")
        if date < self.as_of:
            raise ValueError("cannot execute before the book's as_of date")
        self.advance_to(date)
        adv_table = self._adv if self._adv is not None else self.cost_model.adv_table(self.md)
        self._adv = adv_table
        adv_row = adv_table.loc[date]
        px = self.md.close.loc[date]
        fills: list[Fill] = []
        pos = b["positions"]
        for o in sorted(orders, key=lambda o: o.side != "SELL"):
            price = float(px[o.ticker])
            if not np.isfinite(price) or price <= 0:
                raise ValueError(f"no price for {o.ticker} on {date.date()}")
            shares = o.shares
            if o.side == "SELL":
                shares = min(shares, pos.get(o.ticker, 0.0))
                if shares <= 0:
                    continue
            notional = shares * price
            cost = self.cost_model.trade_cost(o.ticker, notional, float(adv_row.get(o.ticker, np.nan)))
            if o.side == "BUY":
                b["cash"] -= notional + cost
                pos[o.ticker] = pos.get(o.ticker, 0.0) + shares
                self.ledger.buy(o.ticker, date, shares, notional)
            else:
                b["cash"] += notional - cost
                pos[o.ticker] = pos.get(o.ticker, 0.0) - shares
                self.ledger.sell(o.ticker, date, shares, notional, lot_plan=o.lot_plan)
                if abs(pos[o.ticker]) < 1e-9:
                    del pos[o.ticker]
            f = Fill(date=str(date.date()), ticker=o.ticker, side=o.side, shares=shares, price=price,
                     notional=notional, cost=cost)
            fills.append(f)
            b["fills"].append(asdict(f))
        eq = self.equity(date)
        b["marks"][str(date.date())] = eq
        b["peak_equity"] = max(float(b["peak_equity"]), eq)
        self.save()
        return fills
