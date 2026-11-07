"""Phase 2.2 signals (locked).

Each signal returns *pinned* weights for favoured funds and *bounds* for the
remaining funds; the optimizer then allocates the residual budget inside those
bounds.  This is how "3 signals x 2 optimizers" compose (see docs/SPEC.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import SPEC, Spec
from .data import MarketData


@dataclass
class SignalOutput:
    pinned: dict[str, float]
    bounds: dict[str, tuple[float, float]]
    scores: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


def _prices(win: MarketData, tickers, lookback: int) -> pd.DataFrame:
    px = win.adj_close[list(tickers)].dropna(how="any")
    if len(px) < lookback + 1:
        raise ValueError(f"need {lookback + 1} prices, have {len(px)}")
    return px


def momentum_scores(win: MarketData, tickers, spec: Spec = SPEC) -> pd.Series:
    px = _prices(win, tickers, spec.signal_lookback)
    return (px.iloc[-1] / px.iloc[-1 - spec.signal_lookback] - 1.0).rename("momentum_60d")


def drift_scores(win: MarketData, tickers, spec: Spec = SPEC) -> pd.Series:
    px = _prices(win, tickers, spec.signal_lookback)
    r = px.pct_change(fill_method=None).iloc[-spec.signal_lookback:]
    return (r > 0).mean().rename("drift_60d")


def signal_none(win: MarketData, tickers, spec: Spec = SPEC) -> SignalOutput:
    return SignalOutput(pinned={}, bounds={t: (spec.w_min, spec.w_max) for t in tickers})


def signal_momentum(win: MarketData, tickers, spec: Spec = SPEC) -> SignalOutput:
    s = momentum_scores(win, tickers, spec)
    top = list(s.sort_values(ascending=False).index[: spec.top_n])
    pinned = {t: spec.w_max for t in top}
    bounds = {t: (spec.w_min, spec.other_cap) for t in tickers if t not in pinned}
    return SignalOutput(pinned=pinned, bounds=bounds, scores=s)


def signal_drift(win: MarketData, tickers, spec: Spec = SPEC) -> SignalOutput:
    s = drift_scores(win, tickers, spec)
    top = s.sort_values(ascending=False).index[: spec.top_n]
    pinned = {}
    for t in top:
        level = float(np.clip((s[t] - 0.5) / 0.5, 0.0, 1.0))
        pinned[t] = spec.drift_lo + (spec.drift_hi - spec.drift_lo) * level
    bounds = {t: (spec.w_min, spec.other_cap) for t in tickers if t not in pinned}
    return SignalOutput(pinned=pinned, bounds=bounds, scores=s)


SIGNALS = {"None": signal_none, "Momentum": signal_momentum, "Drift": signal_drift}
