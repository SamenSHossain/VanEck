import numpy as np
import pandas as pd

from vaneck.config import SPEC
from vaneck.stats import bonferroni_table, paired_onesided_t


def test_paired_onesided_direction():
    x = pd.Series([0.10, 0.12, 0.08, 0.11, 0.09])
    y = pd.Series([0.05, 0.06, 0.04, 0.05, 0.05])
    res = paired_onesided_t(x, y)
    assert res["p_one_sided"] < 0.01 and res["t"] > 0
    rev = paired_onesided_t(y, x)
    assert rev["p_one_sided"] > 0.99


def test_bonferroni_threshold():
    tbl = bonferroni_table({"a": {"p_one_sided": 0.01}, "b": {"p_one_sided": 0.005}}, SPEC)
    assert np.isclose(tbl["alpha_bonferroni"].iloc[0], 0.05 / 6)
    assert not tbl.loc["a", "significant"] and tbl.loc["b", "significant"]
