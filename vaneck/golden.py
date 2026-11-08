"""Golden results: frozen key numbers that may only change together with a visible input change.

``python -m vaneck golden --update`` regenerates the files and records the
configuration hash and data hash they were produced from. The test suite fails
if either hash differs from the current inputs (regenerate and review the diff)
or if the numbers differ while the hashes match (an engine regression).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import audit, liquidity, study, taxaware
from .config import ROOT, SPEC, Spec
from .data import MarketData

GOLDEN_DIR = ROOT / "tests" / "golden"
SUMMARY_COLS = ["cagr_pre", "cagr_post", "sharpe_pre", "sharpe_post", "mdd_post", "turnover_pre", "cost_bps_yr", "tax_pct_yr"]
RTOL, ATOL = 1e-6, 1e-9


def compute(md: MarketData, spec: Spec = SPEC) -> dict[str, pd.DataFrame]:
    splits = study.make_splits(md, spec)
    base = study.run_baselines(md, splits, spec)
    strat = study.run_strategies(md, splits, spec, overlay=True)
    diag = study.run_strategies(md, splits, spec, overlay=False)
    ladder, _ = taxaware.run_ladder(md, splits, spec)
    curve, caps = liquidity.capacity_curve(md, splits, spec)
    return {
        "gate1_baselines": study.summary_table(base)[SUMMARY_COLS],
        "phase2_strategies": study.summary_table({**strat, **diag})[SUMMARY_COLS],
        "ext1_policy_ladder": ladder[["sharpe_pre", "sharpe_post", "tax_pct_yr", "turnover_pre"]],
        "ext3_capacity_curve": curve[["drag_bps_yr", "tracking_error_bps_yr"]],
    }


def write(golden: dict[str, pd.DataFrame], out: Path = GOLDEN_DIR, spec: Spec = SPEC) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    for name, df in golden.items():
        df.to_csv(out / f"{name}.csv")
    manifest = {"config_hash": audit.config_hash(spec), "data_hash": audit.data_hash(), "created_utc": audit.utc_now(),
                "files": sorted(golden), "rtol": RTOL, "atol": ATOL, "versions": audit.package_versions()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def load(out: Path = GOLDEN_DIR) -> tuple[dict, dict[str, pd.DataFrame]]:
    manifest = json.loads((out / "manifest.json").read_text())
    frames = {}
    for name in manifest["files"]:
        df = pd.read_csv(out / f"{name}.csv")
        n_index = 2 if name in ("ext1_policy_ladder", "ext3_capacity_curve") else 1
        frames[name] = df.set_index(list(df.columns[:n_index]))
    return manifest, frames


def inputs_changed(manifest: dict, spec: Spec = SPEC) -> list[str]:
    changed = []
    if manifest["config_hash"] != audit.config_hash(spec):
        changed.append(f"config_hash {manifest['config_hash']} -> {audit.config_hash(spec)}")
    if manifest["data_hash"] != audit.data_hash():
        changed.append(f"data_hash {manifest['data_hash']} -> {audit.data_hash()}")
    return changed


def compare(current: dict[str, pd.DataFrame], golden: dict[str, pd.DataFrame]) -> list[str]:
    problems = []
    for name, want in golden.items():
        got = current[name].copy()
        got.index = got.index.map(str) if got.index.nlevels == 1 else got.index
        want = want.copy()
        try:
            if got.index.nlevels > 1:
                want.index = pd.MultiIndex.from_tuples([tuple(_coerce(x) for x in t) for t in want.index], names=got.index.names)
                got.index = pd.MultiIndex.from_tuples([tuple(_coerce(x) for x in t) for t in got.index], names=got.index.names)
            got = got.loc[want.index, want.columns].astype(float)
            pd.testing.assert_frame_equal(got, want.astype(float), rtol=RTOL, atol=ATOL, check_names=False)
        except AssertionError as exc:
            problems.append(f"{name}: {str(exc).splitlines()[0]}")
        except KeyError as exc:
            problems.append(f"{name}: missing rows/columns {exc}")
    return problems


def _coerce(x):
    try:
        f = float(x)
        return f if not f.is_integer() else float(int(f))
    except (TypeError, ValueError):
        return str(x)
