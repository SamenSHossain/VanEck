import pandas as pd
import pytest

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
    # a substitute loss-sold within the no-rebuy window blocks the swap
    hold["X"].last_buy = OLD
    hold["S"] = HoldingView("S", 50.0, [], last_loss_sale=D - pd.Timedelta(days=5))
    trades = plan(D, pd.Series({"X": 0.5, "Y": 0.5}), hold, cash=0.0, prices=prices, policy=pol)
    assert all(tr.reason != "harvest" for tr in trades)


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
