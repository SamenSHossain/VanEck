"""Runs the three pre-registered extensions and writes reports/EXTENSIONS.md with figures."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from . import audit, design, liquidity, report, study, taxaware
from .config import REPORT_DIR, SPEC, Spec
from .data import MarketData

SHORT = {"EqualWeight": "Equal weight", study.PRIMARY_BENCHMARK: "60/40 VanEck",
         "60/40 Classic (SPY/BND)": "60/40 SPY/BND", "DriftxMinVar": "Drift x MinVar",
         "MomentumxMaxSharpe": "Momentum x MaxSharpe"}
ORDINAL = ["#86b6ef", "#5598e7", "#2a78d6", "#184f95"]   # validated ordinal ramp (policies P0..P3)


def fig_policy_ladder(tbl: pd.DataFrame, path: Path):
    """Post-tax Sharpe by execution policy, one row per portfolio (ordinal hue by policy)."""
    policies = list(tbl.index.get_level_values("policy").unique())
    ports = list(tbl.index.get_level_values("portfolio").unique())
    fig, ax = plt.subplots(figsize=(8.5, 0.6 * len(ports) + 1.8), dpi=150)
    report.style(ax, grid_axis="x")
    for i, p in enumerate(ports):
        vals = [tbl.loc[(p, pol), "sharpe_post"] for pol in policies]
        ax.plot([min(vals), max(vals)], [i, i], color=report.AXIS, linewidth=2, zorder=1)
        for k, (pol, v) in enumerate(zip(policies, vals)):
            ax.scatter([v], [i], s=70, color=ORDINAL[k % len(ORDINAL)], edgecolor=report.SURFACE, linewidth=2, zorder=3,
                       label=pol if i == 0 else None)
        ax.annotate(f"{vals[-1]:.2f}", (vals[-1], i), xytext=(8, 0), textcoords="offset points", va="center",
                    fontsize=8, color=report.INK2)
    ax.set_yticks(range(len(ports)))
    ax.set_yticklabels([SHORT.get(p, p) for p in ports])
    ax.invert_yaxis()
    ax.set_xlabel("post-tax Sharpe ratio")
    report._legend_above(ax, "Post-tax Sharpe by execution policy", ncol=2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_capacity(curve: pd.DataFrame, path: Path, drag_limit: float, te_limit: float):
    ports = list(curve.index.get_level_values("portfolio").unique())
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), dpi=150, sharex=True)
    for ax, col, title, limit in ((axes[0], "drag_bps_yr", "Friction drag, bps per year", drag_limit),
                                  (axes[1], "tracking_error_bps_yr", "Tracking error vs frictionless, bps per year", te_limit)):
        report.style(ax)
        for k, p in enumerate(ports):
            c = curve.loc[p]
            ax.plot(c.index, c[col], color=report.PALETTE[k], linewidth=2, marker="o", markersize=6,
                    markeredgecolor=report.SURFACE, markeredgewidth=1.5, label=SHORT.get(p, p))
        ax.axhline(limit, color=report.MUTED, linewidth=1)
        ax.text(curve.index.get_level_values("aum").min(), limit, f" limit {limit:.0f}", color=report.INK2,
                fontsize=8, va="bottom")
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v / 1e6:.0f}M" if v < 1e9 else f"${v / 1e9:.0f}B"))
        ax.set_title(title, loc="left", pad=8)
        ax.set_xlabel("initial capital")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=len(labels), bbox_to_anchor=(0.5, 1.0), frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(path)
    plt.close(fig)


def run_all(md: MarketData, spec: Spec = SPEC, out: Path = REPORT_DIR, n_boot: int = 2000, plots: bool = True) -> dict:
    out = Path(out)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    splits = study.make_splits(md, spec)
    results: dict = {}

    # ---- 1. tax-aware execution --------------------------------------------------- #
    ladder, _ = taxaware.run_ladder(md, splits, spec)
    margins = taxaware.gate1_margins(ladder, spec)
    sens = taxaware.sensitivities(md, splits, spec)
    ladder.to_csv(out / "tables" / "ext1_policy_ladder.csv")
    margins.to_csv(out / "tables" / "ext1_gate1_margins.csv")
    sens.to_csv(out / "tables" / "ext1_sensitivities.csv")
    results["gate1_margin_by_policy"] = margins["margin"].round(4).to_dict()
    results["gate1_pass_any_policy"] = bool(margins["gate1_pass"].any())

    # ---- 2. statistical design ---------------------------------------------------- #
    des = design.design_report(md, splits, spec, n_boot=n_boot)
    for k in ("power", "hac", "dsr6", "dsr12", "rw6", "rw12"):
        des[k].to_csv(out / "tables" / f"ext2_{k}.csv")
    results["white_rc_p_primary"] = des["meta"]["white_rc_p_primary"]
    results["white_rc_p_all"] = des["meta"]["white_rc_p_all"]
    results["romano_wolf_rejections"] = [str(i) for i in des["rw12"].index[des["rw12"]["reject_at_alpha"]]]

    # ---- 3. liquidity --------------------------------------------------------------- #
    curve, caps = liquidity.capacity_curve(md, splits, spec)
    curve.to_csv(out / "tables" / "ext3_capacity_curve.csv")
    caps.to_csv(out / "tables" / "ext3_capacity.csv")
    results["capacity_aum"] = caps["capacity_aum"].to_dict()

    figs = ""
    if plots:
        fig_policy_ladder(ladder, out / "figures" / "ext1_policy_ladder.png")
        fig_capacity(curve, out / "figures" / "ext3_capacity_curve.png", 25.0, 50.0)
        figs = "\n![policy ladder](figures/ext1_policy_ladder.png)\n\n![capacity](figures/ext3_capacity_curve.png)\n"

    # ---- report ---------------------------------------------------------------------- #
    pct = ("cagr_pre", "cagr_post", "mdd_post", "turnover_pre", "tracking_error_vs_p0_pre")
    lad_show = ladder.reset_index()
    lad_show["portfolio"] = lad_show["portfolio"].map(lambda p: SHORT.get(p, p))
    lad_cols = ["portfolio", "policy", "cagr_pre", "cagr_post", "sharpe_pre", "sharpe_post", "mdd_post", "turnover_pre",
                "cost_bps_yr", "tax_pct_yr", "harvests", "tracking_error_vs_p0_pre", "sharpe_post_gain_vs_p0"]
    sens_show = sens.reset_index()
    sens_show["portfolio"] = sens_show["portfolio"].map(lambda p: SHORT.get(p, p))
    power_cols = ["family", "n_years", "mean_annual_excess", "sd_annual", "mde_annual_bonferroni", "mde_annual_alpha05",
                  "years_needed_for_observed_effect", "mde_monthly_bonferroni_x12", "mde_monthly_alpha05_x12"]
    hac_cols = ["family", "mean_monthly_excess_x12", "se_hac_x12", "lags", "t_hac", "p_hac_one_sided", "p_plain_one_sided"]
    dsr_cols = ["sr_annualised", "skew", "kurt", "psr_vs_zero", "sr0_expected_max", "deflated_sr"]
    rw_cols = ["mean_diff", "t", "p_raw_bootstrap", "p_adjusted_stepdown", "reject_at_alpha"]
    curve_show = curve.reset_index()
    curve_show["portfolio"] = curve_show["portfolio"].map(lambda p: SHORT.get(p, p))
    curve_show["aum"] = curve_show["aum"].map(lambda v: f"{v / 1e6:,.0f}M")
    caps_show = caps.reset_index()
    caps_show["portfolio"] = caps_show["portfolio"].map(lambda p: SHORT.get(p, p))
    caps_show["capacity_aum"] = caps_show["capacity_aum"].map(lambda v: f"{v / 1e6:,.0f}M" if v else "below 1M")

    md_parts = [
        "# Pre-registered extensions - generated report\n",
        f"Window {splits[0].exec_date.date()} to {spec.study_end}; rules in docs/SPEC.md sections 14-16. "
        f"Narrative in EXTENSIONS_FINDINGS.md.\n",
        "## Extension 1 - tax-aware execution (SPEC s.14)\n",
        f"**Gate 1 verdict under any policy: {'changes' if results['gate1_pass_any_policy'] else 'unchanged (fail)'}**\n",
        report.md_table(margins, pct_cols=()), "",
        "Policy ladder (pre- and post-tax, same OOS window; tracking error is against the same portfolio under P0, pre-tax):\n",
        report.md_table(lad_show[lad_cols], index=False, pct_cols=pct), "",
        "Sensitivities:\n", report.md_table(sens_show, index=False, pct_cols=("cagr_post",)), "",
        "## Extension 2 - statistical design (SPEC s.15)\n",
        f"Benchmark {study.PRIMARY_BENCHMARK}; months {des['meta']['months'][0]} to {des['meta']['months'][1]}; "
        f"Bonferroni alpha {des['meta']['alpha_bonferroni']:.4f}; bootstrap {n_boot} draws, block {des['meta']['block']}.\n",
        "### A. Power: minimum detectable annual excess return at 80% power (annual test, n = 7) and the monthly equivalent\n",
        report.md_table(des["power"][power_cols],
                        pct_cols=("mean_annual_excess", "sd_annual", "mde_annual_bonferroni", "mde_annual_alpha05",
                                  "mde_monthly_bonferroni_x12", "mde_monthly_alpha05_x12")), "",
        "### B. Monthly paired test with Newey-West standard errors\n",
        report.md_table(des["hac"][hac_cols], pct_cols=("mean_monthly_excess_x12", "se_hac_x12")), "",
        "### C. Probabilistic and deflated Sharpe ratios (monthly excess returns over cash)\n",
        "Primary family, N = 6 trials:\n", report.md_table(des["dsr6"][dsr_cols]), "",
        "All 12 (with the edge series versus 60/40):\n",
        report.md_table(des["dsr12"][dsr_cols + ["edge_sr_annualised", "psr_edge_vs_zero"]]), "",
        "### D. Romano-Wolf stepdown (one-sided, alpha 0.05)\n",
        f"Primary family: White reality-check p = {des['meta']['white_rc_p_primary']:.3f}\n",
        report.md_table(des["rw6"][rw_cols], pct_cols=("mean_diff",)), "",
        f"All 12: White reality-check p = {des['meta']['white_rc_p_all']:.3f}\n",
        report.md_table(des["rw12"][rw_cols], pct_cols=("mean_diff",)), "",
        "## Extension 3 - liquidity and capacity (SPEC s.16)\n",
        "Square-root impact (coefficient 1.0) plus the 10% participation cap, pre-tax; capacity = largest AUM with drag "
        "<= 25 bps/yr and tracking error <= 50 bps/yr against the frictionless, uncapped run.\n",
        report.md_table(caps_show, index=False), "",
        report.md_table(curve_show, index=False, pct_cols=("cum_shortfall", "cagr", "turnover_pre", "capped_trade_share")),
        figs,
    ]
    (out / "EXTENSIONS.md").write_text("\n".join(md_parts))
    audit.write_json(out / "extensions_results.json", {**results, "manifest": audit.run_manifest("extensions", spec)})
    return results
