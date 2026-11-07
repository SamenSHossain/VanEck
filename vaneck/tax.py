"""Tax-lot ledger (Phase 1.3.2).

* Positions are held in *total-return units* (price = Yahoo ``Adj Close``), so a
  distribution does not change the unit count; instead the reinvested dollars
  are added to cost basis (and taxed as a distribution if enabled).
* Realized gains use FIFO (or HIFO) lot relief at a single flat rate.
* Wash sales: a loss realized on ticker X is disallowed to the extent X (or a
  fund in the same substantially-identical group) was bought within
  ``wash_sale_days`` before or after the sale; disallowed losses are added to
  the replacement lot's basis.
* Taxes for year Y are settled at the first rebalance of year Y+1; net losses
  carry forward indefinitely.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from .config import SPEC, Spec


@dataclass
class Lot:
    date: pd.Timestamp
    units: float
    basis: float  # dollars for the whole lot


@dataclass
class _LossSale:
    date: pd.Timestamp
    ticker: str
    units: float          # units still eligible to be matched with replacement buys
    loss_per_unit: float  # positive number (loss magnitude per unit)


@dataclass
class _Buy:
    date: pd.Timestamp
    ticker: str
    units: float          # units not yet matched to a loss sale
    lot: Lot


class TaxLedger:
    def __init__(self, spec: Spec = SPEC, taxed: bool = True):
        self.spec = spec
        self.rate = spec.tax_rate if taxed else 0.0
        self.tax_dist = spec.tax_distributions and taxed
        self.lots: dict[str, list[Lot]] = defaultdict(list)
        self.realized_by_year: dict[int, float] = defaultdict(float)
        self.dist_income_by_year: dict[int, float] = defaultdict(float)
        self.carryforward = 0.0  # <= 0, unused net losses
        self.settled_through: int | None = None
        self.group_of: dict[str, str] = {}
        for grp in spec.wash_sale_groups:
            for t in grp:
                self.group_of[t] = "+".join(grp)
        self._buys: dict[str, list[_Buy]] = defaultdict(list)
        self._loss_sales: dict[str, list[_LossSale]] = defaultdict(list)
        self.log: list[dict] = []

    # -- helpers ----------------------------------------------------------- #
    def _grp(self, ticker: str) -> str:
        return self.group_of.get(ticker, ticker)

    def units(self, ticker: str) -> float:
        return sum(l.units for l in self.lots[ticker])

    def basis(self, ticker: str) -> float:
        return sum(l.basis for l in self.lots[ticker])

    def _prune(self, date: pd.Timestamp) -> None:
        cutoff = date - pd.Timedelta(days=self.spec.wash_sale_days)
        for g in list(self._buys):
            self._buys[g] = [b for b in self._buys[g] if b.date >= cutoff and b.units > 1e-12]
        for g in list(self._loss_sales):
            self._loss_sales[g] = [s for s in self._loss_sales[g] if s.date >= cutoff and s.units > 1e-12]

    # -- events ------------------------------------------------------------ #
    def buy(self, ticker: str, date: pd.Timestamp, units: float, dollars: float) -> None:
        if units <= 0:
            return
        self._prune(date)
        lot = Lot(date=date, units=units, basis=dollars)
        self.lots[ticker].append(lot)
        g = self._grp(ticker)
        # A purchase within 30 days *after* a loss sale washes that loss.
        remaining = units
        disallowed_total = 0.0
        for s in self._loss_sales[g]:
            if remaining <= 1e-12:
                break
            if s.date >= date:
                continue
            matched = min(remaining, s.units)
            disallowed = matched * s.loss_per_unit
            s.units -= matched
            remaining -= matched
            disallowed_total += disallowed
            # reverse the deduction (loss was recorded as negative realized gain)
            self.realized_by_year[s.date.year] += disallowed
        lot.basis += disallowed_total
        self._buys[g].append(_Buy(date=date, ticker=ticker, units=remaining, lot=lot))
        if disallowed_total:
            self.log.append({"date": date, "ticker": ticker, "event": "wash_after",
                             "disallowed": disallowed_total})

    def sell(self, ticker: str, date: pd.Timestamp, units: float, proceeds: float) -> float:
        """Sell ``units`` for ``proceeds`` dollars; return the realized gain (after wash rules)."""
        if units <= 0:
            return 0.0
        self._prune(date)
        lots = self.lots[ticker]
        held = sum(l.units for l in lots)
        if units > held + 1e-9:
            raise ValueError(f"selling {units} > held {held} of {ticker}")
        units = min(units, held)
        order = sorted(range(len(lots)), key=(lambda i: -lots[i].basis / lots[i].units)) \
            if self.spec.lot_method.upper() == "HIFO" else list(range(len(lots)))
        remaining = units
        basis_sold = 0.0
        for i in order:
            lot = lots[i]
            if remaining <= 1e-12:
                break
            take = min(lot.units, remaining)
            frac = take / lot.units
            basis_sold += lot.basis * frac
            lot.basis -= lot.basis * frac
            lot.units -= take
            remaining -= take
        self.lots[ticker] = [l for l in lots if l.units > 1e-12]
        gain = proceeds - basis_sold
        disallowed = 0.0
        if gain < 0:
            loss_per_unit = -gain / units
            g = self._grp(ticker)
            # Replacement shares bought within 30 days *before* the sale.
            to_match = units
            for b in self._buys[g]:
                if to_match <= 1e-12:
                    break
                if b.date >= date or b.units <= 1e-12:
                    continue
                matched = min(to_match, b.units)
                b.units -= matched
                to_match -= matched
                disallowed += matched * loss_per_unit
                b.lot.basis += matched * loss_per_unit  # defer into the replacement lot
            if to_match > 1e-12:
                self._loss_sales[g].append(_LossSale(date=date, ticker=ticker, units=to_match,
                                                     loss_per_unit=loss_per_unit))
        realized = gain + disallowed
        self.realized_by_year[date.year] += realized
        self.log.append({"date": date, "ticker": ticker, "event": "sell", "proceeds": proceeds,
                         "basis": basis_sold, "gain": gain, "disallowed": disallowed, "realized": realized})
        return realized

    def distribution(self, ticker: str, date: pd.Timestamp, dollars: float) -> None:
        """Reinvested distribution: raise basis pro-rata and accrue distribution income."""
        if dollars <= 0:
            return
        lots = self.lots[ticker]
        total = sum(l.units for l in lots)
        if total <= 0:
            return
        for l in lots:
            l.basis += dollars * (l.units / total)
        if self.tax_dist:
            self.dist_income_by_year[date.year] += dollars

    # -- liabilities ------------------------------------------------------- #
    def _open_years(self, through: int | None = None) -> list[int]:
        years = set(self.realized_by_year) | set(self.dist_income_by_year)
        if self.settled_through is not None:
            years = {y for y in years if y > self.settled_through}
        if through is not None:
            years = {y for y in years if y <= through}
        return sorted(years)

    def accrued_liability(self) -> float:
        """Tax owed on everything not yet settled, given current carryforward."""
        if self.rate == 0.0:
            return 0.0
        cf = self.carryforward
        tax = 0.0
        for y in self._open_years():
            net = self.realized_by_year.get(y, 0.0) + cf
            if net > 0:
                tax += self.rate * net
                cf = 0.0
            else:
                cf = net
            tax += self.rate * self.dist_income_by_year.get(y, 0.0)
        return tax

    def settle(self, date: pd.Timestamp) -> float:
        """Settle all tax years strictly before ``date.year``; return the cash due."""
        if self.rate == 0.0:
            self.settled_through = date.year - 1
            return 0.0
        tax = 0.0
        for y in self._open_years(through=date.year - 1):
            net = self.realized_by_year.get(y, 0.0) + self.carryforward
            if net > 0:
                tax += self.rate * net
                self.carryforward = 0.0
            else:
                self.carryforward = net
            tax += self.rate * self.dist_income_by_year.get(y, 0.0)
        self.settled_through = date.year - 1
        if tax:
            self.log.append({"date": date, "ticker": "", "event": "settle", "tax": tax})
        return tax
