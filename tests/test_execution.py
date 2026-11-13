import pandas as pd
import pytest

from vaneck import portfolio
from vaneck.config import Spec
from vaneck.costs import CostModel
from vaneck.data import MarketData
from vaneck.execution import ExecutionPolicy, HoldingView, plan
from vaneck.tax import Lot, select_lots

D = pd.Timestamp("2024-06-03")
OLD, NEW = pd.Timestamp("2022-01-03"), pd.Timestamp("2024-05-01")


def _by(trades):
    return {t.ticker: t for t in trades}


def test_select_lots_orderings():
    lots = [Lot(OLD, 10, 1500.0),   # $150/u -> loss at price 100
            Lot(OLD, 10, 900.0),    # $90/u  -> small long-term gain
            Lot(NEW, 10, 500.0),    # $50/u  -> big short-term gain
            Lot(NEW, 10, 950.0)]    # $95/u  -> small short-term gain
    taxopt = [i for i, _ in select_lots(lots, 40, "TAXOPT", 100.0, D)]
    assert taxopt == [0, 1, 3, 2]
    hifo = [i for i, _ in select_lots(lots, 40, "HIFO", 100.0, D)]
    assert hifo == [0, 3, 1, 2]
    fifo = select_lots(lots, 15, "FIFO", 100.0, D)
    assert fifo == [(0, 10), (1, 5)]


def test_full_rebalance_matches_delta_to_target_and_sells_first():
    prices = pd.Series({"X": 100.0, "Y": 10.0})
    hold = {"X": HoldingView("X", 100.0, [(0, 80, 6000.0, OLD)]), "Y": HoldingView("Y", 10.0, [(0, 100, 1000.0, OLD)])}
    targets = pd.Series({"X": 0.5, "Y": 0.5})
    trades = plan(D, targets, hold, cash=1000.0, prices=prices, policy=ExecutionPolicy())
    equity = 8000 + 1000 + 1000
    t = _by(trades)
    assert t["X"].dollars == pytest.approx(0.5 * equity - 8000)
    assert t["Y"].dollars == pytest.approx(0.5 * equity - 1000)
    assert trades[0].dollars < 0 < trades[-1].dollars
    assert t["X"].lot_plan == [(0, pytest.approx(3000 / 100.0))]


def test_band_skips_in_band_positions_and_never_borrows():
    prices = pd.Series({"X": 100.0, "Y": 10.0})
    hold = {"X": HoldingView("X", 100.0, [(0, 55, 5500.0, OLD)]), "Y": HoldingView("Y", 10.0, [(0, 300, 3000.0, OLD)])}
    pol = ExecutionPolicy(band=0.20)
    trades = plan(D, pd.Series({"X": 0.5, "Y": 0.5}), hold, cash=1500.0, prices=prices, policy=pol)
    t = _by(trades)
    assert "X" not in t                      # 55% is inside [40%, 60%]
    assert t["Y"].dollars == pytest.approx(1500.0)   # wanted 2000, scaled to available cash


def test_band_deploys_excess_cash_to_most_underweight_group():
    prices = pd.Series({"X": 100.0, "Y": 10.0})
    hold = {"X": HoldingView("X", 100.0, [(0, 45, 4500.0, OLD)]), "Y": HoldingView("Y", 10.0, [(0, 350, 3500.0, OLD)])}
    pol = ExecutionPolicy(band=0.20, cash_deploy_threshold=0.01)
    trades = plan(D, pd.Series({"X": 0.5, "Y": 0.5}), hold, cash=2000.0, prices=prices, policy=pol)
    t = _by(trades)
    assert t["Y"].dollars == pytest.approx(1500.0)
    assert t["X"].dollars == pytest.approx(400.0) and t["X"].reason == "deploy"


def test_harvest_sells_loss_lots_and_buys_substitute():
    prices = pd.Series({"X": 100.0, "S": 50.0, "Y": 10.0})
    hold = {"X": HoldingView("X", 100.0, [(0, 100, 15000.0, OLD)], last_buy=OLD),
            "Y": HoldingView("Y", 10.0, [(0, 500, 5000.0, OLD)], last_buy=OLD)}
    pol = ExecutionPolicy(harvest=True, substitutes=(("X", "S"),))
    trades = plan(D, pd.Series({"X": 0.5, "Y": 0.5}), hold, cash=0.0, prices=prices, policy=pol)
    t = _by(trades)
    assert t["X"].dollars == pytest.approx(-10000.0) and t["X"].reason == "harvest"
    assert t["X"].lot_plan == [(0, 100)]
    assert t["S"].dollars == pytest.approx(7500.0)       # 10000 harvested in, 2500 trimmed to the 50% target
    assert t["Y"].dollars == pytest.approx(2500.0)
    # a fund bought in the last 30 days is not harvested
    hold["X"].last_buy = D - pd.Timedelta(days=10)
    trades = plan(D, pd.Series({"X": 0.5, "Y": 0.5}), hold, cash=0.0, prices=prices, policy=pol)
    assert all(tr.reason != "harvest" for tr in trades)
    # day 30 is still inside the ledger's wash-sale window, day 31 is not
    half = pd.Series({"X": 0.5, "Y": 0.5})
    hold["X"].last_buy = D - pd.Timedelta(days=30)
    assert all(tr.reason != "harvest" for tr in plan(D, half, hold, cash=0.0, prices=prices, policy=pol))
    hold["X"].last_buy = D - pd.Timedelta(days=31)
    assert any(tr.reason == "harvest" for tr in plan(D, half, hold, cash=0.0, prices=prices, policy=pol))
    # a substitute loss-sold within the no-rebuy window blocks the swap
    hold["X"].last_buy = OLD
    hold["S"] = HoldingView("S", 50.0, [], last_loss_sale=D - pd.Timedelta(days=5))
    trades = plan(D, pd.Series({"X": 0.5, "Y": 0.5}), hold, cash=0.0, prices=prices, policy=pol)
    assert all(tr.reason != "harvest" for tr in trades)


def test_no_rebuy_window_applies_to_a_fund_with_no_holding_view():
    """A fund sold out completely has no holding view; its loss-sale date reaches the planner separately."""
    prices = pd.Series({"X": 100.0, "S": 50.0, "Y": 10.0})
    targets = pd.Series({"X": 0.5, "Y": 0.5})
    hold = {"X": HoldingView("X", 100.0, [(0, 100, 15000.0, OLD)], last_buy=OLD),
            "Y": HoldingView("Y", 10.0, [(0, 500, 5000.0, OLD)], last_buy=OLD)}
    pol = ExecutionPolicy(harvest=True, substitutes=(("X", "S"),))
    # S was loss-sold five days ago and is no longer held: harvesting X must not buy it back
    trades = plan(D, targets, hold, cash=0.0, prices=prices, policy=pol, last_loss_sale={"S": D - pd.Timedelta(days=5)})
    assert all(tr.reason != "harvest" for tr in trades) and "S" not in _by(trades)
    # day 30 is still inside the window, day 31 is not
    trades = plan(D, targets, hold, cash=0.0, prices=prices, policy=pol, last_loss_sale={"S": D - pd.Timedelta(days=30)})
    assert "S" not in _by(trades)
    trades = plan(D, targets, hold, cash=0.0, prices=prices, policy=pol, last_loss_sale={"S": D - pd.Timedelta(days=31)})
    assert _by(trades)["S"].dollars > 0 and _by(trades)["X"].reason == "harvest"
    # a rebalance buy uses the substitute while the fund itself is inside its window
    trades = plan(D, pd.Series({"X": 0.5}), {}, cash=10_000.0, prices=prices, policy=pol,
                  last_loss_sale={"X": D - pd.Timedelta(days=10)})
    t = _by(trades)
    assert "X" not in t and t["S"].dollars == pytest.approx(5000.0)
    # without harvesting there is no window: the calendar policies are unchanged
    trades = plan(D, pd.Series({"X": 0.5}), {}, cash=10_000.0, prices=prices, policy=ExecutionPolicy(),
                  last_loss_sale={"X": D - pd.Timedelta(days=10)})
    assert _by(trades)["X"].dollars == pytest.approx(5000.0)


class ZeroCost(CostModel):
    def trade_cost(self, ticker, notional, adv, daily_vol=None):
        return 0.0


def _step_market(steps: dict[str, list[tuple[str, float]]]) -> MarketData:
    """Flat prices that step to a new level on the given dates; no distributions, deep volume."""
    idx = pd.bdate_range("2023-07-03", "2023-12-29")
    px = pd.DataFrame(index=idx, columns=list(steps), dtype=float)
    for t, path in steps.items():
        for day, level in path:
            px.loc[pd.Timestamp(day):, t] = level
    zeros = pd.DataFrame(0.0, index=idx, columns=px.columns)
    macro = pd.DataFrame({"spx": 1.0, "vix": 1.0, "tnx": 1.0, "irx": 0.0, "crb": 1.0}, index=idx)
    return MarketData(adj_close=px, close=px.copy(), volume=zeros + 1e7, distributions=zeros, macro=macro,
                      splits=zeros.copy())


def test_simulator_keeps_the_no_rebuy_window_after_a_fund_is_sold_out():
    """X is harvested into S, S is then sold, and the group is bought again inside X's 31-day window.

    Nothing else blocks the purchase: no harvest is involved at the third date, so only the no-rebuy window
    can steer the buy from X to S. Without the ledger's loss-sale memory the simulator bought X back.
    """
    md = _step_market({"X": [("2023-07-03", 100.0), ("2023-10-02", 90.0)],
                       "S": [("2023-07-03", 50.0)], "Y": [("2023-07-03", 10.0)]})
    buy, swap, out, back = (pd.Timestamp(d) for d in ("2023-08-01", "2023-10-02", "2023-10-09", "2023-10-16"))
    assert (back - swap).days < 31
    targets = pd.DataFrame({"X": [0.5, 0.5, 0.0, 0.5], "Y": [0.5, 0.5, 1.0, 0.5]}, index=[buy, swap, out, back])
    spec = Spec(cash_yield_mode="fixed", cash_yield_fixed=0.0)
    pol = ExecutionPolicy(harvest=True, substitutes=(("X", "S"),))
    for taxed in (False, True):
        res = portfolio.simulate(md, targets, spec=spec, taxed=taxed, cost_model=ZeroCost(spec, override_file=None),
                                 initial=1_000_000.0, policy=pol)
        assert res.harvests.loc[swap] == 1                         # X is harvested into S
        assert res.weights.loc[swap, "X"] == 0.0
        assert res.weights.loc[out, "S"] == 0.0                    # the group is then sold out completely
        assert res.trades.loc[back, "X"] == 0.0                    # and bought back through S, not X
        assert res.trades.loc[back, "S"] > 0.0
        assert not [e for e in res.ledger.log if e["event"] == "wash_after"]


def test_participation_cap_scales_and_truncates_lot_plans():
    prices = pd.Series({"X": 100.0, "Y": 10.0})
    adv = pd.Series({"X": 10_000.0, "Y": 20_000.0})
    hold = {"Y": HoldingView("Y", 10.0, [(0, 600, 6000.0, OLD), (1, 400, 4000.0, NEW)])}
    pol = ExecutionPolicy(max_participation=0.10)
    trades = plan(D, pd.Series({"X": 0.5, "Y": 0.0}), hold, cash=10_000.0, prices=prices, policy=pol, adv=adv)
    t = _by(trades)
    assert t["X"].dollars == pytest.approx(1000.0) and t["X"].capped and t["X"].requested == pytest.approx(10_000.0)
    assert t["Y"].dollars == pytest.approx(-2000.0) and t["Y"].capped
    assert t["Y"].lot_plan == [(0, pytest.approx(200.0))]


def test_harvesting_never_rebuys_inside_the_window_on_random_paths():
    """Invariant on realistic paths: under a harvesting policy the ledger never sees a rebuy within 30 days.

    Month-to-month targets that sometimes drop a fund to zero empty a harvested group and buy it again, which
    is where the no-rebuy window decides. On this seed the planner without the ledger's loss-sale memory
    rebuys inside the window five times, before and after tax.
    """
    import numpy as np

    from vaneck.calendar import walk_forward_splits
    from vaneck.data import make_synthetic
    md = make_synthetic(n_days=1500, seed=2, daily_vol=0.02)
    pairs = (("SMH", "MOAT"), ("GDX", "ANGL"), ("REMX", "FLTR"), ("HAP", "PFXF"))
    funds = [a for a, _ in pairs]
    rng = np.random.default_rng(2)
    rows = {}
    for sp in walk_forward_splits(md.index, md.index[0], md.index[-1]):
        w = rng.uniform(0.1, 1.0, len(funds)) * (rng.uniform(size=len(funds)) > 0.35)
        if w.sum() == 0:
            w[0] = 1.0
        rows[sp.exec_date] = dict(zip(funds, w / w.sum() * 0.98))
    targets = pd.DataFrame(rows).T.sort_index()
    pol = ExecutionPolicy(harvest=True, lot_method="TAXOPT", substitutes=pairs)
    for taxed in (False, True):
        res = portfolio.simulate(md, targets, taxed=taxed, policy=pol)
        assert res.harvests.sum() >= 10                              # the policy is active on these paths
        assert not [e for e in res.ledger.log if e["event"] == "wash_after"]


def test_a_complete_harvest_leaves_no_residue_in_the_simulator():
    """Found by search: on this path the dollars of the sale, divided by the price, come back a hair short of the
    units held. The simulator kept 1e-16 of the account in X, with no tax lot behind it."""
    dates = pd.to_datetime(["2023-02-01", "2023-03-01", "2023-05-01", "2023-07-03", "2023-09-01"])
    p = 66.328834
    path = [p, round(p * 1.03, 6), round(p * 1.01, 6), round(p * 0.99, 6), round(p * 0.86, 6)]   # ends 14% down
    idx = pd.bdate_range("2023-01-02", "2023-12-29")
    px = pd.DataFrame({"X": float("nan"), "S": 118.81301, "Y": 10.0}, index=idx)
    for d, level in zip(dates, path):
        px.loc[d:, "X"] = level
    px = px.bfill()
    zeros = pd.DataFrame(0.0, index=idx, columns=px.columns)
    macro = pd.DataFrame({"spx": 1.0, "vix": 1.0, "tnx": 1.0, "irx": 0.0, "crb": 1.0}, index=idx)
    md = MarketData(adj_close=px, close=px.copy(), volume=zeros + 1e7, distributions=zeros, macro=macro, splits=zeros.copy())
    wx = [0.3732, 0.6852, 0.6204, 0.4925, 0.6254]
    targets = pd.DataFrame({"X": wx, "Y": [round(1 - w, 4) for w in wx]}, index=dates)
    spec = Spec(cash_yield_mode="fixed", cash_yield_fixed=0.0)
    pol = ExecutionPolicy(harvest=True, substitutes=(("X", "S"),))
    res = portfolio.simulate(md, targets, spec=spec, cost_model=ZeroCost(spec, override_file=None), initial=1_000_000.0,
                             policy=pol)
    last = dates[-1]
    assert res.harvests.loc[last] == 1
    assert res.weights.loc[last, "X"] == 0.0 and res.ledger.lots["X"] == []
    assert res.weights.loc[last, "S"] == pytest.approx(wx[-1])
