"""Cross-validation of the primary (Yahoo) data against independent evidence.

Three layers, each a list of health ``Check`` objects:
1. macro series against FRED (identical underlying data -> tight tolerances);
2. each ETF against its cached peer/substitute (catches bad prints and
   adjustment errors in either series);
3. corporate-actions audit: Yahoo's own adjustment factors must match its
   recorded distributions day by day.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import SPEC, SUBSTITUTES, UNIVERSE, Spec
from .data import MarketData
from .health import BLOCK, OK, WARN, Check, HealthReport


@dataclass(frozen=True)
class CrossvalLimits:
    macro_tol: dict = None  # type: ignore[assignment]
    macro_share_warn: float = 0.01       # share of overlapping days beyond tolerance
    macro_share_block: float = 0.05
    peer_corr_min: float = 0.60          # daily-return correlation over the window
    peer_corr_min_vol: float = 0.003     # apply the correlation test only when both legs move at least this much a day
    peer_jump_warn: float = 0.10         # one-day return divergence
    peer_jump_block: float = 0.25
    peer_window: int = 250
    ca_min_jump: float = 5e-5            # adjustment-factor change that counts as a distribution
    ca_rel_tol: float = 0.05             # implied vs recorded dividend, relative
    ca_abs_tol: float = 0.02             # dollars
    div_yield_warn: float = 0.10         # one distribution above 10% of price

    def __post_init__(self):
        if self.macro_tol is None:
            object.__setattr__(self, "macro_tol", {"vix": 0.05, "tnx": 0.05, "irx": 0.06, "spx": 0.001})


def compare_macro(md: MarketData, fred: dict[str, pd.Series], limits: CrossvalLimits = CrossvalLimits(),
                  lookback_days: int = 750) -> tuple[list[Check], pd.DataFrame]:
    checks, rows = [], []
    for name, other in fred.items():
        if name not in md.macro.columns:
            continue
        ours = md.macro[name].dropna().tail(lookback_days)
        joined = pd.concat([ours.rename("yahoo"), other.rename("fred")], axis=1, join="inner").dropna()
        if len(joined) < 20:
            checks.append(Check(f"fred_{name}", WARN, len(joined), 20, "too little overlap to compare"))
            continue
        tol = limits.macro_tol[name]
        diff = (joined["yahoo"] - joined["fred"])
        if name == "spx":
            diff = diff / joined["fred"]
        beyond = (diff.abs() > tol)
        share = float(beyond.mean())
        last_bad = bool(beyond.iloc[-1])
        status = BLOCK if (last_bad or share > limits.macro_share_block) else WARN if share > limits.macro_share_warn else OK
        detail = (f"overlap {len(joined)} days to {joined.index[-1].date()}; max |diff| {diff.abs().max():.4g}; "
                  f"{share:.1%} of days beyond {tol}" + ("; LAST DAY DISAGREES" if last_bad else ""))
        checks.append(Check(f"fred_{name}", status, round(float(diff.abs().iloc[-1]), 4), tol, detail))
        rows.append({"series": name, "overlap_days": len(joined), "last_common": joined.index[-1].date(),
                     "yahoo_last": joined["yahoo"].iloc[-1], "fred_last": joined["fred"].iloc[-1],
                     "max_abs_diff": float(diff.abs().max()), "share_beyond_tol": share, "status": status})
    return checks, pd.DataFrame(rows)


def peer_divergence(md: MarketData, pairs: dict[str, str] | None = None, limits: CrossvalLimits = CrossvalLimits()) \
        -> tuple[list[Check], pd.DataFrame]:
    """Each fund's daily returns against its substitute's: large one-day gaps flag a bad series."""
    pairs = pairs or {k: v for k, v in SUBSTITUTES.items() if k in UNIVERSE}
    rets = md.returns()
    checks, rows = [], []
    worst_status = OK
    for a, b in pairs.items():
        if a not in rets.columns or b not in rets.columns:
            continue
        r = rets[[a, b]].dropna().tail(limits.peer_window)
        if len(r) < 60:
            continue
        corr = float(r[a].corr(r[b]))
        gap = (r[a] - r[b]).abs()
        max_gap = float(gap.max())
        vol_ok = min(float(r[a].std()), float(r[b].std())) >= limits.peer_corr_min_vol
        low_corr = vol_ok and corr < limits.peer_corr_min
        status = BLOCK if max_gap > limits.peer_jump_block else WARN if (max_gap > limits.peer_jump_warn or low_corr) else OK
        worst_status = max(worst_status, status, key=lambda s: {OK: 0, WARN: 1, BLOCK: 2}[s])
        rows.append({"fund": a, "peer": b, "days": len(r), "corr": corr, "max_one_day_gap": max_gap,
                     "gap_date": gap.idxmax().date(), "status": status})
    tbl = pd.DataFrame(rows)
    flagged = tbl[tbl["status"] != OK] if len(tbl) else tbl
    detail = "; ".join(f"{r.fund}/{r.peer} corr {r.corr:.2f} max gap {r.max_one_day_gap:.1%} on {r.gap_date}"
                       for r in flagged.itertuples()) if len(flagged) else f"{len(tbl)} pairs within limits"
    checks.append(Check("peer_divergence", worst_status, int(len(flagged)), 0, detail))
    return checks, tbl


def corporate_actions_audit(md: MarketData, tickers=None, limits: CrossvalLimits = CrossvalLimits()) \
        -> tuple[list[Check], pd.DataFrame]:
    """Adjustment-factor changes must coincide with recorded distributions, and match their size."""
    tickers = list(tickers or md.adj_close.columns)
    rows, checks = [], []
    for t in tickers:
        px = pd.concat([md.adj_close[t], md.close[t], md.distributions[t]], axis=1, keys=["adj", "close", "div"])
        px = px.dropna(subset=["adj", "close"])
        if len(px) < 10:
            continue
        ratio = px["adj"] / px["close"]
        f = ratio / ratio.shift(1)
        jump = (f - 1.0).abs() > limits.ca_min_jump
        implied = (px["close"].shift(1) * (1.0 - 1.0 / f)).where(jump, 0.0)
        recorded = px["div"].fillna(0.0)
        unmatched_jumps = int((jump & (recorded <= 0)).sum())
        unmatched_divs = int(((recorded > 0) & ~jump).sum())
        both = jump & (recorded > 0)
        mism = ((implied - recorded).abs() > np.maximum(limits.ca_rel_tol * recorded, limits.ca_abs_tol)) & both
        n_mism = int(mism.sum())
        yields = (recorded / px["close"].shift(1)).where(recorded > 0)
        max_yield = float(yields.max()) if yields.notna().any() else 0.0
        status = BLOCK if unmatched_jumps or n_mism else WARN if (unmatched_divs or max_yield > limits.div_yield_warn) else OK
        rows.append({"ticker": t, "distributions": int((recorded > 0).sum()), "factor_jumps": int(jump.sum()),
                     "unmatched_jumps": unmatched_jumps, "unmatched_distributions": unmatched_divs,
                     "size_mismatches": n_mism, "max_distribution_yield": max_yield,
                     "worst_date": str(mism[mism].index[-1].date()) if n_mism else "", "status": status})
    tbl = pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()
    bad = tbl[tbl["status"] == BLOCK] if len(tbl) else tbl
    warn = tbl[tbl["status"] == WARN] if len(tbl) else tbl
    status = BLOCK if len(bad) else WARN if len(warn) else OK
    detail = ("adjustment/distribution inconsistencies: " + ", ".join(bad.index)) if len(bad) else \
             ("review: " + ", ".join(warn.index)) if len(warn) else "factors and distributions agree for every ticker"
    checks.append(Check("corporate_actions", status, int(len(bad)), 0, detail))
    return checks, tbl


def crossval_report(md: MarketData, secondary: dict, spec: Spec = SPEC, limits: CrossvalLimits = CrossvalLimits()) \
        -> tuple[HealthReport, dict[str, pd.DataFrame]]:
    rep = HealthReport(as_of=str(md.index[-1].date()))
    tables: dict[str, pd.DataFrame] = {}
    c, tables["macro"] = compare_macro(md, secondary.get("fred", {}), limits)
    rep.checks += c
    if not secondary.get("fred"):
        rep.checks.append(Check("fred_available", WARN, 0, 1, "no FRED cache: run `python -m vaneck fetch --secondary`"))
    c, tables["peers"] = peer_divergence(md, limits=limits)
    rep.checks += c
    c, tables["corporate_actions"] = corporate_actions_audit(md, list(UNIVERSE) + ["SPY", "BND", "VGIT"], limits)
    rep.checks += c
    unavailable = [k for k, v in secondary.get("manifest", {}).items() if v.get("status") != "ok"]
    if unavailable:
        rep.checks.append(Check("secondary_providers", WARN, len(unavailable), 0, "unavailable: " + ", ".join(unavailable[:6])))
    return rep, tables
