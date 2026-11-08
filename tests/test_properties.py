"""Property-based invariants for the tax ledger and the execution planner."""
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vaneck.config import SPEC
from vaneck.execution import ExecutionPolicy, HoldingView, plan
from vaneck.tax import TaxLedger

OPS = st.lists(st.tuples(st.sampled_from(["buy", "sell", "dist"]), st.floats(1, 5000), st.floats(5, 300),
                         st.sampled_from(["X", "Y"])), min_size=1, max_size=40)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(ops=OPS)
def test_ledger_cash_flow_identity_and_nonnegativity(ops):
    led = TaxLedger(SPEC, taxed=True)
    date = pd.Timestamp("2020-01-02")
    bought = sold = dists = 0.0
    for kind, amount, price, t in ops:
        date += pd.Timedelta(days=7)
        held = led.units(t)
        if kind == "buy":
            led.buy(t, date, amount / price, amount)
            bought += amount
        elif kind == "sell" and held > 0:
            units = min(held, amount / price)
            proceeds = units * price
            led.sell(t, date, units, proceeds)
            sold += proceeds
        elif kind == "dist" and held > 0:
            led.distribution(t, date, amount)
            dists += amount
        for lots in led.lots.values():
            assert all(lot.units >= -1e-9 and lot.basis >= -1e-6 for lot in lots)
    remaining_basis = sum(led.basis(t) for t in ("X", "Y"))
    realized = sum(led.realized_by_year.values())
    scale = max(1.0, bought + dists + sold)
    assert bought + dists - sold == pytest.approx(remaining_basis - realized, abs=1e-6 * scale)
    assert led.accrued_liability() >= 0.0


WEIGHTS = st.lists(st.floats(0, 0.3), min_size=3, max_size=3)
HOLD = st.lists(st.floats(0, 5000), min_size=3, max_size=3)


@settings(max_examples=200, deadline=None)
@given(w=WEIGHTS, h=HOLD, cash=st.floats(0, 20000), band=st.sampled_from([None, 0.2]), harvest=st.booleans())
def test_planner_never_borrows_or_oversells(w, h, cash, band, harvest):
    tickers = ["A", "B", "C"]
    prices = pd.Series({"A": 100.0, "B": 50.0, "C": 20.0, "S": 10.0})
    targets = pd.Series(dict(zip(tickers, w)))
    date = pd.Timestamp("2024-06-03")
    holdings = {t: HoldingView(t, float(prices[t]), [(0, v / prices[t], v * 1.2, pd.Timestamp("2023-01-03"))],
                               last_buy=pd.Timestamp("2023-01-03")) for t, v in zip(tickers, h) if v > 0}
    pol = ExecutionPolicy(band=band, harvest=harvest, substitutes=(("A", "S"),) if harvest else ())
    trades = plan(date, targets, holdings, cash, prices, pol)
    buys = sum(t.dollars for t in trades if t.dollars > 0)
    sells = -sum(t.dollars for t in trades if t.dollars < 0)
    assert buys <= cash + sells + 1e-6
    for t in trades:
        if t.dollars < 0:
            held_value = holdings[t.ticker].value if t.ticker in holdings else 0.0
            assert -t.dollars <= held_value + 1e-6
            if t.lot_plan:
                assert sum(u for _, u in t.lot_plan) <= holdings[t.ticker].units + 1e-9
        else:
            assert t.ticker in prices.index
    assert len({t.ticker for t in trades}) == len(trades)   # one trade per ticker
