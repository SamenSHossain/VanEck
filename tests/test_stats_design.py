import numpy as np
import pandas as pd

from vaneck.stats import (
    deflated_sharpe_table,
    expected_max_sharpe,
    hac_paired_test,
    min_detectable_effect,
    newey_west_se,
    probabilistic_sharpe,
    required_n,
    romano_wolf,
)


def test_mde_and_required_n_are_consistent():
    m7 = min_detectable_effect(7, 0.035, alpha=0.05 / 6, power=0.8)
    m90 = min_detectable_effect(90, 0.035, alpha=0.05 / 6, power=0.8)
    assert m90 < m7
    assert min_detectable_effect(7, 0.07, alpha=0.05 / 6) > m7
    n = required_n(0.0144, 0.035, alpha=0.05 / 6, power=0.8)
    assert n is not None and n > 20
    assert min_detectable_effect(n, 0.035, alpha=0.05 / 6) <= 0.0144 < min_detectable_effect(n - 1, 0.035, alpha=0.05 / 6)


def test_hac_se_tracks_dependence():
    rng = np.random.default_rng(0)
    iid = rng.normal(0, 1, 2000)
    se_plain = iid.std(ddof=1) / np.sqrt(len(iid))
    se_hac, lags = newey_west_se(iid)
    assert abs(se_hac / se_plain - 1) < 0.15 and lags > 0
    ar = np.empty(2000)
    ar[0] = 0.0
    for i in range(1, 2000):
        ar[i] = 0.6 * ar[i - 1] + rng.normal()
    assert newey_west_se(ar)[0] > ar.std(ddof=1) / np.sqrt(2000) * 1.5
    res = hac_paired_test(pd.Series(ar + 0.5), pd.Series(np.zeros(2000)))
    assert res["p_one_sided"] < 0.01 and res["t"] > 0


def test_psr_dsr_properties():
    assert abs(probabilistic_sharpe(0.0, 0.0, 100, 0.0, 3.0) - 0.5) < 1e-12
    assert expected_max_sharpe(0.01, 12) > expected_max_sharpe(0.01, 6) > 0
    rng = np.random.default_rng(1)
    rets = pd.DataFrame(rng.normal(0.002, 0.03, size=(120, 6)), columns=list("abcdef"))
    tbl = deflated_sharpe_table(rets, n_trials=6)
    assert ((tbl["deflated_sr"] <= tbl["psr_vs_zero"] + 1e-12) & tbl["deflated_sr"].between(0, 1)).all()


def test_romano_wolf_controls_noise_and_finds_signal():
    rng = np.random.default_rng(2)
    noise = rng.normal(0, 0.03, size=(120, 5))
    signal = rng.normal(0.03, 0.03, size=(120, 1))
    diffs = pd.DataFrame(np.hstack([signal, noise]), columns=["sig", "n1", "n2", "n3", "n4", "n5"])
    tbl = romano_wolf(diffs, n_boot=500, block=6, seed=0)
    assert tbl.loc["sig", "reject_at_alpha"]
    assert not tbl.drop("sig")["reject_at_alpha"].any()
    assert (tbl["p_adjusted_stepdown"] >= tbl["p_raw_bootstrap"] - 1e-12).all()
    ordered = tbl.sort_values("t", ascending=False)["p_adjusted_stepdown"].to_numpy()
    assert (np.diff(ordered) >= -1e-12).all()
    assert 0 <= tbl.attrs["white_reality_check_p"] <= 1
