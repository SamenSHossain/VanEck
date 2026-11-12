from dataclasses import replace

from vaneck import design
from vaneck.calendar import walk_forward_splits
from vaneck.config import SPEC


def test_design_report_runs_on_synthetic(synth):
    spec = replace(SPEC, study_end=str(synth.index[-1].date()))
    splits = walk_forward_splits(synth.index, synth.index[0], synth.index[-1])
    rep = design.design_report(synth, splits, spec, n_boot=50)
    assert set(rep) >= {"power", "hac", "dsr6", "dsr12", "rw6", "rw12", "meta"}
    assert rep["rw6"].shape[0] == 6 and rep["rw12"].shape[0] == 12
    assert rep["power"]["mde_monthly_bonferroni_x12"].astype(float).gt(0).all()
    assert rep["hac"]["p_hac_one_sided"].astype(float).between(0, 1).all()
