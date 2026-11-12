"""Phase 2.2 optimizers (locked): long-only min-variance and max-Sharpe with box bounds."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .config import SPEC, Spec
from .data import MarketData

log = logging.getLogger(__name__)
ANN = 252.0


def covariance(win: MarketData, tickers, spec: Spec = SPEC) -> pd.DataFrame:
    """Annualized covariance of daily returns over the (training) window."""
    r = win.returns(list(tickers)).dropna(how="any")
    if len(r) < 120:
        raise ValueError("training window too short for a covariance estimate")
    return r.cov(ddof=1) * ANN


def expected_returns(win: MarketData, tickers, spec: Spec = SPEC) -> pd.Series:
    """Annualized 60-day trailing return in excess of cash (plan: mu = 60-day rolling return)."""
    px = win.adj_close[list(tickers)].dropna(how="any")
    lb = spec.signal_lookback
    mu = (px.iloc[-1] / px.iloc[-1 - lb] - 1.0) * (ANN / lb)
    rf = float(win.cash_yield(spec).iloc[-1])
    return (mu - rf).rename("mu_excess")


def _feasible_bounds(lo: np.ndarray, hi: np.ndarray, budget: float) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = lo.copy(), hi.copy()
    if lo.sum() > budget + 1e-12:
        log.warning("floors exceed budget; scaling floors down")
        lo *= budget / lo.sum()
    if hi.sum() < budget - 1e-12:
        log.warning("caps below budget; scaling caps up")
        hi *= budget / hi.sum()
    return lo, hi


def optimize(objective: str, cov: pd.DataFrame, mu: pd.Series | None,
             pinned: dict[str, float], bounds: dict[str, tuple[float, float]]) -> pd.Series:
    tickers = list(cov.index)
    free = [t for t in tickers if t not in pinned]
    budget = 1.0 - sum(pinned.values())
    if budget < -1e-9:
        raise ValueError("pinned weights exceed 100%")
    sigma = cov.to_numpy()
    w_pin = np.array([pinned.get(t, 0.0) for t in tickers])
    idx_free = np.array([tickers.index(t) for t in free], dtype=int)
    if not free or budget <= 1e-12:
        return pd.Series(w_pin, index=tickers)

    lo = np.array([bounds[t][0] for t in free])
    hi = np.array([bounds[t][1] for t in free])
    lo, hi = _feasible_bounds(lo, hi, budget)
    span = np.maximum(hi - lo, 0.0)
    x0 = lo + (budget - lo.sum()) * (span / span.sum() if span.sum() > 0 else 0.0)

    def full(x: np.ndarray) -> np.ndarray:
        w = w_pin.copy()
        w[idx_free] = x
        return w

    def var(x):
        w = full(x)
        return float(w @ sigma @ w)

    def var_grad(x):
        return (2.0 * sigma @ full(x))[idx_free]

    cons = [{"type": "eq", "fun": lambda x: x.sum() - budget, "jac": lambda x: np.ones_like(x)}]
    bnds = list(zip(lo, hi))
    mv = minimize(var, x0, jac=var_grad, bounds=bnds, constraints=cons, method="SLSQP",
                  options={"ftol": 1e-14, "maxiter": 1000})
    x_mv = mv.x if mv.success else x0

    if objective.lower() in ("minvar", "min-variance", "min_variance"):
        x = x_mv
    elif objective.lower() in ("maxsharpe", "max-sharpe", "max_sharpe"):
        if mu is None:
            raise ValueError("max-Sharpe needs expected returns")
        m = mu.reindex(tickers).to_numpy()
        # Best achievable linear return inside the box: greedy fill by mu.
        order = np.argsort(-m[idx_free])
        x_lin = lo.copy()
        rem = budget - lo.sum()
        for k in order:
            add = min(hi[k] - lo[k], rem)
            x_lin[k] += add
            rem -= add
        if float(m @ full(x_lin)) <= 0.0:
            x = x_mv  # no fund has positive expected excess return -> risk-minimizing fallback
        else:
            def neg_sharpe(x):
                w = full(x)
                v = w @ sigma @ w
                return -float(m @ w) / np.sqrt(v) if v > 0 else 0.0
            best = None
            for start in (x0, x_mv, x_lin):
                res = minimize(neg_sharpe, start, bounds=bnds, constraints=cons, method="SLSQP",
                               options={"ftol": 1e-12, "maxiter": 1000})
                if res.success and (best is None or res.fun < best.fun):
                    best = res
            x = best.x if best is not None else x_mv
    else:
        raise ValueError(f"unknown objective {objective}")

    x = np.clip(x, lo, hi)
    x *= budget / x.sum()
    w = pd.Series(full(x), index=tickers)
    w[w.abs() < 1e-12] = 0.0
    return w
