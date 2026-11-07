"""Phase 2.3 statistics: pre-registered paired one-sided t-tests with Bonferroni correction."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as st

from .config import SPEC, Spec
from .metrics import TRADING_DAYS, calendar_year_returns, excess_returns, monthly_returns


def paired_onesided_t(x: pd.Series, y: pd.Series) -> dict:
    """H0: mean(x - y) = 0 vs H1: mean(x - y) > 0."""
    d = (x - y).dropna()
    n = len(d)
    if n < 2:
        return {"n": n, "mean_diff": float(d.mean()) if n else np.nan, "t": np.nan, "p_one_sided": np.nan}
    sd = d.std(ddof=1)
    t = d.mean() / (sd / np.sqrt(n)) if sd > 0 else np.inf
    p = float(1.0 - st.t.cdf(t, df=n - 1))
    return {"n": n, "mean_diff": float(d.mean()), "t": float(t), "p_one_sided": p}


def annual_test(nav_x: pd.Series, nav_y: pd.Series, full_years_only: bool = True) -> dict:
    ax, ay = calendar_year_returns(nav_x), calendar_year_returns(nav_y)
    if full_years_only:
        ax, ay = ax[ax["full_year"]], ay[ay["full_year"]]
    return paired_onesided_t(ax["return"], ay["return"])


def monthly_test(nav_x: pd.Series, nav_y: pd.Series) -> dict:
    return paired_onesided_t(monthly_returns(nav_x), monthly_returns(nav_y))


def bootstrap_sharpe_diff(nav_x: pd.Series, nav_y: pd.Series, rf: pd.Series, n_boot: int = 2000,
                          block: int = 21, seed: int = 0) -> dict:
    """Paired moving-block bootstrap of the annualized Sharpe difference (one-sided p)."""
    ex = excess_returns(nav_x, rf)
    ey = excess_returns(nav_y, rf).reindex(ex.index)
    ex, ey = ex.to_numpy(), ey.to_numpy()
    n = len(ex)
    rng = np.random.default_rng(seed)

    def sh(a):
        s = a.std(ddof=1)
        return a.mean() / s * np.sqrt(TRADING_DAYS) if s > 0 else 0.0

    obs = sh(ex) - sh(ey)
    n_blocks = int(np.ceil(n / block))
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n - block + 1, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        diffs[b] = sh(ex[idx]) - sh(ey[idx])
    p = float(np.mean(diffs <= 0.0))
    return {"sharpe_diff": float(obs), "p_one_sided": p,
            "ci_low": float(np.percentile(diffs, 2.5)), "ci_high": float(np.percentile(diffs, 97.5))}


def bonferroni_table(results: dict[str, dict], spec: Spec = SPEC) -> pd.DataFrame:
    """``results``: strategy -> dict with 'p_one_sided'."""
    alpha_b = spec.bonferroni_alpha
    df = pd.DataFrame(results).T
    df["alpha_bonferroni"] = alpha_b
    df["significant"] = df["p_one_sided"] < alpha_b
    return df
