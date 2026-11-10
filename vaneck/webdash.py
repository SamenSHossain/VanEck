"""Collect everything the interactive dashboard shows into one JSON document."""
from __future__ import annotations

import inspect
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import audit, design, metrics, retire, study
from .calendar import Split
from .config import REPORT_DIR, ROOT, SPEC, UNIVERSE, Spec
from .data import MarketData
from .regimes import episodes, regime_at
from .stats import bootstrap_sharpe_diff
from .universe import run_gate0

TABLES = REPORT_DIR / "tables"
EDGE_WINDOW = metrics.TRADING_DAYS   # trading days in the rolling 12-month edge
BOOT_BLOCK = int(inspect.signature(bootstrap_sharpe_diff).parameters["block"].default)   # the block every interval here uses
CLASSIC_BENCHMARK = "60/40 Classic (SPY/BND)"


def _records(df: pd.DataFrame, index_name: str = "name") -> list[dict]:
    out = df.copy()
    if isinstance(out.index, pd.MultiIndex):
        out = out.reset_index()
    else:
        out = out.reset_index().rename(columns={"index": index_name})
    out = out.replace({np.nan: None})
    return json.loads(out.to_json(orient="records", date_format="iso"))


def _csv(name: str, index_cols: int = 1) -> list[dict]:
    p = TABLES / f"{name}.csv"
    if not p.exists():
        return []
    df = pd.read_csv(p)
    return json.loads(df.replace({np.nan: None}).to_json(orient="records"))


def _weekly(series: pd.Series, base: float | None = 100.0) -> list:
    s = series.dropna().resample("W-FRI").last().dropna()
    if base is not None and len(s):
        s = base * s / s.iloc[0]
    return [round(float(v), 4) for v in s.to_numpy()]


def _module(classname: str) -> str:
    """``tests.test_tax`` -> ``test_tax``; a bare module name stays as it is."""
    parts = classname.split(".")
    if len(parts) > 1 and parts[0] == "tests":
        return parts[1]
    return parts[-1]


def _junit(path: Path) -> dict:
    if not path.exists():
        return {"available": False}
    root = ET.parse(path).getroot()
    cases: list[dict[str, Any]] = []
    for tc in root.iter("testcase"):
        outcome = "passed"
        if tc.find("failure") is not None or tc.find("error") is not None:
            outcome = "failed"
        elif tc.find("skipped") is not None:
            outcome = "skipped"
        cases.append({"file": _module(tc.get("classname") or ""), "name": tc.get("name") or "",
                      "time": round(float(tc.get("time") or 0.0), 3), "outcome": outcome})
    files: dict[str, dict[str, Any]] = {}
    for c in cases:
        blank = {"file": c["file"], "passed": 0, "failed": 0, "skipped": 0, "time": 0.0, "tests": []}
        f = files.setdefault(str(c["file"]), blank)
        f[c["outcome"]] += 1
        f["time"] = round(f["time"] + c["time"], 3)
        f["tests"].append({"name": c["name"], "outcome": c["outcome"], "time": c["time"]})
    return {"available": True, "total": len(cases), "passed": sum(c["outcome"] == "passed" for c in cases),
            "failed": sum(c["outcome"] == "failed" for c in cases), "skipped": sum(c["outcome"] == "skipped" for c in cases),
            "time": round(sum(c["time"] for c in cases), 1), "files": sorted(files.values(), key=lambda f: f["file"])}


def _store_timeline(n: int = 40) -> list[dict]:
    db = ROOT / "state" / "vaneck.sqlite"
    if not db.exists():
        return []
    from .store import Store
    st = Store(db)
    try:
        return st.timeline(n)
    finally:
        st.close()


def _json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def margin_interval(run: study.Run, bench: study.Run, spec: Spec = SPEC, n_boot: int = 2000) -> dict:
    """Bootstrap interval of a portfolio's post-tax Sharpe margin over a benchmark, the statistic Gate 1 decides on.

    Columns are named as in ``study.phase2_tests`` so both can be drawn on one axis. ``share_ge_threshold`` is the
    share of resamples whose margin reaches the Gate 1 requirement.
    """
    bs = bootstrap_sharpe_diff(run.post.nav, bench.post.nav, run.post.rf, n_boot=n_boot, return_draws=True)
    draws = bs["draws"]
    return {"portfolio": run.name, "benchmark": bench.name, "sharpe_diff": bs["sharpe_diff"],
            "sharpe_diff_ci_low": bs["ci_low"], "sharpe_diff_ci_high": bs["ci_high"], "p_bootstrap_sharpe": bs["p_one_sided"],
            "threshold": spec.gate1_sharpe_margin, "share_ge_threshold": float(np.mean(draws >= spec.gate1_sharpe_margin)),
            "n_boot": int(n_boot), "block_days": BOOT_BLOCK}


def rolling_edge(runs: dict[str, study.Run], bench: study.Run, window: int = EDGE_WINDOW) -> list[dict]:
    """Month-end samples of each run's trailing ``window``-day post-tax return and Sharpe ratio minus the benchmark's."""
    def trailing(sim) -> tuple[pd.Series, pd.Series]:
        ex = metrics.excess_returns(sim.nav, sim.rf)
        roll = ex.rolling(window)
        return sim.nav / sim.nav.shift(window) - 1.0, roll.mean() / roll.std(ddof=1) * np.sqrt(metrics.TRADING_DAYS)

    b_ret, b_sharpe = trailing(bench.post)
    rows: list[dict] = []
    for name, r in runs.items():
        ret, sharpe = trailing(r.post)
        df = pd.DataFrame({"excess_12m": ret - b_ret, "sharpe_diff_12m": sharpe - b_sharpe}).dropna()
        df = df.resample("ME").last().dropna()
        rows += [{"date": d.strftime("%Y-%m-%d"), "run": name, "kind": r.kind, "excess_12m": round(float(e), 5),
                  "sharpe_diff_12m": round(float(sd), 4)} for d, e, sd in zip(df.index, df["excess_12m"], df["sharpe_diff_12m"])]
    return rows


def weight_paths(md: MarketData, splits: list[Split], runs: dict[str, study.Run], spec: Spec = SPEC) -> list[dict]:
    """Target weights decided at each rebalance: one wide row per run and date, cash as the residual.

    ``regime`` is the regime the overlay saw on the signal date (the same call the strategies make).
    """
    seen = {s.exec_date: (s.signal_date, regime_at(md.window(end=s.signal_date, start=s.train_start).macro, spec))
            for s in splits}
    first = next(iter(runs.values())).pre.targets
    cols = [c for c in first.columns if any((r.pre.targets[c].abs() > 1e-9).any() for r in runs.values())]
    rows: list[dict] = []
    for name, r in runs.items():
        for d, w in r.pre.targets[cols].iterrows():
            signal, regime = seen.get(d, (None, None))
            row: dict = {"run": name, "kind": r.kind, "date": d.strftime("%Y-%m-%d"),
                         "signal_date": signal.strftime("%Y-%m-%d") if signal is not None else None, "regime": regime}
            row.update({c: round(float(w[c]), 4) for c in cols})
            row["CASH"] = round(max(0.0, 1.0 - float(w.sum())), 4)
            rows.append(row)
    return rows


def book_bands(marks: list[tuple[str, float]]) -> dict:
    """Calendar-month returns (the retirement rules' own definition) and the drawdown path of a book's daily marks."""
    if not marks:
        return {"monthly": [], "drawdown": []}
    curve = pd.Series({pd.Timestamp(k): float(v) for k, v in marks}).sort_index()
    monthly = retire.monthly_returns(curve)
    dd = curve / curve.cummax() - 1.0
    return {"monthly": [{"month": d.strftime("%Y-%m"), "ret": round(float(v), 6)} for d, v in monthly.items()],
            "drawdown": [round(float(v), 5) for v in dd.to_numpy()]}


def golden_check(md: MarketData, spec: Spec, base: dict, strat: dict, diag: dict) -> dict | None:
    """Frozen golden results against the current engine: input hashes, per-table reproduction, package versions."""
    from . import golden
    if not (golden.GOLDEN_DIR / "manifest.json").exists():
        return None
    manifest, frames = golden.load()
    changed = golden.inputs_changed(manifest, spec)
    files = golden.detail(golden.compute(md, spec, base=base, strat=strat, diag=diag), frames)
    reproduced = all(f["ok"] for f in files)
    now = audit.package_versions()
    return {"status": "inputs changed" if changed else ("match" if reproduced else "regression"),
            "checked_utc": audit.utc_now(), "frozen_utc": manifest["created_utc"],
            "config_hash_frozen": manifest["config_hash"], "config_hash_current": audit.config_hash(spec),
            "data_hash_frozen": manifest["data_hash"], "data_hash_current": audit.data_hash(),
            "values": sum(f["values"] for f in files), "reproduced": sum(f["matching"] for f in files),
            "rtol": manifest["rtol"], "atol": manifest["atol"], "files": files,
            "versions": [{"package": k, "frozen": v, "current": now.get(k), "same": now.get(k) == v}
                         for k, v in manifest["versions"].items()]}


def provenance(manifest: dict, golden: dict | None) -> list[dict]:
    """Each stored artefact the dashboard reads, with the input hashes it was produced from."""
    current = (manifest["config_hash"], manifest["data_hash"])

    def row(artefact: str, created, config, data) -> dict:
        return {"artefact": artefact, "created_utc": created, "config_hash": config, "data_hash": data,
                "same_inputs": (config, data) == current}

    rows = [row("this export (study results recomputed)", manifest["created_utc"], *current)]
    report = _json(REPORT_DIR / "run_manifest.json")
    if report:
        rows.append(row("main report (gate verdicts, sensitivities)", report.get("created_utc"), report.get("config_hash"),
                        report.get("data_hash")))
    ext = (_json(REPORT_DIR / "extensions_results.json") or {}).get("manifest")
    if ext:
        rows.append(row("extensions report tables", ext.get("created_utc"), ext.get("config_hash"), ext.get("data_hash")))
    if golden:
        rows.append(row("frozen golden results", golden["frozen_utc"], golden["config_hash_frozen"], golden["data_hash_frozen"]))
    exp = _json(ROOT / "paper" / "expectations_EqualWeight.json")
    if exp:
        rows.append(row("paper-book expectation bands", exp.get("frozen_utc"), exp.get("config_hash"), exp.get("data_hash")))
    return rows


def collect(md: MarketData, spec: Spec = SPEC, junit: Path = REPORT_DIR / "junit.xml", n_boot: int = 2000,
            check_golden: bool = True) -> dict:
    """Everything the dashboard shows. ``n_boot`` matches the report's default so both give the same bootstrap figures."""
    splits = study.make_splits(md, spec)
    base = study.run_baselines(md, splits, spec)
    strat = study.run_strategies(md, splits, spec, overlay=True)
    diag = study.run_strategies(md, splits, spec, overlay=False)
    runs = {**base, **strat, **diag}
    kinds = {**{k: "baseline" for k in base}, **{k: "strategy" for k in strat}, **{k: "diagnostic" for k in diag}}
    first = next(iter(runs.values())).pre.nav
    weeks = [d.strftime("%Y-%m-%d") for d in first.resample("W-FRI").last().dropna().index]
    nav = {name: {"kind": kinds[name], "pre": _weekly(r.pre.nav), "post": _weekly(r.post.nav),
                  "dd_post": [round(float(v), 4) for v in metrics.drawdown_series(r.post.nav).resample("W-FRI").last().dropna()]}
           for name, r in runs.items()}

    g0 = run_gate0(md, spec)
    regimes = study.regime_series(md, spec).loc[splits[0].exec_date:spec.study_end]
    reg_tbls = study.regime_tables({**base, **strat}, study.regime_series(md, spec))
    reg_rows = []
    for name, t in reg_tbls.items():
        for reg, row in t.iterrows():
            reg_rows.append({"run": name, "regime": reg, "days": int(row["days"]), "sharpe": float(row["sharpe"]),
                             "ann_return": float(row["ann_return"]), "mdd_concat": float(row["mdd_concat"])})
    bench = base[study.PRIMARY_BENCHMARK]
    tests_post = study.phase2_tests(strat, bench, spec, taxed=True, n_boot=n_boot)
    tests_diag = study.phase2_tests(diag, bench, spec, taxed=True, n_boot=n_boot)
    annual = study.annual_returns_table(runs)
    ew = base["EqualWeight"]
    margins = [margin_interval(ew, base[b], spec, n_boot) for b in (study.PRIMARY_BENCHMARK, CLASSIC_BENCHMARK) if b in base]
    edge = rolling_edge({"EqualWeight": ew, **strat}, bench)
    weights = weight_paths(md, splits, {**strat, **diag}, spec)
    golden = golden_check(md, spec, base, strat, diag) if check_golden else None

    book = _json(ROOT / "paper" / "book.json")
    marks = sorted((book or {}).get("marks", {}).items())
    decisions = []
    dec_dirs = [ROOT / "paper" / "decisions", ROOT / "paper" / "live" / "decisions", ROOT / "paper" / "demo" / "decisions"]
    for p in sorted(q for dd in dec_dirs for q in dd.glob("*.json")):
        d = json.loads(p.read_text())
        decisions.append({
            "file": str(p.relative_to(ROOT)), "run_id": d["run_id"], "strategy": d["strategy"],
            "kind": d.get("kind", "rebalance"), "signal_date": d["signal_date"], "exec_date": d["exec_date"],
            "status": d["status"], "regime": d.get("regime"), "policy": d.get("policy"),
            "n_orders": len(d["risk"]["orders"]), "n_fills": len(d.get("fills") or []),
            "health": d["health"]["status"], "risk": d["risk"]["status"],
            "flags": [c["name"] for c in d["risk"]["checks"] if c["status"] != "pass"], "notes": d.get("notes", []),
        })
    manifest = audit.run_manifest("webdash", spec)
    annual_json = {c: [None if pd.isna(v) else round(float(v), 4) for v in annual[c]] for c in annual.columns}
    book_json = None
    if book:
        bands = book_bands(marks)
        book_json = {"name": book.get("name"), "as_of": book.get("as_of"), "initial_cash": book.get("initial_cash"),
                     "dates": [k for k, _ in marks], "equity": [round(float(v), 2) for _, v in marks],
                     "drawdown": bands["drawdown"], "monthly": bands["monthly"],
                     "fills": len(book.get("fills", [])), "realized_by_year": book.get("realized_by_year"),
                     "tax_liability_accrued": book.get("tax_liability_accrued")}
    ext_names = ("ext1_policy_ladder", "ext1_gate1_margins", "ext1_sensitivities", "ext2_power", "ext2_hac",
                 "ext2_dsr12", "ext2_rw12", "ext2_rw6", "ext3_capacity_curve", "ext3_capacity")
    extensions: dict[str, Any] = {n.split("_", 1)[1]: _csv(n) for n in ext_names}
    extensions["results"] = _json(REPORT_DIR / "extensions_results.json")
    extensions["family_alpha"] = design.RW_ALPHA
    rolling = g0.rolling_avg.resample("W-FRI").last().dropna()
    return {
        "meta": {"generated_utc": manifest["created_utc"], "git": manifest["git"], "config_hash": manifest["config_hash"],
                 "data_hash": manifest["data_hash"], "data_through": str(md.index[-1].date()),
                 "oos": [str(splits[0].exec_date.date()), spec.study_end], "n_rebalances": len(splits),
                 "repo": "https://github.com/SamenSHossain/VanEck", "branch": manifest["git"].get("branch"),
                 "universe": list(UNIVERSE),
                 "config": {"tax_rate": spec.tax_rate, "lot_method": spec.lot_method, "train_years": spec.train_years,
                            "wash_sale_days": spec.wash_sale_days, "holdout_start": spec.holdout_start,
                            "cash_yield_mode": spec.cash_yield_mode, "w_min": spec.w_min, "w_max": spec.w_max,
                            "signal_lookback": spec.signal_lookback}},
        "gates": _json(REPORT_DIR / "gates.json"),
        "gate0": {"corr": {"tickers": list(g0.corr_full.index), "matrix": g0.corr_full.round(3).values.tolist()},
                  "checks": _records(g0.summary_table(), "check"), "clusters": _records(g0.clusters, "ticker"),
                  "rolling_dates": [d.strftime("%Y-%m-%d") for d in rolling.index],
                  "rolling_avg": [round(float(v), 4) for v in rolling.to_numpy()], "threshold": spec.gate0_avg_corr_max},
        "gate1": {"checks": _records(study.gate1(base, spec), "criterion"), "summary": _records(study.summary_table(base)),
                  "margins": margins, "rolling_edge": edge, "edge_window_days": EDGE_WINDOW,
                  "sensitivities": _csv("sensitivities")},
        "phase2": {"summary": _records(study.summary_table({**strat, **diag})),
                   "tests_post": _records(tests_post, "strategy"), "tests_diag": _records(tests_diag, "strategy"),
                   "regime_share": regimes.value_counts(normalize=True).round(4).to_dict(), "regime_rows": reg_rows,
                   "episodes": _records(episodes(regimes).query("days >= 10"), "i")},
        "annual": {"years": [int(y) for y in annual.index], "returns": annual_json},
        "nav": {"weeks": weeks, "series": nav},
        "weights": weights,
        "extensions": extensions,
        "operations": {"book": book_json, "decisions": decisions,
                       "monitor": _json(REPORT_DIR / "monitor_EqualWeight.json"), "health": _json(REPORT_DIR / "health.json"),
                       "expectations": _json(ROOT / "paper" / "expectations_EqualWeight.json"), "timeline": _store_timeline(),
                       "crossval": {k: _csv(f"crossval_{k}") for k in ("macro", "peers", "corporate_actions")}},
        "tests": _junit(junit),
        "golden": golden,
        "provenance": provenance(manifest, golden),
    }


_YN = {True: "yes", False: "no"}
_PF: dict[Any, str] = {True: "pass", False: "fail"}


def _words(recs: list[dict]) -> list[dict]:
    """Booleans become words, so a table cell on the dashboard shows the stored value as it is."""
    return [{k: (_YN[v] if isinstance(v, bool) else v) for k, v in r.items()} for r in recs]


def _rows(table: str, recs: list[dict] | None) -> list[dict]:
    return [{"table": table, **r} for r in _words(recs or [])]


def _kv(table: str, d: dict | None) -> list[dict]:
    return [{"table": table, "key": k, "value": (_YN[v] if isinstance(v, bool) else v)} for k, v in (d or {}).items()]


def _drop(recs: list[dict], *cols: str) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in cols} for r in recs]


def tables(payload: dict) -> dict[str, list[dict]]:
    """Reshape ``collect``'s document into flat row tables, one JSON file each.

    Every row of the first five carries a ``table`` column; the dashboard page splits a file into its tables with
    ``dash.calc``. ``weights`` is one table: a row per strategy and rebalance, a column per holding.
    """
    m, g = payload["meta"], payload["gates"] or {}
    g0, g1, p2, an = payload["gate0"], payload["gate1"], payload["phase2"], payload["annual"]
    study_rows: list[dict] = []
    study_rows += _kv("meta", {"data_through": m["data_through"], "oos_start": m["oos"][0], "oos_end": m["oos"][1],
                               "n_rebalances": m["n_rebalances"], "generated_utc": m["generated_utc"],
                               "git_sha": (m["git"].get("sha") or "")[:12], "branch": m["branch"],
                               "config_hash": m["config_hash"], "data_hash": m["data_hash"], "repo": m["repo"],
                               "universe": ", ".join(m["universe"]), "n_funds": len(m["universe"])})
    study_rows += _kv("gates", {"gate0": _PF.get(g.get("gate0"), "unknown"), "gate1": _PF.get(g.get("gate1"), "unknown"),
                                "gate2": _PF.get(g.get("gate2"), "unknown"),
                                "gate2_winners": ", ".join(g.get("gate2_winners") or []) or "none"})
    study_rows += _kv("config", {**(m.get("config") or {}), "edge_window_days": g1.get("edge_window_days")})
    study_rows += _rows("gate0_checks", g0["checks"]) + _rows("gate0_clusters", g0["clusters"])
    tick = g0["corr"]["tickers"]
    study_rows += [{"table": "gate0_corr", "a": a, "b": b, "rho": g0["corr"]["matrix"][i][j]}
                   for i, a in enumerate(tick) for j, b in enumerate(tick)]
    study_rows += [{"table": "gate0_rolling", "date": d, "avg": v} for d, v in zip(g0["rolling_dates"], g0["rolling_avg"])]
    study_rows += _kv("gate0_params", {"threshold": g0["threshold"]})
    study_rows += _rows("gate1_checks", g1["checks"]) + _rows("gate1_summary", g1["summary"])
    study_rows += _rows("gate1_margin", g1.get("margins"))
    study_rows += _rows("rolling_edge", g1.get("rolling_edge"))
    keep = ("variant", "name", "sharpe_pre", "sharpe_post", "cagr_post", "mdd_post", "turnover_pre", "tax_pct_yr")
    study_rows += _rows("sensitivities", [{k: r.get(k) for k in keep} for r in g1.get("sensitivities") or []])
    study_rows += _rows("phase2_summary", p2["summary"])
    study_rows += _rows("phase2_tests", [{"family": "primary", **x} for x in p2["tests_post"]]
                        + [{"family": "diagnostic", **x} for x in p2["tests_diag"]])
    study_rows += _rows("regime_rows", p2["regime_rows"])
    study_rows += [{"table": "regime_share", "regime": k, "share": v} for k, v in p2["regime_share"].items()]
    study_rows += _rows("episodes", [{**e, "start": str(e["start"])[:10], "end": str(e["end"])[:10]} for e in p2["episodes"]])
    study_rows += [{"table": "annual", "year": y, "run": run, "ret": v}
                   for run, vals in an["returns"].items() for y, v in zip(an["years"], vals) if v is not None]

    nav = payload["nav"]
    nav_rows = [{"week": w, "run": run, "kind": s["kind"], "pre": s["pre"][i], "post": s["post"][i],
                 "dd": s["dd_post"][i] if i < len(s["dd_post"]) else None}
                for run, s in nav["series"].items() for i, w in enumerate(nav["weeks"]) if i < len(s["pre"])]

    ex = payload["extensions"]
    ext_rows: list[dict] = []
    for name in ("policy_ladder", "gate1_margins", "sensitivities", "capacity_curve", "capacity"):
        ext_rows += _rows(name, ex.get(name))
    for name in ("power", "hac", "dsr12", "rw12", "rw6"):
        ext_rows += _rows(name, [{("strategy" if k == "Unnamed: 0" else k): v for k, v in r.items()} for r in ex.get(name) or []])
    res = ex.get("results") or {}
    ext_rows += _kv("results", {"gate1_pass_any_policy": res.get("gate1_pass_any_policy"),
                                "white_rc_p_primary": res.get("white_rc_p_primary"), "white_rc_p_all": res.get("white_rc_p_all"),
                                "romano_wolf_rejections": ", ".join(res.get("romano_wolf_rejections") or []) or "none",
                                "family_alpha": ex.get("family_alpha"),
                                **{f"margin_{k}": v for k, v in (res.get("gate1_margin_by_policy") or {}).items()},
                                **{f"capacity_{k}": v for k, v in (res.get("capacity_aum") or {}).items()}})

    op = payload["operations"]
    b = op["book"] or {}
    mon = op["monitor"] or {}
    h = op["health"] or {}
    cv = op["crossval"] or {}
    op_rows: list[dict] = []
    dates, equity = b.get("dates", []), b.get("equity", [])
    op_rows += [{"table": "book", "date": d, "equity": e, "dd": x}
                for d, e, x in zip(dates, equity, b.get("drawdown") or [None] * len(dates))]
    op_rows += _rows("book_monthly", b.get("monthly"))
    op_rows += _kv("book_meta", {"name": b.get("name"), "as_of": b.get("as_of"), "initial_cash": b.get("initial_cash"),
                                 "fills": b.get("fills"), "tax_liability_accrued": b.get("tax_liability_accrued"),
                                 **{f"realized_{k}": v for k, v in (b.get("realized_by_year") or {}).items()}})
    op_rows += _rows("decisions", [{**_drop([d], "flags", "notes", "health", "risk")[0], "flags": ", ".join(d["flags"]) or "none",
                                    "notes": " | ".join(d["notes"])} for d in op["decisions"]])
    op_rows += _kv("monitor", {"status": mon.get("status"), "as_of": mon.get("as_of"), **(mon.get("realized") or {})})
    op_rows += _kv("reconciliation", mon.get("reconciliation"))
    op_rows += [{"table": "alerts", "i": i, "text": a} for i, a in enumerate(mon.get("alerts") or [])]
    op_rows += _rows("retirement", (mon.get("retirement") or {}).get("rules"))
    op_rows += _kv("retirement_meta", {"status": (mon.get("retirement") or {}).get("status")})
    op_rows += _rows("health", h.get("checks")) + _kv("health_meta", {"as_of": h.get("as_of"), "status": h.get("status")})
    op_rows += _rows("crossval_macro", _drop(cv.get("macro") or [], "Unnamed: 0"))
    op_rows += _rows("crossval_peers", _drop(cv.get("peers") or [], "Unnamed: 0"))
    op_rows += _rows("crossval_ca", cv.get("corporate_actions"))
    op_rows += _rows("timeline", [{"i": i, **r} for i, r in enumerate(op["timeline"] or [])])
    op_rows += _kv("expectations", {k: v for k, v in (op["expectations"] or {}).items() if k != "window"})

    ts = payload["tests"]
    test_rows: list[dict] = _kv("summary", {k: ts.get(k) for k in ("total", "passed", "failed", "skipped", "time")})
    test_rows += _rows("files", _drop(ts.get("files") or [], "tests"))
    test_rows += _rows("cases", [{"file": f["file"], **c} for f in ts.get("files") or [] for c in f["tests"]])
    gold = payload.get("golden") or {}
    test_rows += _kv("golden", {k: v for k, v in gold.items() if k not in ("files", "versions")})
    test_rows += _rows("golden_files", gold.get("files"))
    test_rows += _rows("versions", gold.get("versions"))
    test_rows += _rows("provenance", payload.get("provenance"))
    return {"study_tables": study_rows, "nav_weekly": nav_rows, "extensions": ext_rows, "operations": op_rows,
            "tests": test_rows, "weights": payload.get("weights") or []}


def write(md: MarketData, out: Path = REPORT_DIR / "dashboard_data.json", spec: Spec = SPEC,
          junit: Path = REPORT_DIR / "junit.xml", n_boot: int = 2000, check_golden: bool = True) -> list[Path]:
    """Write the full document and, beside it in ``dashboard/``, the row tables the dashboard page reads."""
    payload = collect(md, spec, junit, n_boot=n_boot, check_golden=check_golden)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, default=str))
    folder = out.parent / "dashboard"
    folder.mkdir(exist_ok=True)
    written = [out]
    for name, rows in tables(payload).items():
        path = folder / f"{name}.json"
        path.write_text(json.dumps(rows, separators=(",", ":"), default=str))
        written.append(path)
    return written
