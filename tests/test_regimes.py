import numpy as np
import pandas as pd

from vaneck.regimes import classify, regime_inputs


def _inputs(vix, ytd, dd, crb, tnx):
    return pd.DataFrame({"vix": [vix], "spx_ytd": [ytd], "spx_drawdown": [dd], "crb_yoy": [crb], "tnx": [tnx]},
                        index=pd.DatetimeIndex(["2022-06-01"]))


def test_regime_definitions_and_precedence():
    assert classify(_inputs(12, 0.05, -0.02, 0.0, 3.0)).iloc[0] == "Bull"
    assert classify(_inputs(20, -0.05, -0.08, 0.0, 3.0)).iloc[0] == "Correction"
    assert classify(_inputs(30, -0.05, -0.08, 0.0, 3.0)).iloc[0] == "Crisis"        # VIX > 25
    assert classify(_inputs(20, -0.15, -0.22, 0.0, 3.0)).iloc[0] == "Crisis"        # drawdown
    assert classify(_inputs(20, -0.05, -0.08, 0.30, 4.5)).iloc[0] == "Stagflation"
    assert classify(_inputs(30, -0.05, -0.08, 0.30, 4.5)).iloc[0] == "Crisis"       # crisis beats stagflation
    assert classify(_inputs(12, -0.02, -0.03, 0.0, 3.0)).iloc[0] == "Neutral"       # low vol, negative YTD
    assert classify(_inputs(20, 0.15, -0.01, 0.0, 3.0)).iloc[0] == "Neutral"        # mid vol, strong YTD


def test_regime_inputs_ytd_and_yoy():
    idx = pd.bdate_range("2019-01-01", "2021-12-31")
    spx = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    macro = pd.DataFrame({"spx": spx, "vix": 15.0, "tnx": 2.0, "irx": 1.0, "crb": spx * 3})
    inp = regime_inputs(macro)
    last_2019 = spx[spx.index.year == 2019].iloc[-1]
    first_2020 = inp.loc[inp.index.year == 2020].iloc[0]
    assert first_2020["spx_ytd"] == (spx[spx.index.year == 2020].iloc[0] / last_2019 - 1)
    d = pd.Timestamp("2021-06-01")
    expected = spx.loc[d] / spx.loc[:pd.Timestamp("2020-06-01")].iloc[-1] - 1
    assert abs(inp.loc[d, "crb_yoy"] - expected) < 1e-12
    assert (inp["spx_drawdown"] <= 1e-12).all()
