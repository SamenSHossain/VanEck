"""SPEC section 17: pre-registered retirement rules for a paper or live portfolio.

Expectation bands are frozen once per deployment from the research engine over
the main study window and never updated with live data.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import audit
from .config import SPEC, Spec
from .costs import CostModel
from .data import MarketData
from .execution import ExecutionPolicy
from .portfolio import build_targets, simulate
from .store import STATE_DIR
from .study import TRADEABLES, make_splits

RULES = ("R1_monthly_returns", "R2_drawdown", "R3_rolling_sharpe", "R4_engine_agreement", "R5_implementability")


def _slug(name: str) -> str:
    from .live import slug
    return slug(name)


# --------------------------------------------------------------------------- #
# Expectation bands
# --------------------------------------------------------------------------- #
def monthly_returns(curve: pd.Series) -> pd.Series:
    """Calendar-month returns; the first month counts when the series starts within its first 3 business days."""
    m = curve.resample("ME").last()
    r = m.pct_change()
    first = curve.index[0]
    month_start = pd.bdate_range(first.replace(day=1), periods=3)
    if first <= month_start[-1] and len(m) > 0:
        r.iloc[0] = m.iloc[0] / curve.iloc[0] - 1.0
    return r.dropna()


def _monthly_excess(nav: pd.Series, rf: pd.Series) -> pd.Series:
    m = monthly_returns(nav)
    cash_m = rf.reindex(nav.index).ffill().resample("ME").mean() / 12.0
    return (m - cash_m.reindex(m.index)).dropna()


def rolling_sharpe_12m(nav: pd.Series, rf: pd.Series) -> pd.Series:
    ex = _monthly_excess(nav, rf)
    roll = ex.rolling(12)
    return (roll.mean() / roll.std(ddof=1) * np.sqrt(12)).dropna()


def compute_expectations(md: MarketData, strategy: str, spec: Spec = SPEC, policy: ExecutionPolicy | None = None) -> dict:
    from .live import strategy_registry
    fn, _ = strategy_registry(spec)[strategy]
    splits = make_splits(md, spec)
    tgt = build_targets(md, splits, fn, TRADEABLES)
    sim = simulate(md, tgt, spec=spec, taxed=False, cost_model=CostModel(spec), end=spec.study_end, name=strategy, policy=policy)
    monthly = monthly_returns(sim.nav)
    rs = rolling_sharpe_12m(sim.nav, sim.rf)
    dd = float((sim.nav / sim.nav.cummax() - 1.0).min())
    return {
        "strategy": strategy, "policy": policy.name if policy else "P0 calendar (legacy)",
        "window": [str(sim.nav.index[0].date()), str(sim.nav.index[-1].date())], "n_months": int(len(monthly)),
        "monthly_return_p5": float(monthly.quantile(0.05)), "monthly_return_median": float(monthly.median()),
        "rolling_sharpe_12m_p5": float(rs.quantile(0.05)) if len(rs) else float("nan"),
        "max_drawdown": dd, "drawdown_retire_level": 1.25 * dd,
        "frozen_utc": audit.utc_now(), "config_hash": audit.config_hash(spec), "data_hash": audit.data_hash(),
    }


def expectations_path(strategy: str, out_dir: Path) -> Path:
    return out_dir / f"expectations_{_slug(strategy)}.json"


def freeze_expectations(md: MarketData, strategy: str, out_dir: Path, spec: Spec = SPEC,
                        policy: ExecutionPolicy | None = None) -> dict:
    exp = compute_expectations(md, strategy, spec, policy)
    out_dir.mkdir(parents=True, exist_ok=True)
    expectations_path(strategy, out_dir).write_text(json.dumps(exp, indent=2))
    return exp


def load_expectations(strategy: str, out_dir: Path) -> dict | None:
    p = expectations_path(strategy, out_dir)
    return json.loads(p.read_text()) if p.exists() else None


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
@dataclass
class RuleResult:
    rule: str
    status: str          # ok | review | retire | halt | insufficient
    value: object = None
    threshold: object = None
    detail: str = ""


_RANK = {"ok": 0, "insufficient": 0, "review": 1, "halt": 2, "retire": 3}


def evaluate(curve: pd.Series, rf: pd.Series, expectations: dict, monitor_history: list[dict] | None = None) -> list[RuleResult]:
    """``curve``: daily marks of the book; ``monitor_history``: recent monitor reports, newest first."""
    out: list[RuleResult] = []
    monthly = monthly_returns(curve)
    # R1
    p5 = expectations["monthly_return_p5"]
    if len(monthly) >= 6:
        last6 = monthly.tail(6)
        n_bad = int((last6 < p5).sum())
        out.append(RuleResult("R1_monthly_returns", "retire" if n_bad >= 3 else "ok", n_bad, "3 of 6 below p5",
                              f"{n_bad} of the last 6 months below the backtest 5th percentile ({p5:.2%})"))
    else:
        out.append(RuleResult("R1_monthly_returns", "insufficient", len(monthly), 6, "needs 6 full months of marks"))
    # R2
    dd = float((curve / curve.cummax() - 1.0).min())
    level = expectations["drawdown_retire_level"]
    out.append(RuleResult("R2_drawdown", "retire" if dd < level else "ok", round(dd, 4), round(level, 4),
                          f"worst drawdown {dd:.1%} vs retire level {level:.1%} "
                          f"(1.25 x backtest {expectations['max_drawdown']:.1%})"))
    # R3
    rs = rolling_sharpe_12m(curve, rf)
    p5s = expectations.get("rolling_sharpe_12m_p5")
    if len(rs) >= 3 and p5s is not None and p5s == p5s:
        last3 = rs.tail(3)
        bad = bool((last3 < p5s).all())
        out.append(RuleResult("R3_rolling_sharpe", "review" if bad else "ok", round(float(last3.iloc[-1]), 3), round(p5s, 3),
                              f"last 3 rolling 12m Sharpe values {[round(v, 2) for v in last3]} vs p5 {p5s:.2f}"))
    else:
        out.append(RuleResult("R3_rolling_sharpe", "insufficient", len(rs), 3, "needs 14 months of marks"))
    # R4 / R5 from monitor history
    hist = monitor_history or []

    def _has(rep: dict, needle: str) -> bool:
        return any(needle in a for a in rep.get("alerts", []))

    if len(hist) >= 2:
        n_eng = int(sum(_has(r, "engine disagreement") for r in hist[:2]))
        eng = n_eng == 2
        out.append(RuleResult("R4_engine_agreement", "halt" if eng else "ok", n_eng, "2 consecutive",
                              "engine-agreement alert on the last two monitor runs" if eng else "no persistent engine alert"))
    else:
        out.append(RuleResult("R4_engine_agreement", "insufficient", len(hist), 2, "needs two monitor runs"))
    if len(hist) >= 3:
        n_imp = int(sum(_has(r, "implementability gap") for r in hist[:3]))
        imp = n_imp == 3
        detail = "capped-intended gap beyond tolerance on the last three monitor runs" if imp else "no persistent gap"
        out.append(RuleResult("R5_implementability", "review" if imp else "ok", n_imp, "3 consecutive", detail))
    else:
        out.append(RuleResult("R5_implementability", "insufficient", len(hist), 3, "needs three monitor runs"))
    return out


def overall(results: list[RuleResult]) -> str:
    return max((r.status for r in results), key=lambda s: _RANK[s], default="ok")


def results_to_dict(results: list[RuleResult]) -> dict:
    return {"status": overall(results), "rules": [asdict(r) for r in results]}


# --------------------------------------------------------------------------- #
# Flags
# --------------------------------------------------------------------------- #
def flag_path(strategy: str, state_dir: Path = STATE_DIR) -> Path:
    return state_dir / f"RETIRED_{_slug(strategy)}.json"


def is_retired(strategy: str, state_dir: Path = STATE_DIR) -> dict | None:
    p = flag_path(strategy, state_dir)
    return json.loads(p.read_text()) if p.exists() else None


def set_flag(strategy: str, status: str, results: list[RuleResult], state_dir: Path = STATE_DIR) -> dict:
    rec = {"strategy": strategy, "status": status, "at": audit.utc_now(),
           "rules": [asdict(r) for r in results if r.status in ("retire", "halt")]}
    state_dir.mkdir(parents=True, exist_ok=True)
    flag_path(strategy, state_dir).write_text(json.dumps(rec, indent=2))
    return rec


def clear_flag(strategy: str, by: str, reason: str, state_dir: Path = STATE_DIR) -> None:
    p = flag_path(strategy, state_dir)
    if p.exists():
        rec = json.loads(p.read_text())
        rec.update({"cleared_by": by, "cleared_at": audit.utc_now(), "clear_reason": reason})
        with (state_dir / "retire.log").open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        p.unlink()
