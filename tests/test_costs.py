import pytest

from vaneck.config import SPEC
from vaneck.costs import CostModel


def test_spread_tiers_monotone():
    cm = CostModel(SPEC, override_file=None)
    levels = [1e9, 2e8, 5e7, 5e6, 1e5]
    spreads = [cm.spread_bps("Z", a) for a in levels]
    assert spreads == sorted(spreads)
    assert spreads[0] == 2.0 and spreads[-1] == 40.0


def test_trade_cost_formula():
    cm = CostModel(SPEC, override_file=None)
    cost = cm.trade_cost("Z", 1_000_000.0, 1e9)  # 2 bps spread -> 1 bp half + 0.5 slippage
    assert cost == pytest.approx(1_000_000.0 * 1.5 / 1e4 + SPEC.fee_per_trade)
    assert cm.trade_cost("Z", 0.0, 1e9) == 0.0
    assert cm.trade_cost("Z", -1_000_000.0, 1e9) == cm.trade_cost("Z", 1_000_000.0, 1e9)


def test_override_wins():
    cm = CostModel(SPEC, overrides={"Z": 7.0}, override_file=None)
    assert cm.spread_bps("Z", 1e12) == 7.0
