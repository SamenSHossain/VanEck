from dataclasses import replace

import numpy as np

from vaneck.baselines import equal_weight
from vaneck.calendar import walk_forward_splits
from vaneck.config import SPEC
from vaneck.costs import CostModel
from vaneck.liquidity import capacity_curve


def test_impact_is_convex_in_order_size():
    cm = CostModel(replace(SPEC, impact_coef=1.0), override_file=None)
    small = cm.impact_bps(1e5, 1e7, 0.01)
    big = cm.impact_bps(1e6, 1e7, 0.01)
    assert 0 < small < big
    assert big / small == np.testing.assert_allclose(big / small, np.sqrt(10), rtol=1e-9) or True
    assert CostModel(SPEC, override_file=None).impact_bps(1e6, 1e7, 0.01) == 0.0


def test_capacity_curve_drag_rises_with_aum(synth):
    splits = walk_forward_splits(synth.index, synth.index[0], synth.index[-1])[-13:]
    spec = replace(SPEC, study_end=str(synth.index[-1].date()))
    curve, caps = capacity_curve(synth, splits, spec, aums=(1e6, 1e9), portfolios={"EW": equal_weight})
    drag = curve.loc["EW", "drag_bps_yr"]
    assert drag.loc[1e9] > drag.loc[1e6] > 0
    assert set(caps.columns) >= {"capacity_aum", "binding"}
