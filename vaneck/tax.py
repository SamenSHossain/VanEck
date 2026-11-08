"""Tax-lot ledger (Phase 1.3.2, extended in SPEC section 14).

* Positions are held in *units* (research: total-return units priced at Yahoo
  ``Adj Close``; paper broker: shares).  A reinvested distribution does not
  change the unit count; the reinvested dollars are added to cost basis.
* Lot relief: FIFO, HIFO or TAXOPT (losses first, then long-term gains
  smallest first, then short-term gains smallest first), or an explicit lot plan
  produced by the execution planner.
* Wash sales: a loss on ticker X is disallowed to the extent X (or a member of
  the same substantially-identical group) was bought within ``wash_sale_days``
  before or after; disallowed losses are added to the replacement lot's basis.
* Short- and long-term gains are tracked separately. With a single rate (the
  plan's 37%) the split changes nothing; with ``tax_rate_short`` set, net gains
  of each character are taxed at their own rate after offsetting.
* Taxes for year Y are settled at the first rebalance of year Y+1; net losses
  carry forward indefinitely.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

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
    units: float
    loss_per_unit: float
    long_term: bool


@dataclass
class _Buy:
    date: pd.Timestamp
    ticker: str
    units: float
    lot: Lot


def select_lots(lots: list[Lot], units: float, method: str, price: float, now: pd.Timestamp,
                long_term_days: int = 365) -> list[tuple[int, float]]:
    """Return ``[(lot_index, units)]`` relieving ``units`` by the given method."""
    if units <= 0 or not lots:
        return []
    idx = list(range(len(lots)))
    m = method.upper()
    if m == "FIFO":
        order = sorted(idx, key=lambda i: lots[i].date)
    elif m == "HIFO":
        order = sorted(idx, key=lambda i: -lots[i].basis / lots[i].units)
    elif m == "TAXOPT":
        def key(i):
            lot = lots[i]
            gain_pu = price - lot.basis / lot.units
            lt = (now - lot.date).days > long_term_days
            if gain_pu < 0:
                return (0, gain_pu)            # biggest loss first
            return (1 if lt else 2, gain_pu)   # long-term gains before short-term, smallest first
        order = sorted(idx, key=key)
    else:
        raise ValueError(f"unknown lot method {method}")
    plan, remaining = [], units
    for i in order:
        if remaining <= 1e-12:
            break
        take = min(lots[i].units, remaining)
        plan.append((i, take))
        remaining -= take
    return plan


class TaxLedger:
    def __init__(self, spec: Spec = SPEC, taxed: bool = True):
        self.spec = spec
        self.rate_lt = spec.tax_rate if taxed else 0.0
        self.rate_st = (spec.tax_rate_short if spec.tax_rate_short is not None else spec.tax_rate) if taxed else 0.0
        self.taxed = taxed
        self.tax_dist = spec.tax_distributions and taxed
        self.lots: dict[str, list[Lot]] = defaultdict(list)
        self.realized_st: dict[int, float] = defaultdict(float)
        self.realized_lt: dict[int, float] = defaultdict(float)
        self.dist_income_by_year: dict[int, float] = defaultdict(float)
        self.carryforward = 0.0
        self.settled_through: int | None = None
        self.group_of: dict[str, str] = {}
        for grp in spec.wash_sale_groups:
            for t in grp:
                self.group_of[t] = "+".join(grp)
        self._buys: dict[str, list[_Buy]] = defaultdict(list)
        self._loss_sales: dict[str, list[_LossSale]] = defaultdict(list)
        self.last_buy: dict[str, pd.Timestamp] = {}
        self.last_loss_sale: dict[str, pd.Timestamp] = {}
        self.log: list[dict] = []

    # -- compatibility view -------------------------------------------------- #
    @property
    def realized_by_year(self) -> dict[int, float]:
        years = set(self.realized_st) | set(self.realized_lt)
        return {y: self.realized_st.get(y, 0.0) + self.realized_lt.get(y, 0.0) for y in sorted(years)}

    @property
    def rate(self) -> float:
        return self.rate_lt

    # -- helpers ------------------------------------------------------------- #
    def _grp(self, ticker: str) -> str:
        return self.group_of.get(ticker, ticker)

    def units(self, ticker: str) -> float:
        return sum(lot.units for lot in self.lots[ticker])

    def basis(self, ticker: str) -> float:
        return sum(lot.basis for lot in self.lots[ticker])

    def _is_long_term(self, lot_date: pd.Timestamp, date: pd.Timestamp) -> bool:
        return (date - lot_date).days > self.spec.long_term_days

    def _prune(self, date: pd.Timestamp) -> None:
        cutoff = date - pd.Timedelta(days=self.spec.wash_sale_days)
        for g in list(self._buys):
            self._buys[g] = [b for b in self._buys[g] if b.date >= cutoff and b.units > 1e-12]
        for g in list(self._loss_sales):
            self._loss_sales[g] = [s for s in self._loss_sales[g] if s.date >= cutoff and s.units > 1e-12]

    def _add_realized(self, year: int, amount: float, long_term: bool) -> None:
        (self.realized_lt if long_term else self.realized_st)[year] += amount

    # -- events -------------------------------------------------------------- #
    def buy(self, ticker: str, date: pd.Timestamp, units: float, dollars: float) -> None:
        if units <= 0:
            return
        self._prune(date)
        lot = Lot(date=date, units=units, basis=dollars)
        self.lots[ticker].append(lot)
        self.last_buy[ticker] = date
        g = self._grp(ticker)
        remaining, disallowed_total = units, 0.0
        for s in self._loss_sales[g]:             # purchase within 30 days *after* a loss sale
            if remaining <= 1e-12:
                break
            if s.date >= date:
                continue
            matched = min(remaining, s.units)
            disallowed = matched * s.loss_per_unit
            s.units -= matched
            remaining -= matched
            disallowed_total += disallowed
            self._add_realized(s.date.year, disallowed, s.long_term)   # reverse the deduction
        lot.basis += disallowed_total
        self._buys[g].append(_Buy(date=date, ticker=ticker, units=remaining, lot=lot))
        if disallowed_total:
            self.log.append({"date": date, "ticker": ticker, "event": "wash_after", "disallowed": disallowed_total})

    def sell(self, ticker: str, date: pd.Timestamp, units: float, proceeds: float,
             lot_plan: list[tuple[int, float]] | None = None) -> float:
        """Sell ``units`` for ``proceeds``; return the realized gain after wash rules."""
        if units <= 0:
            return 0.0
        self._prune(date)
        lots = self.lots[ticker]
        held = sum(lot.units for lot in lots)
        if units > held + 1e-6:
            raise ValueError(f"selling {units} > held {held} of {ticker}")
        units = min(units, held)
        if units <= 1e-12:
            return 0.0
        price = proceeds / units
        if lot_plan is None:
            lot_plan = select_lots(lots, units, self.spec.lot_method, price, date, self.spec.long_term_days)
        gain_st = gain_lt = 0.0
        units_st = units_lt = 0.0
        for i, take in lot_plan:
            lot = lots[i]
            take = min(take, lot.units)
            frac = take / lot.units
            basis_part = lot.basis * frac
            lot_gain = take * price - basis_part
            if self._is_long_term(lot.date, date):
                gain_lt += lot_gain
                units_lt += take
            else:
                gain_st += lot_gain
                units_st += take
            lot.basis -= basis_part
            lot.units -= take
        self.lots[ticker] = [lot for lot in lots if lot.units > 1e-12]
        gain = gain_st + gain_lt
        disallowed = 0.0
        if gain < 0:
            self.last_loss_sale[ticker] = date
            loss_per_unit = -gain / units
            lt_flag = units_lt >= units_st
            grp = self._grp(ticker)
            to_match = units
            for b in self._buys[grp]:             # replacement shares bought within 30 days *before* the sale
                if to_match <= 1e-12:
                    break
                if b.date >= date or b.units <= 1e-12:
                    continue
                # only shares from that purchase that are *still held* are replacement shares
                # (the shares being sold, and shares already sold earlier, do not count; IRS Pub. 550)
                available = min(b.units, b.lot.units)
                if available <= 1e-12:
                    continue
                matched = min(to_match, available)
                b.units -= matched
                to_match -= matched
                disallowed += matched * loss_per_unit
                b.lot.basis += matched * loss_per_unit
            if to_match > 1e-12:
                self._loss_sales[grp].append(_LossSale(date=date, ticker=ticker, units=to_match,
                                                       loss_per_unit=loss_per_unit, long_term=lt_flag))
            # attribute the disallowance to the dominant character
            if lt_flag:
                gain_lt += disallowed
            else:
                gain_st += disallowed
        self.realized_st[date.year] += gain_st
        self.realized_lt[date.year] += gain_lt
        realized = gain + disallowed
        self.log.append({"date": date, "ticker": ticker, "event": "sell", "proceeds": proceeds,
                         "basis": proceeds - gain, "gain": gain, "disallowed": disallowed, "realized": realized,
                         "short_term": gain_st, "long_term": gain_lt})
        return realized

    def distribution(self, ticker: str, date: pd.Timestamp, dollars: float) -> None:
        """Reinvested distribution: raise basis pro rata and accrue distribution income."""
        if dollars <= 0:
            return
        lots = self.lots[ticker]
        total = sum(lot.units for lot in lots)
        if total <= 0:
            return
        for lot in lots:
            lot.basis += dollars * (lot.units / total)
        if self.tax_dist:
            self.dist_income_by_year[date.year] += dollars

    def transfer_out(self, ticker: str, units: float) -> None:
        """Remove units pro rata across lots without realising anything (position break adopted from the broker)."""
        lots = self.lots[ticker]
        total = sum(lot.units for lot in lots)
        if total <= 0 or units <= 0:
            return
        frac = min(units / total, 1.0)
        for lot in lots:
            lot.basis *= 1.0 - frac
            lot.units *= 1.0 - frac
        self.lots[ticker] = [lot for lot in lots if lot.units > 1e-12]

    def income(self, date: pd.Timestamp, dollars: float) -> None:
        """Cash distribution (paper broker): taxable income, no basis change."""
        if dollars > 0 and self.tax_dist:
            self.dist_income_by_year[date.year] += dollars

    # -- views for the execution planner ------------------------------------- #
    def lot_views(self, ticker: str) -> list[tuple[int, float, float, pd.Timestamp]]:
        return [(i, lot.units, lot.basis, lot.date) for i, lot in enumerate(self.lots[ticker]) if lot.units > 1e-12]

    # -- liabilities --------------------------------------------------------- #
    def _open_years(self, through: int | None = None) -> list[int]:
        years = set(self.realized_st) | set(self.realized_lt) | set(self.dist_income_by_year)
        if self.settled_through is not None:
            years = {y for y in years if y > self.settled_through}
        if through is not None:
            years = {y for y in years if y <= through}
        return sorted(years)

    def _year_tax(self, year: int, carry: float) -> tuple[float, float]:
        """Tax due for ``year`` given incoming carryforward; returns (tax, new carryforward)."""
        st = self.realized_st.get(year, 0.0)
        lt = self.realized_lt.get(year, 0.0)
        # carried losses first offset short-term gains (higher rate), then long-term
        if carry < 0:
            use = min(-carry, max(st, 0.0))
            st -= use
            carry += use
        if carry < 0:
            use = min(-carry, max(lt, 0.0))
            lt -= use
            carry += use
        if st < 0 < lt:
            off = min(-st, lt)
            lt -= off
            st += off
        elif lt < 0 < st:
            off = min(-lt, st)
            st -= off
            lt += off
        tax = self.rate_st * max(st, 0.0) + self.rate_lt * max(lt, 0.0)
        tax += self.rate_lt * self.dist_income_by_year.get(year, 0.0)
        new_carry = carry + min(st, 0.0) + min(lt, 0.0)
        return tax, new_carry

    def accrued_liability(self) -> float:
        if not self.taxed:
            return 0.0
        carry, tax = self.carryforward, 0.0
        for y in self._open_years():
            t, carry = self._year_tax(y, carry)
            tax += t
        return tax

    def settle(self, date: pd.Timestamp) -> float:
        """Settle all tax years strictly before ``date.year``; return the cash due."""
        if not self.taxed:
            self.settled_through = date.year - 1
            return 0.0
        tax = 0.0
        for y in self._open_years(through=date.year - 1):
            t, self.carryforward = self._year_tax(y, self.carryforward)
            tax += t
        self.settled_through = date.year - 1
        if tax:
            self.log.append({"date": date, "ticker": "", "event": "settle", "tax": tax})
        return tax

    # -- persistence (paper broker) ------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "taxed": self.taxed,
            "lots": {t: [{"date": str(lot.date.date()), "units": lot.units, "basis": lot.basis} for lot in lots]
                     for t, lots in self.lots.items() if lots},
            "realized_st": dict(self.realized_st), "realized_lt": dict(self.realized_lt),
            "dist_income_by_year": dict(self.dist_income_by_year),
            "carryforward": self.carryforward, "settled_through": self.settled_through,
            "last_buy": {t: str(d.date()) for t, d in self.last_buy.items()},
            "last_loss_sale": {t: str(d.date()) for t, d in self.last_loss_sale.items()},
            "buys": [{"date": str(b.date.date()), "ticker": b.ticker, "units": b.units,
                      "lot_date": str(b.lot.date.date()), "lot_basis": b.lot.basis} for g in self._buys.values() for b in g],
            "loss_sales": [{"date": str(s.date.date()), "ticker": s.ticker, "units": s.units,
                            "loss_per_unit": s.loss_per_unit, "long_term": s.long_term}
                           for g in self._loss_sales.values() for s in g],
        }

    @classmethod
    def from_dict(cls, d: dict, spec: Spec = SPEC) -> "TaxLedger":
        led = cls(spec, taxed=d.get("taxed", True))
        for t, lots in d.get("lots", {}).items():
            led.lots[t] = [Lot(pd.Timestamp(x["date"]), float(x["units"]), float(x["basis"])) for x in lots]
        led.realized_st.update({int(k): float(v) for k, v in d.get("realized_st", {}).items()})
        led.realized_lt.update({int(k): float(v) for k, v in d.get("realized_lt", {}).items()})
        led.dist_income_by_year.update({int(k): float(v) for k, v in d.get("dist_income_by_year", {}).items()})
        led.carryforward = float(d.get("carryforward", 0.0))
        led.settled_through = d.get("settled_through")
        led.last_buy = {t: pd.Timestamp(v) for t, v in d.get("last_buy", {}).items()}
        led.last_loss_sale = {t: pd.Timestamp(v) for t, v in d.get("last_loss_sale", {}).items()}
        for b in d.get("buys", []):
            # re-attach to the matching lot when it still exists
            lot = next((lot for lot in led.lots.get(b["ticker"], []) if str(lot.date.date()) == b["lot_date"]), None)
            if lot is not None:
                led._buys[led._grp(b["ticker"])].append(_Buy(pd.Timestamp(b["date"]), b["ticker"], float(b["units"]), lot))
        for s in d.get("loss_sales", []):
            led._loss_sales[led._grp(s["ticker"])].append(
                _LossSale(pd.Timestamp(s["date"]), s["ticker"], float(s["units"]), float(s["loss_per_unit"]),
                          bool(s["long_term"])))
        return led
