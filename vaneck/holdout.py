"""The 2025+ hold-out: sealed until one logged decision opens it, then frozen (SPEC section 19).

Nothing in the study, the extensions or the dashboard measures performance after ``spec.study_end``.
``python -m vaneck validate`` reports the seal. ``validate --unseal --by NAME --reason TEXT`` writes the record,
runs the pre-registered evaluation once and stores its tables; every later call only shows what was stored.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import audit, study
from .calendar import Split
from .config import REPORT_DIR, SPEC, Spec
from .data import MarketData

HOLDOUT_DIR = REPORT_DIR / "holdout"
RECORD = "unsealed.json"
RESULTS = "results.json"
RECORD_FIELDS = ("opened_by", "opened_utc", "reason", "evaluated_through", "config_hash", "data_hash", "git_sha")


class AlreadyOpened(RuntimeError):
    """The hold-out has been opened before; it is evaluated once."""


class HoldoutSealed(RuntimeError):
    """An operation would value positions inside the sealed hold-out."""


def is_sealed(folder: Path | None = None) -> bool:
    """True until the opening record exists. Reads one file name, never a price."""
    return not ((folder or HOLDOUT_DIR) / RECORD).exists()


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def _splits(md: MarketData, spec: Spec) -> list[Split]:
    start = pd.Timestamp(spec.holdout_start)
    return [s for s in study.make_splits(md, spec, start=spec.fund_start, end=md.index[-1]) if s.exec_date >= start]


def window(md: MarketData, spec: Spec = SPEC) -> dict:
    """Calendar facts about the hold-out. Only dates are read here, never a price after the study's end."""
    days = md.index[md.index >= pd.Timestamp(spec.holdout_start)]
    return {"start": spec.holdout_start, "data_through": str(md.index[-1].date()), "trading_days": int(len(days)),
            "rebalances": len(_splits(md, spec))}


def status(md: MarketData, spec: Spec = SPEC, folder: Path = HOLDOUT_DIR) -> dict:
    """Sealed or opened, the waiting window, and the opening record if there is one."""
    record = _read(folder / RECORD)
    out: dict = {"state": "opened" if record else "sealed", **window(md, spec)}
    for key in RECORD_FIELDS:
        out[key] = (record or {}).get(key)
    out["results_stored"] = (folder / RESULTS).exists()
    return out


def stored(folder: Path = HOLDOUT_DIR) -> dict | None:
    """The frozen results, if the hold-out was opened and evaluated."""
    return _read(folder / RESULTS)


def evaluate(md: MarketData, spec: Spec = SPEC, n_boot: int = 2000) -> dict:
    """The pre-registered evaluation: baselines and the six locked rules over the hold-out window.

    Reads hold-out prices. Call it only through :func:`unseal` or :func:`complete`.
    """
    splits = _splits(md, spec)
    if not splits:
        raise ValueError("no rebalance falls inside the hold-out window")
    end = md.index[-1]
    base = study.run_baselines(md, splits, spec, end=end)
    strat = study.run_strategies(md, splits, spec, end=end, overlay=True)
    summary = pd.concat([study.summary_table(base), study.summary_table(strat)]).reset_index()
    ew = base["EqualWeight"]
    margins = [study.margin_interval(ew, base[b], spec, n_boot) for b in (study.PRIMARY_BENCHMARK, study.CLASSIC_BENCHMARK)
               if b in base]
    return {"window": [str(splits[0].exec_date.date()), str(end.date())], "n_rebalances": len(splits),
            "summary": json.loads(summary.to_json(orient="records")), "margins": margins}


def unseal(md: MarketData, by: str, reason: str, spec: Spec = SPEC, folder: Path = HOLDOUT_DIR, n_boot: int = 2000) -> dict:
    """Open the hold-out: write who, when and why, then evaluate once and freeze the tables.

    The record is written before any hold-out number is computed, so a look can never go unlogged.
    """
    if not (by or "").strip() or not (reason or "").strip():
        raise ValueError("opening the hold-out needs a name (--by) and a reason (--reason)")
    record_path = folder / RECORD
    existing = _read(record_path)
    if existing is not None:
        raise AlreadyOpened(f"the hold-out was opened by {existing['opened_by']}; its results are frozen in {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    record = {"opened_by": by.strip(), "opened_utc": audit.utc_now(), "reason": reason.strip(),
              "holdout_start": spec.holdout_start, "evaluated_through": str(md.index[-1].date()),
              "config_hash": audit.config_hash(spec), "data_hash": audit.data_hash(), "git_sha": audit.git_info().get("sha"),
              "n_boot": int(n_boot)}
    record_path.write_text(json.dumps(record, indent=2))
    return {"record": record, "results": complete(md, spec, folder)}


def complete(md: MarketData, spec: Spec = SPEC, folder: Path = HOLDOUT_DIR) -> dict:
    """Return the frozen results, computing them if an opening was logged but its evaluation never finished.

    The evaluation always stops at the date the record names, so data that arrives later cannot change it.
    """
    record = _read(folder / RECORD)
    if record is None:
        raise RuntimeError("the hold-out is sealed")
    results = stored(folder)
    if results is None:
        results = evaluate(md.window(end=record["evaluated_through"]), spec, int(record.get("n_boot", 2000)))
        (folder / RESULTS).write_text(json.dumps(results, indent=2, default=str))
    return results
