"""The dashboard exporter: its pure helpers, and a smoke run that checks the tables the page reads."""
from dataclasses import replace

import numpy as np
import pandas as pd

from vaneck import golden, webdash
from vaneck.config import SPEC


def test_junit_cases_are_grouped_by_test_module():
    assert webdash._module("tests.test_tax") == "test_tax"
    assert webdash._module("tests.test_tax.TestLots") == "test_tax"
    assert webdash._module("test_tax") == "test_tax"


def test_booleans_become_words_and_keys_survive():
    rows = webdash._rows("t", [{"a": True, "b": False, "c": 1.5, "d": None}])
    assert rows == [{"table": "t", "a": "yes", "b": "no", "c": 1.5, "d": None}]
    assert webdash._kv("k", {"x": True}) == [{"table": "k", "key": "x", "value": "yes"}]


def test_book_bands_use_the_retirement_rules_month_definition():
    days = pd.bdate_range("2023-12-29", "2024-03-29")
    curve = pd.Series(100.0, index=days)
    curve[days >= "2024-02-01"] = 90.0     # one 10% fall at the start of February, flat otherwise
    bands = webdash.book_bands([(d.strftime("%Y-%m-%d"), float(v)) for d, v in curve.items()])
    assert [m["month"] for m in bands["monthly"]] == ["2024-01", "2024-02", "2024-03"]   # the stub December is not a month
    assert [round(m["ret"], 6) for m in bands["monthly"]] == [0.0, -0.1, 0.0]
    assert len(bands["drawdown"]) == len(curve) and min(bands["drawdown"]) == -0.1 and bands["drawdown"][0] == 0.0
    assert webdash.book_bands([]) == {"monthly": [], "drawdown": []}


def test_golden_detail_counts_values_within_tolerance():
    want = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, np.nan]}, index=["x", "y"])
    same = golden.detail({"t": want.copy()}, {"t": want})[0]
    assert same["ok"] and same["matching"] == 4 and same["values"] == 4 and same["max_abs_diff"] == 0.0
    moved = want.copy()
    moved.loc["x", "a"] = 1.5
    off = golden.detail({"t": moved}, {"t": want})[0]
    assert not off["ok"] and off["matching"] == 3 and abs(off["max_abs_diff"] - 0.5) < 1e-12
    one_sided_nan = want.copy()
    one_sided_nan.loc["y", "b"] = 7.0
    assert golden.detail({"t": one_sided_nan}, {"t": want})[0]["matching"] == 3
    missing = golden.detail({"t": want.drop(index="y")}, {"t": want})[0]
    assert not missing["ok"] and missing["matching"] == 0 and "missing" in missing["problem"]
    assert golden.compare({"t": want.copy()}, {"t": want}) == [] and golden.compare({"t": moved}, {"t": want})


def test_exported_tables_carry_what_the_dashboard_reads(synth, tmp_path):
    spec = replace(SPEC, fund_start=str(synth.index[0].date()), study_end=str(synth.index[-1].date()))
    payload = webdash.collect(synth, spec, junit=tmp_path / "absent.xml", n_boot=40, check_golden=False)
    t = webdash.tables(payload)
    assert set(t) == {"study_tables", "nav_weekly", "extensions", "operations", "tests", "weights"}

    def table(name, tbl):
        return [r for r in t[name] if r.get("table") == tbl]

    margins = table("study_tables", "gate1_margin")
    assert [m["benchmark"] for m in margins] == [webdash.study.PRIMARY_BENCHMARK, webdash.CLASSIC_BENCHMARK]
    summary = {r["name"]: r for r in table("study_tables", "gate1_summary")}
    for m in margins:
        assert m["sharpe_diff_ci_low"] <= m["sharpe_diff_ci_high"] and 0.0 <= m["share_ge_threshold"] <= 1.0
        # the bootstrap's point estimate is the same statistic Gate 1 compares with its threshold
        gate1 = summary["EqualWeight"]["sharpe_post"] - summary[m["benchmark"]]["sharpe_post"]
        assert abs(m["sharpe_diff"] - gate1) < 1e-9
    tests = table("study_tables", "phase2_tests")
    assert len(tests) == 12 and {"sharpe_diff", "sharpe_diff_ci_low", "sharpe_diff_ci_high", "alpha_bonferroni"} <= set(tests[0])

    edge = table("study_tables", "rolling_edge")
    assert {r["run"] for r in edge} == {"EqualWeight"} | {x["strategy"] for x in tests if x["family"] == "primary"}
    assert {"date", "run", "kind", "excess_12m", "sharpe_diff_12m"} <= set(edge[0])

    holdings = [k for k in t["weights"][0] if k not in ("run", "kind", "date", "signal_date", "regime")]
    assert "CASH" in holdings and len({r["run"] for r in t["weights"]}) == 12
    assert all(abs(sum(r[h] for h in holdings) - 1.0) < 2e-3 for r in t["weights"])
    assert all(r["regime"] for r in t["weights"])

    assert table("tests", "provenance")[0]["same_inputs"] == "yes"
    assert not table("tests", "golden_files")          # the frozen-results check was skipped

    state = {r["key"]: r["value"] for r in table("operations", "ops_state")}
    assert state["submission"] in ("open", "sell-only", "blocked") and state["attention"] in ("yes", "no")
    assert state["checked_utc"]
    gates = table("operations", "ops_gates")
    assert {"switch", "state", "effect", "detail", "since"} <= set(gates[0])
    assert {g["switch"] for g in gates} >= {"data health gate", "halt flag", "approval signing", "risk limits"}
    files = table("operations", "data_files")
    assert {"series", "role", "provider", "status", "first", "last", "rows", "sha256", "in_data_hash"} <= set(files[0])
    assert sum(f["role"] == "universe fund" for f in files) == 9 and all(f["sha256"] for f in files if f["in_data_hash"] == "yes")
    sealed = {r["key"]: r["value"] for r in table("study_tables", "holdout")}
    assert sealed["state"] in ("sealed", "opened") and sealed["start"] == spec.holdout_start
    if sealed["state"] == "sealed":
        assert not table("study_tables", "holdout_summary") and not table("study_tables", "holdout_margin")
    slip, checks = table("operations", "shortfall"), table("operations", "risk_checks")
    wanted = {"signal_date", "gap_before", "gap_after", "scaled", "ordered_share", "explicit_bps", "delay_bps"}
    if any((webdash.ROOT / "paper" / "decisions").glob("*.json")):
        assert slip and wanted <= set(slip[0])
        assert {"check", "decisions", "passed", "warned", "blocked", "limit"} <= set(checks[0])
    else:                                    # the container image ships without the paper book: the tables are empty
        assert not slip and not checks
    meta = {r["key"]: r["value"] for r in table("study_tables", "meta")}
    assert meta["code_state"] in ("committed", "uncommitted code changes", "unknown")
