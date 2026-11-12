import pandas as pd
import pytest

from vaneck.risk import RiskLimits, orders_from_targets, pre_trade_checks


@pytest.fixture
def world():
    tickers = ["A", "B", "C"]
    prices = pd.Series({"A": 100.0, "B": 50.0, "C": 20.0})
    adv = pd.Series({"A": 50e6, "B": 5e6, "C": 1e5})
    return tickers, prices, adv


def test_orders_are_whole_shares_and_sells_first(world):
    tickers, prices, adv = world
    targets = pd.Series({"A": 0.5, "B": 0.5, "C": 0.0})
    positions = {"C": 1000.0}  # must be sold
    cash = 100_000.0
    limits = RiskLimits(max_participation=1.0, cost_reserve_bps=0.0)
    orders = orders_from_targets(targets, positions, prices, cash, adv, limits)
    assert orders[0].side == "SELL" and orders[0].ticker == "C" and orders[0].shares == 1000
    equity = cash + 1000 * 20.0
    buy_a = next(o for o in orders if o.ticker == "A")
    assert buy_a.shares == int(0.5 * equity / 100.0)
    # with the default cost reserve the sizing is slightly smaller
    reserved = orders_from_targets(targets, positions, prices, cash, adv, RiskLimits(max_participation=1.0))
    assert next(o for o in reserved if o.ticker == "A").shares <= buy_a.shares
    assert all(float(o.shares).is_integer() for o in orders)


def test_portfolio_constraints_block(world):
    tickers, prices, adv = world
    bad = pd.Series({"A": 0.7, "B": 0.4, "C": -0.1})
    rep = pre_trade_checks([], bad, 1e6, 1e6, limits=RiskLimits(), cap=0.5)
    names = {c.name: c.status for c in rep.checks}
    assert names["long_only"] == "block" and names["max_weight"] == "block" and names["gross_exposure"] == "block"
    assert rep.blocked


def test_participation_scaling_and_dust(world):
    tickers, prices, adv = world
    targets = pd.Series({"A": 0.3, "B": 0.3, "C": 0.3})
    orders = orders_from_targets(targets, {}, prices, 1e6, adv, RiskLimits())
    rep = pre_trade_checks(orders, targets, 1e6, 1e6, limits=RiskLimits(max_participation=0.10))
    c = next(o for o in rep.orders if o.ticker == "C")
    assert c.notional <= 0.10 * adv["C"] + 20.0
    assert "scaled" in c.note
    assert any(ch.name == "participation" and ch.status == "warn" for ch in rep.checks)
    tiny = orders_from_targets(pd.Series({"A": 0.0004}), {}, prices, 1e6, adv, RiskLimits())  # 3 shares = $300
    rep2 = pre_trade_checks(tiny, pd.Series({"A": 0.0004}), 1e6, 1e6, limits=RiskLimits(min_order_notional=500))
    assert not rep2.orders and rep2.dropped


def test_turnover_and_cash_blocks(world):
    tickers, prices, adv = world
    targets = pd.Series({"A": 0.5, "B": 0.5})
    orders = orders_from_targets(targets, {}, prices, 1e6, adv, RiskLimits(max_participation=10.0))
    rep = pre_trade_checks(orders, targets, 1e6, 1e6, limits=RiskLimits(max_turnover_one_way=0.25, max_participation=10.0))
    assert any(c.name == "turnover_one_way" and c.status == "block" for c in rep.checks)
    rep2 = pre_trade_checks(orders, targets, 1e6, cash=100.0, limits=RiskLimits(max_participation=10.0))
    assert any(c.name == "cash_after_trades" and c.status == "block" for c in rep2.checks)


def test_drawdown_halt_removes_buys_keeps_sells(world):
    tickers, prices, adv = world
    targets = pd.Series({"A": 0.5, "B": 0.0})
    orders = orders_from_targets(targets, {"B": 2000.0}, prices, 500_000.0, adv, RiskLimits(max_participation=10.0))
    rep = pre_trade_checks(orders, targets, 600_000.0, 500_000.0, limits=RiskLimits(max_participation=10.0), drawdown=-0.30)
    assert rep.halted
    assert all(o.side == "SELL" for o in rep.orders) and any(o.side == "BUY" for o in rep.dropped)


def test_staleness_blocks(world):
    tickers, prices, adv = world
    rep = pre_trade_checks([], pd.Series({"A": 0.1}), 1e6, 1e6, staleness_days=9)
    assert any(c.name == "signal_staleness_days" and c.status == "block" for c in rep.checks)
