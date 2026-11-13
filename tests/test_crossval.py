import numpy as np

from vaneck.crossval import compare_macro, corporate_actions_audit, peer_divergence
from vaneck.health import BLOCK, OK, WARN


def test_macro_agreement_and_last_day_disagreement(synth):
    fred = {k: synth.macro[k].copy() for k in ("vix", "tnx", "spx")}
    checks, tbl = compare_macro(synth, fred)
    assert all(c.status == OK for c in checks) and (tbl["share_beyond_tol"] == 0).all()
    fred["vix"].iloc[-1] += 3.0                      # a wrong print on the latest common day
    checks, _ = compare_macro(synth, fred)
    assert next(c for c in checks if c.name == "fred_vix").status == BLOCK


def test_corporate_actions_consistent_then_broken(synth):
    checks, tbl = corporate_actions_audit(synth, ["SMH", "GDX"])
    assert checks[0].status == OK and (tbl["unmatched_jumps"] == 0).all()
    d = synth.index[-40]
    saved = synth.adj_close.loc[:d, "SMH"].copy()
    synth.adj_close.loc[:d, "SMH"] *= 0.98          # an adjustment with no recorded distribution
    try:
        checks, tbl = corporate_actions_audit(synth, ["SMH"])
        assert checks[0].status == BLOCK and tbl.loc["SMH", "unmatched_jumps"] >= 1
    finally:
        synth.adj_close.loc[:d, "SMH"] = saved          # exact values, not a division by 0.98


def test_peer_divergence_flags_a_bad_print(synth):
    pairs = {"SMH": "MOAT"}
    checks, tbl = peer_divergence(synth, pairs)
    base_status = checks[0].status
    assert base_status in (OK, WARN)
    d = synth.index[-5]
    saved = synth.adj_close.at[d, "SMH"]
    synth.adj_close.at[d, "SMH"] = saved * 1.5      # +50% one-day error
    try:
        checks, tbl = peer_divergence(synth, pairs)
        assert checks[0].status == BLOCK and tbl.iloc[0]["max_one_day_gap"] > 0.25
    finally:
        synth.adj_close.at[d, "SMH"] = saved
    assert np.isfinite(tbl.iloc[0]["corr"])
