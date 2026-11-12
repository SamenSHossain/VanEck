import numpy as np
import pandas as pd

from vaneck import retire

EXP = {"monthly_return_p5": -0.05, "rolling_sharpe_12m_p5": -0.5, "max_drawdown": -0.20, "drawdown_retire_level": -0.25}


def _curve(monthly_rets, start="2023-01-01"):
    """Daily marks whose calendar-month returns are exactly ``monthly_rets``."""
    months = pd.period_range(start, periods=len(monthly_rets), freq="M")
    parts = []
    level = 100.0
    for per, r in zip(months, monthly_rets):
        days = pd.bdate_range(per.start_time, per.end_time)
        step = (1.0 + r) ** (1.0 / len(days)) - 1.0
        parts.append(pd.Series(level * np.cumprod(np.full(len(days), 1.0 + step)), index=days))
        level = parts[-1].iloc[-1]
    return pd.concat(parts)


def test_rules_fire_as_pre_registered():
    rf = pd.Series(0.0, index=pd.bdate_range("2022-01-01", "2026-01-01"))
    healthy = _curve([0.01] * 8)
    res = retire.evaluate(healthy, rf, EXP, monitor_history=[])
    by = {r.rule: r for r in res}
    assert by["R1_monthly_returns"].status == "ok" and by["R2_drawdown"].status == "ok"
    assert by["R3_rolling_sharpe"].status == "insufficient" and by["R4_engine_agreement"].status == "insufficient"
    assert retire.overall(res) == "ok"
    bad = _curve([0.01, 0.0, -0.06, 0.0, -0.07, 0.01, -0.08, 0.0])   # 3 of the last 6 months below -5%
    res = retire.evaluate(bad, rf, EXP, monitor_history=[])
    assert {r.rule: r.status for r in res}["R1_monthly_returns"] == "retire"
    deep = _curve([0.02, -0.30, 0.01])
    res = retire.evaluate(deep, rf, EXP, monitor_history=[])
    assert {r.rule: r.status for r in res}["R2_drawdown"] == "retire" and retire.overall(res) == "retire"
    hist = [{"alerts": ["paper vs research (executed weights) differ: engine disagreement, investigate"]}] * 2
    res = retire.evaluate(healthy, rf, EXP, monitor_history=hist)
    assert {r.rule: r.status for r in res}["R4_engine_agreement"] == "halt"
    hist = [{"alerts": ["implementability gap -2.0% exceeds 1.0%"]}] * 3
    res = retire.evaluate(healthy, rf, EXP, monitor_history=hist)
    assert {r.rule: r.status for r in res}["R5_implementability"] == "review"


def test_flag_roundtrip(tmp_path):
    res = retire.evaluate(_curve([0.02, -0.30, 0.01]), pd.Series(0.0, index=pd.bdate_range("2022-01-01", "2026-01-01")), EXP)
    assert retire.is_retired("EqualWeight", tmp_path) is None
    retire.set_flag("EqualWeight", retire.overall(res), res, tmp_path)
    flag = retire.is_retired("EqualWeight", tmp_path)
    assert flag["status"] == "retire" and flag["rules"][0]["rule"] == "R2_drawdown"
    retire.clear_flag("EqualWeight", "alice", "reviewed: data error", tmp_path)
    assert retire.is_retired("EqualWeight", tmp_path) is None and (tmp_path / "retire.log").exists()


def test_expectations_from_research_engine(synth):
    from dataclasses import replace

    from vaneck.config import SPEC
    spec = replace(SPEC, fund_start=str(synth.index[0].date()), study_end=str(synth.index[-1].date()))
    exp = retire.compute_expectations(synth, "EqualWeight", spec)
    assert exp["n_months"] > 12 and exp["monthly_return_p5"] < exp["monthly_return_median"]
    assert exp["max_drawdown"] < 0 and exp["drawdown_retire_level"] < exp["max_drawdown"]
