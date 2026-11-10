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


BOOT_BLOCK = 21  # trading days per block in the paired bootstrap of Sharpe differences (SPEC section 10)


def bootstrap_sharpe_diff(nav_x: pd.Series, nav_y: pd.Series, rf: pd.Series, n_boot: int = 2000,
                          block: int = BOOT_BLOCK, seed: int = 0, return_draws: bool = False) -> dict:
    """Paired moving-block bootstrap of the annualized Sharpe difference (one-sided p).

    ``return_draws`` adds the resampled differences under ``draws``, for a caller that needs another
    functional of the same distribution (the share of draws at or above a required margin, say).
    """
    ex = excess_returns(nav_x, rf)
    ey = excess_returns(nav_y, rf).reindex(ex.index)
    ax, ay = ex.to_numpy(), ey.to_numpy()
    n = len(ax)
    rng = np.random.default_rng(seed)

    def sh(a):
        s = a.std(ddof=1)
        return a.mean() / s * np.sqrt(TRADING_DAYS) if s > 0 else 0.0

    obs = sh(ax) - sh(ay)
    n_blocks = int(np.ceil(n / block))
    diffs = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n - block + 1, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        diffs[b] = sh(ax[idx]) - sh(ay[idx])
    p = float(np.mean(diffs <= 0.0))
    out: dict = {"sharpe_diff": float(obs), "p_one_sided": p,
                 "ci_low": float(np.percentile(diffs, 2.5)), "ci_high": float(np.percentile(diffs, 97.5))}
    if return_draws:
        out["draws"] = diffs
    return out


def bonferroni_table(results: dict[str, dict], spec: Spec = SPEC) -> pd.DataFrame:
    """``results``: strategy -> dict with 'p_one_sided'."""
    alpha_b = spec.bonferroni_alpha
    df = pd.DataFrame(results).T
    df["alpha_bonferroni"] = alpha_b
    df["significant"] = df["p_one_sided"] < alpha_b
    return df


# --------------------------------------------------------------------------- #
# SPEC section 15: power, HAC tests, deflated Sharpe, Romano-Wolf stepdown
# --------------------------------------------------------------------------- #
from scipy.optimize import brentq  # noqa: E402

EULER_GAMMA = 0.5772156649015329


def min_detectable_effect(n: int, sd: float, alpha: float = 0.05, power: float = 0.80, one_sided: bool = True) -> float:
    """Smallest mean difference a paired t-test detects with the given power (noncentral t)."""
    if n < 3 or not np.isfinite(sd) or sd <= 0:
        return np.nan
    df = n - 1
    t_crit = st.t.ppf(1 - alpha, df) if one_sided else st.t.ppf(1 - alpha / 2, df)
    f = lambda nc: st.nct.sf(t_crit, df, nc) - power
    nc = brentq(f, 0.0, 100.0)
    return float(nc * sd / np.sqrt(n))


def required_n(effect: float, sd: float, alpha: float = 0.05, power: float = 0.80, one_sided: bool = True,
               n_max: int = 20000) -> int | None:
    """Smallest sample size at which ``effect`` becomes detectable; None if above ``n_max``."""
    if effect <= 0 or not np.isfinite(effect):
        return None
    lo, hi = 3, n_max
    if min_detectable_effect(hi, sd, alpha, power, one_sided) > effect:
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        if min_detectable_effect(mid, sd, alpha, power, one_sided) <= effect:
            hi = mid
        else:
            lo = mid + 1
    return int(lo)


def newey_west_se(d: np.ndarray, lags: int | None = None) -> tuple[float, int]:
    """HAC standard error of the mean with a Bartlett kernel."""
    d = np.asarray(d, dtype=float)
    n = len(d)
    if lags is None:
        lags = int(np.floor(0.75 * n ** (1.0 / 3.0)))
    e = d - d.mean()
    s = float(e @ e) / n
    for j in range(1, lags + 1):
        w = 1.0 - j / (lags + 1.0)
        s += 2.0 * w * float(e[j:] @ e[:-j]) / n
    return float(np.sqrt(max(s, 0.0) / n)), lags


def hac_paired_test(x: pd.Series, y: pd.Series, lags: int | None = None) -> dict:
    """One-sided paired test with Newey-West standard errors (H1: mean(x - y) > 0)."""
    d = (x - y).dropna().to_numpy()
    n = len(d)
    if n < 3:
        return {"n": n, "mean_diff": np.nan, "se_hac": np.nan, "t": np.nan, "p_one_sided": np.nan, "lags": 0}
    se, L = newey_west_se(d, lags)
    t = d.mean() / se if se > 0 else np.inf
    return {"n": n, "mean_diff": float(d.mean()), "se_hac": se, "t": float(t),
            "p_one_sided": float(1.0 - st.t.cdf(t, df=n - 1)), "lags": L}


def sharpe_moments(r: pd.Series | np.ndarray) -> dict:
    r = np.asarray(pd.Series(r).dropna(), dtype=float)
    n = len(r)
    sd = r.std(ddof=1)
    sr = r.mean() / sd if sd > 0 else np.nan
    return {"n": n, "sr": float(sr), "skew": float(st.skew(r)), "kurt": float(st.kurtosis(r, fisher=False))}


def probabilistic_sharpe(sr_hat: float, sr_ref: float, n: int, skew: float, kurt: float) -> float:
    """P(true SR > sr_ref) given the estimate (per-period SR, non-annualised)."""
    denom = np.sqrt(max(1.0 - skew * sr_hat + (kurt - 1.0) / 4.0 * sr_hat ** 2, 1e-12))
    return float(st.norm.cdf((sr_hat - sr_ref) * np.sqrt(n - 1) / denom))


def expected_max_sharpe(var_sr: float, n_trials: int) -> float:
    """Expected maximum per-period SR among ``n_trials`` zero-mean trials (Bailey & Lopez de Prado)."""
    if n_trials <= 1 or var_sr <= 0:
        return 0.0
    g = EULER_GAMMA
    return float(np.sqrt(var_sr) * ((1 - g) * st.norm.ppf(1 - 1.0 / n_trials) + g * st.norm.ppf(1 - 1.0 / (n_trials * np.e))))


def deflated_sharpe_table(returns: pd.DataFrame, n_trials: int | None = None, periods_per_year: int = 12) -> pd.DataFrame:
    """Per-column PSR(0), the family's expected max SR and the deflated SR."""
    n_trials = n_trials or returns.shape[1]
    moms = {c: sharpe_moments(returns[c]) for c in returns.columns}
    srs = np.array([m["sr"] for m in moms.values()])
    sr0 = expected_max_sharpe(float(np.nanvar(srs, ddof=1)) if len(srs) > 1 else 0.0, n_trials)
    rows = {}
    for c, m in moms.items():
        rows[c] = {"n": m["n"], "sr_per_period": m["sr"], "sr_annualised": m["sr"] * np.sqrt(periods_per_year),
                   "skew": m["skew"], "kurt": m["kurt"],
                   "psr_vs_zero": probabilistic_sharpe(m["sr"], 0.0, m["n"], m["skew"], m["kurt"]),
                   "sr0_expected_max": sr0, "n_trials": n_trials,
                   "deflated_sr": probabilistic_sharpe(m["sr"], sr0, m["n"], m["skew"], m["kurt"])}
    return pd.DataFrame(rows).T


def _circular_block_indices(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=n_blocks)
    idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % n
    return idx[:n]


def romano_wolf(diffs: pd.DataFrame, n_boot: int = 2000, block: int = 6, seed: int = 0, alpha: float = 0.05) -> pd.DataFrame:
    """One-sided max-t stepdown (Romano & Wolf) with a circular block bootstrap.

    ``diffs``: periods x strategies matrix of (strategy - benchmark) returns.
    Returns per strategy: t, raw bootstrap p, stepdown-adjusted p, rejection at alpha.
    The adjusted p of the best strategy is White's reality-check p-value.
    """
    X = diffs.dropna().to_numpy(dtype=float)
    n, k = X.shape
    rng = np.random.default_rng(seed)
    mean = X.mean(axis=0)
    se = X.std(axis=0, ddof=1) / np.sqrt(n)
    t_obs = mean / se
    boot_t = np.empty((n_boot, k))
    for b in range(n_boot):
        xb = X[_circular_block_indices(n, block, rng)]
        se_b = xb.std(axis=0, ddof=1) / np.sqrt(n)
        boot_t[b] = (xb.mean(axis=0) - mean) / np.where(se_b > 0, se_b, np.inf)
    order = np.argsort(-t_obs)
    p_adj = np.empty(k)
    prev = 0.0
    for s, kk in enumerate(order):
        remaining = order[s:]
        max_t = boot_t[:, remaining].max(axis=1)
        p = (1.0 + np.sum(max_t >= t_obs[kk])) / (n_boot + 1.0)
        p = max(p, prev)
        p_adj[kk] = p
        prev = p
    p_raw = np.array([(1.0 + np.sum(boot_t[:, j] >= t_obs[j])) / (n_boot + 1.0) for j in range(k)])
    out = pd.DataFrame({"mean_diff": mean, "t": t_obs, "p_raw_bootstrap": p_raw, "p_adjusted_stepdown": p_adj,
                        "reject_at_alpha": p_adj < alpha}, index=diffs.columns)
    out.attrs["white_reality_check_p"] = float(p_adj[order[0]])
    out.attrs["n_boot"], out.attrs["block"], out.attrs["alpha"] = n_boot, block, alpha
    return out.sort_values("t", ascending=False)
