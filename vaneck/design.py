"""SPEC section 15: power, HAC tests, deflated Sharpe and Romano-Wolf over the main study's strategies."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import study
from .calendar import Split
from .config import SPEC, Spec
from .data import MarketData
from .metrics import calendar_year_returns, monthly_returns
from .stats import (
    deflated_sharpe_table,
    hac_paired_test,
    min_detectable_effect,
    paired_onesided_t,
    required_n,
    romano_wolf,
)


def _monthly_excess_over_cash(nav: pd.Series, rf: pd.Series) -> pd.Series:
    m = monthly_returns(nav)
    cash_m = rf.reindex(nav.index).ffill().resample("ME").mean() / 12.0
    return (m - cash_m.reindex(m.index)).dropna()


def design_report(md: MarketData, splits: list[Split], spec: Spec = SPEC, n_boot: int = 2000,
                  block: int = 6) -> dict:
    base = study.run_baselines(md, splits, spec)
    strat = study.run_strategies(md, splits, spec, overlay=True)
    diag = study.run_strategies(md, splits, spec, overlay=False)
    runs = {**strat, **diag}
    bench = base[study.PRIMARY_BENCHMARK].post
    bench_ann = calendar_year_returns(bench.nav)
    bench_ann = bench_ann[bench_ann["full_year"]]["return"]
    bench_mon = monthly_returns(bench.nav)

    ann_diff = pd.DataFrame({n: (lambda cy: cy[cy["full_year"]]["return"])(calendar_year_returns(r.post.nav)) - bench_ann
                             for n, r in runs.items()})
    mon_diff = pd.DataFrame({n: monthly_returns(r.post.nav) - bench_mon for n, r in runs.items()}).dropna()
    alpha_b = spec.bonferroni_alpha

    # ---- A. power ------------------------------------------------------------- #
    rows = {}
    for n, r in runs.items():
        a, m = ann_diff[n].dropna(), mon_diff[n]
        sd_a, sd_m = float(a.std(ddof=1)), float(m.std(ddof=1))
        rows[n] = {
            "family": "primary" if n in strat else "diagnostic",
            "n_years": len(a), "mean_annual_excess": float(a.mean()), "sd_annual": sd_a,
            "mde_annual_bonferroni": min_detectable_effect(len(a), sd_a, alpha_b, 0.8),
            "mde_annual_alpha05": min_detectable_effect(len(a), sd_a, 0.05, 0.8),
            "years_needed_for_observed_effect": required_n(float(a.mean()), sd_a, alpha_b, 0.8),
            "n_months": len(m), "mean_monthly_excess_x12": float(m.mean() * 12), "sd_monthly_x_sqrt12": sd_m * np.sqrt(12),
            "mde_monthly_bonferroni_x12": min_detectable_effect(len(m), sd_m, alpha_b, 0.8) * 12,
            "mde_monthly_alpha05_x12": min_detectable_effect(len(m), sd_m, 0.05, 0.8) * 12,
        }
    power = pd.DataFrame(rows).T

    # ---- B. HAC monthly tests -------------------------------------------------- #
    rows = {}
    for n in runs:
        h = hac_paired_test(mon_diff[n] + bench_mon.reindex(mon_diff.index), bench_mon.reindex(mon_diff.index))
        plain = paired_onesided_t(mon_diff[n] + bench_mon.reindex(mon_diff.index), bench_mon.reindex(mon_diff.index))
        rows[n] = {"family": "primary" if n in strat else "diagnostic", "n_months": h["n"],
                   "mean_monthly_excess_x12": h["mean_diff"] * 12, "se_hac_x12": h["se_hac"] * 12, "lags": h["lags"],
                   "t_hac": h["t"], "p_hac_one_sided": h["p_one_sided"], "p_plain_one_sided": plain["p_one_sided"],
                   "bonferroni_significant": h["p_one_sided"] < alpha_b}
    hac = pd.DataFrame(rows).T

    # ---- C. deflated Sharpe ---------------------------------------------------- #
    excess = pd.DataFrame({n: _monthly_excess_over_cash(r.post.nav, r.post.rf) for n, r in runs.items()}).dropna()
    dsr6 = deflated_sharpe_table(excess[list(strat)], n_trials=6)
    dsr12 = deflated_sharpe_table(excess, n_trials=12)
    psr_diff = deflated_sharpe_table(mon_diff, n_trials=12)[["sr_per_period", "sr_annualised", "psr_vs_zero"]]
    psr_diff.columns = ["edge_sr_per_month", "edge_sr_annualised", "psr_edge_vs_zero"]
    dsr12 = dsr12.join(psr_diff)

    # ---- D. Romano-Wolf -------------------------------------------------------- #
    rw6 = romano_wolf(mon_diff[list(strat)], n_boot=n_boot, block=block, seed=0, alpha=0.05)
    rw12 = romano_wolf(mon_diff, n_boot=n_boot, block=block, seed=0, alpha=0.05)

    return {"power": power, "hac": hac, "dsr6": dsr6, "dsr12": dsr12, "rw6": rw6, "rw12": rw12,
            "meta": {"benchmark": study.PRIMARY_BENCHMARK, "alpha_bonferroni": alpha_b, "n_boot": n_boot, "block": block,
                     "white_rc_p_primary": rw6.attrs["white_reality_check_p"],
                     "white_rc_p_all": rw12.attrs["white_reality_check_p"],
                     "months": [str(mon_diff.index[0].date()), str(mon_diff.index[-1].date())]}}
