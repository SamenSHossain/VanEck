"""Phase 2: the six locked strategies (3 signals x 2 optimizers) and the regime overlay."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .calendar import Split
from .config import SPEC, UNIVERSE, Spec
from .data import MarketData
from .optimizers import covariance, expected_returns, optimize
from .regimes import regime_at
from .signals import SIGNALS

OPTIMIZERS = ("MinVar", "MaxSharpe")


def strategy_grid(overlay: bool = True) -> list[tuple[str, str, bool]]:
    return [(s, o, overlay) for s in SIGNALS for o in OPTIMIZERS]


def strategy_name(signal: str, optimizer: str, overlay: bool) -> str:
    return f"{signal}x{optimizer}" + ("+Regime" if overlay else "")


def _water_fill(base: pd.Series, budget: float, floor: float) -> pd.Series:
    """Scale ``base`` to ``budget`` keeping every weight >= floor."""
    out = pd.Series(np.nan, index=base.index, dtype=float)
    fixed = pd.Series(False, index=base.index)
    for _ in range(len(base) + 1):
        remaining = budget - floor * fixed.sum()
        free = ~fixed
        tot = base[free].sum()
        prop = base[free] * (remaining / tot) if tot > 0 else pd.Series(remaining / free.sum(), index=base[free].index)
        out[free] = prop
        out[fixed] = floor
        below = free & (out < floor - 1e-12)
        if not below.any():
            break
        fixed |= below
    return out


def apply_regime(w: pd.Series, regime: str, win: MarketData, spec: Spec = SPEC) -> pd.Series:
    if regime in ("Bull", "Neutral"):
        return w
    if regime == "Correction":
        return w * spec.correction_scale
    if regime == "Crisis":
        basket = [t for t in spec.hedge_basket if t in win.adj_close.columns and win.adj_close[t].notna().iloc[-1]]
        each = min(spec.w_max, 1.0 / len(basket))
        return pd.Series({t: each for t in basket})
    if regime == "Stagflation":
        tilt = [t for t in spec.stagflation_tilt if t in w.index]
        others = [t for t in w.index if t not in tilt]
        out = pd.Series(0.0, index=w.index)
        out[tilt] = spec.w_max
        out[others] = _water_fill(w[others], 1.0 - spec.w_max * len(tilt), spec.w_min)
        return out
    raise ValueError(f"unknown regime {regime}")


def make_strategy(signal: str, optimizer: str, overlay: bool, spec: Spec = SPEC,
                  universe=UNIVERSE):
    sig_fn = SIGNALS[signal]

    def weight_fn(win: MarketData, split: Split) -> pd.Series:
        tickers = [t for t in universe if win.adj_close[t].notna().sum() >= spec.signal_lookback + 1]
        sig = sig_fn(win, tickers, spec)
        cov = covariance(win, tickers, spec)
        mu = expected_returns(win, tickers, spec) if optimizer == "MaxSharpe" else None
        w = optimize(optimizer, cov, mu, sig.pinned, sig.bounds)
        if overlay:
            w = apply_regime(w, regime_at(win.macro, spec), win, spec)
        return w

    weight_fn.__name__ = strategy_name(signal, optimizer, overlay)
    return weight_fn
