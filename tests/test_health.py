import numpy as np
import pandas as pd

from vaneck.health import HealthLimits, check_market_data


def test_synthetic_data_is_healthy(synth):
    now = synth.index[-1] + pd.Timedelta(days=1)
    rep = check_market_data(synth, now=now, raw_dir=synth.index.__class__ and __import__("pathlib").Path("/nonexistent"))
    blocking = [c.name for c in rep.checks if c.status == "block"]
    assert not blocking, blocking
    assert rep.to_dict()["status"] in ("ok", "warn")


def test_nan_and_staleness_block(synth):
    md = synth
    saved = md.adj_close.iloc[-1, 0]
    md.adj_close.iloc[-1, 0] = np.nan
    rep = check_market_data(md, now=md.index[-1] + pd.Timedelta(days=1))
    assert rep.blocked
    assert {c.name for c in rep.checks if c.status == "block"} >= {"last_bar_alignment", "recent_nans"}
    md.adj_close.iloc[-1, 0] = saved  # restore the exact value for other tests
    rep2 = check_market_data(md, now=md.index[-1] + pd.Timedelta(days=30))
    assert any(c.name == "bar_staleness_days" and c.status == "block" for c in rep2.checks)


def test_adjustment_break_blocks(synth):
    md = synth
    col = md.adj_close.columns[1]
    saved = md.adj_close[col].iloc[-3]
    md.adj_close.iloc[-3, 1] = saved * 0.9  # adj/close ratio drops without a matching close move
    rep = check_market_data(md, now=md.index[-1] + pd.Timedelta(days=1), limits=HealthLimits(jump_warn=0.5))
    assert any(c.name == "adjustment_breaks" and c.status == "block" for c in rep.checks)
    md.adj_close.iloc[-3, 1] = saved
