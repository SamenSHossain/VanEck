"""Command line entry point: ``python -m vaneck <command>``."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from . import data, report, study
from .config import REPORT_DIR, SPEC, UNIVERSE, Spec
from .universe import run_gate0

log = logging.getLogger("vaneck")
SHORT = {
    "EqualWeight": "Equal weight", "60/40 VanEck (60% EW equity funds / 40% BND)": "60/40 VanEck",
    "60/40 Classic (SPY/BND)": "60/40 SPY/BND", "Buy&Hold SMH": "Buy & hold SMH",
}


def _save(df: pd.DataFrame, name: str, outdir: Path):
    (outdir / "tables").mkdir(parents=True, exist_ok=True)
    df.to_csv(outdir / "tables" / f"{name}.csv")


def cmd_fetch(args):
    m = data.update_cache(force=args.force)
    print(f"cached {len(m)} tickers in {data.RAW_DIR}")
    md = data.build_market_data()
    q = data.data_quality_report(md)
    print(q.to_string())
    REPORT_DIR.mkdir(exist_ok=True)
    _save(q, "data_quality", REPORT_DIR)
    return 0


def run_gate0_section(md, spec, outdir, plots) -> tuple[bool, str]:
    g0 = run_gate0(md, spec)
    _save(g0.corr_full.round(4), "gate0_correlation_matrix", outdir)
    _save(g0.rolling_avg.to_frame(), "gate0_rolling_avg_corr", outdir)
    _save(g0.clusters, "gate0_clusters", outdir)
    _save(g0.summary_table(), "gate0_checks", outdir)
    figs = ""
    if plots:
        fd = outdir / "figures"; fd.mkdir(parents=True, exist_ok=True)
        report.fig_corr_heatmap(g0.corr_full, fd / "gate0_corr_heatmap.png",
                                f"Daily-return correlations, {spec.fund_start[:4]}-{spec.study_end[:4]}")
        report.fig_rolling_corr(g0.rolling_avg, spec.gate0_avg_corr_max, fd / "gate0_rolling_corr.png",
                                "Average pairwise 60-day correlation across the 9 funds")
        figs = ("\n![correlation heatmap](figures/gate0_corr_heatmap.png)\n\n"
                "![rolling correlation](figures/gate0_rolling_corr.png)\n")
    md_txt = [
        "## Gate 0 - is the universe allocatable?\n",
        f"**Verdict: {'PASS' if g0.passed else 'FAIL'}**\n",
        report.md_table(g0.summary_table()), "",
        "Themes (average-linkage clusters at rho >= %.2f):\n" % spec.gate0_cluster_corr,
        report.md_table(g0.clusters), "",
        f"Highest pairwise correlation: {g0.max_pair[0]}/{g0.max_pair[1]} = {g0.max_pair[2]:.3f}. "
        f"Rolling 60-day average pairwise correlation: mean {g0.rolling_avg.mean():.2f}, "
        f"max {g0.rolling_avg.max():.2f}, share of days above {spec.gate0_avg_corr_max:.2f}: "
        f"{(g0.rolling_avg > spec.gate0_avg_corr_max).mean():.1%}.",
        figs,
    ]
    return g0.passed, "\n".join(md_txt)


def run_gate1_section(md, splits, spec, outdir, plots, runs=None):
    base = runs or study.run_baselines(md, splits, spec)
    tbl = study.summary_table(base)
    g1 = study.gate1(base, spec)
    passed = study.gate_passed(g1)
    _save(tbl, "gate1_baselines_summary", outdir); _save(g1, "gate1_checks", outdir)
    annual = study.annual_returns_table(base)
    _save(annual, "gate1_annual_returns_posttax", outdir)
    figs = ""
    if plots:
        fd = outdir / "figures"; fd.mkdir(parents=True, exist_ok=True)
        navs = {k: v.post.nav for k, v in base.items()}
        report.fig_nav(navs, fd / "gate1_nav_posttax.png", "Baselines, post-tax, growth of 100 since 2017-06-01",
                       emphasis=list(navs), labels=SHORT)
        report.fig_drawdown({k: base[k].post.nav for k in ("EqualWeight", study.PRIMARY_BENCHMARK, "60/40 Classic (SPY/BND)")},
                            fd / "gate1_drawdowns.png", "Post-tax drawdowns", labels=SHORT)
        report.fig_annual_dots(annual[["EqualWeight", study.PRIMARY_BENCHMARK, "60/40 Classic (SPY/BND)"]],
                               fd / "gate1_annual_returns.png", "Calendar-year returns, post-tax", labels=SHORT)
        figs = ("\n![baseline NAV](figures/gate1_nav_posttax.png)\n\n![drawdowns](figures/gate1_drawdowns.png)\n\n"
                "![annual returns](figures/gate1_annual_returns.png)\n")
    show = tbl[["cagr_pre", "cagr_post", "vol_post", "sharpe_pre", "sharpe_post", "mdd_post", "turnover_pre",
                "cost_bps_yr", "tax_pct_yr"]].rename(index=SHORT)
    md_txt = [
        "## Gate 1 - baselines and feasibility\n",
        f"**Verdict: {'PASS' if passed else 'FAIL'}** (decisive criteria only)\n",
        report.md_table(g1), "",
        "Baseline performance, OOS window %s to %s (costs in bps of NAV per year, taxes in %% of NAV per year):\n"
        % (splits[0].exec_date.date(), spec.study_end),
        report.md_table(show, pct_cols=("cagr_pre", "cagr_post", "vol_post", "mdd_post", "turnover_pre")), "",
        "Calendar-year returns, post-tax (full years only):\n",
        report.md_table(annual.rename(columns=SHORT), pct_cols=tuple(SHORT.values())), figs,
    ]
    return passed, "\n".join(md_txt), base


def run_phase2_section(md, splits, spec, outdir, plots, base, n_boot, informational):
    strat = study.run_strategies(md, splits, spec, overlay=True)
    diag = study.run_strategies(md, splits, spec, overlay=False)
    bench = base[study.PRIMARY_BENCHMARK]
    tbl_s, tbl_d = study.summary_table(strat), study.summary_table(diag)
    tests_post = study.phase2_tests(strat, bench, spec, taxed=True, n_boot=n_boot)
    tests_pre = study.phase2_tests(strat, bench, spec, taxed=False, n_boot=n_boot)
    tests_diag = study.phase2_tests(diag, bench, spec, taxed=True, n_boot=n_boot)
    tests_vs_ew = study.phase2_tests(strat, base["EqualWeight"], spec, taxed=True, n_boot=n_boot)
    for name, t in (("phase2_strategies_summary", tbl_s), ("phase2_diagnostics_summary", tbl_d),
                    ("phase2_tests_posttax", tests_post), ("phase2_tests_pretax", tests_pre),
                    ("phase2_tests_diagnostics_posttax", tests_diag), ("phase2_tests_vs_equalweight", tests_vs_ew)):
        _save(t, name, outdir)
    gate2 = bool(tests_post["significant"].any())
    winners = list(tests_post.index[tests_post["significant"]])
    regimes = study.regime_series(md, spec)
    reg_oos = regimes.loc[splits[0].exec_date:spec.study_end]
    rt = study.regime_tables({**base, **strat, **diag}, regimes)
    reg_rows = []
    for name, t in rt.items():
        t = t.copy(); t.insert(0, "run", name); reg_rows.append(t.reset_index())
    reg_all = pd.concat(reg_rows, ignore_index=True)
    _save(reg_all, "phase2_regime_breakdown_posttax", outdir)
    g3_all = study.gate3(rt, list(strat), spec=spec)
    _save(g3_all, "phase2_gate3_all_strategies", outdir)
    annual = study.annual_returns_table({**{k: base[k] for k in ("EqualWeight", study.PRIMARY_BENCHMARK)}, **strat})
    _save(annual, "phase2_annual_returns_posttax", outdir)
    for name, r in {**strat, **diag}.items():
        _save(r.pre.targets.round(4), f"targets_{name.replace('+', '_')}", outdir)

    figs = ""
    if plots:
        fd = outdir / "figures"
        navs = {k: base[k].post.nav for k in ("EqualWeight", study.PRIMARY_BENCHMARK)}
        navs.update({k: v.post.nav for k, v in strat.items()})
        report.fig_nav(navs, fd / "phase2_nav_posttax.png", "Six locked strategies (gray) against the baselines, post-tax",
                       emphasis=["EqualWeight", study.PRIMARY_BENCHMARK], labels=SHORT)
        navs_d = {k: base[k].post.nav for k in ("EqualWeight", study.PRIMARY_BENCHMARK)}
        navs_d.update({k: v.post.nav for k, v in diag.items()})
        report.fig_nav(navs_d, fd / "phase2_nav_posttax_no_overlay.png",
                       "Diagnostics: the same rules without the regime overlay (gray), post-tax",
                       emphasis=["EqualWeight", study.PRIMARY_BENCHMARK], labels=SHORT)
        all_tbl = pd.concat([study.summary_table(base), tbl_s, tbl_d]).rename(index=SHORT)
        report.fig_sharpe_dumbbell(all_tbl, fd / "phase2_sharpe_pre_post.png", "Sharpe ratio before and after tax, all runs")
        report.fig_regimes(reg_oos, md.macro["spx"], fd / "phase2_regimes.png",
                           "Ex-ante regime calendar over the out-of-sample window")
        best = tests_post["mean_annual_excess"].astype(float).idxmax()
        report.fig_drawdown({"EqualWeight": base["EqualWeight"].post.nav, best: strat[best].post.nav,
                             study.PRIMARY_BENCHMARK: bench.post.nav}, fd / "phase2_drawdowns.png",
                            f"Post-tax drawdowns: equal weight vs {best} vs 60/40", labels=SHORT)
        figs = ("\n![strategy NAV](figures/phase2_nav_posttax.png)\n\n![no overlay](figures/phase2_nav_posttax_no_overlay.png)\n\n"
                "![sharpe](figures/phase2_sharpe_pre_post.png)\n\n![regimes](figures/phase2_regimes.png)\n\n"
                "![drawdowns](figures/phase2_drawdowns.png)\n")

    cols = ["cagr_pre", "cagr_post", "vol_post", "sharpe_pre", "sharpe_post", "mdd_post", "turnover_pre", "cost_bps_yr", "tax_pct_yr"]
    pct = ("cagr_pre", "cagr_post", "vol_post", "mdd_post", "turnover_pre")
    tcols = ["n_years", "mean_annual_excess", "t_annual", "p_one_sided", "p_monthly_paired", "sharpe_diff", "p_bootstrap_sharpe", "significant"]
    reg_show = reg_all[reg_all["run"].isin(["EqualWeight", study.PRIMARY_BENCHMARK] + list(strat))]
    reg_pivot_sharpe = reg_show.pivot(index="run", columns="regime", values="sharpe").rename(index=SHORT)
    reg_pivot_mdd = reg_show.pivot(index="run", columns="regime", values="mdd_concat").rename(index=SHORT)
    share = reg_oos.value_counts(normalize=True)
    tag = " (informational: Gate 1 failed, run with --force)" if informational else ""
    md_txt = [
        f"## Phase 2 - six locked allocation rules{tag}\n",
        f"**Gate 2 verdict: {'PASS' if gate2 else 'FAIL'}** - strategies significant at Bonferroni alpha "
        f"{spec.bonferroni_alpha:.4f} (post-tax, annual paired one-sided t vs 60/40 VanEck): "
        f"{', '.join(winners) if winners else 'none'}\n",
        "### Performance, primary strategies (regime overlay applied)\n",
        report.md_table(tbl_s[cols], pct_cols=pct), "",
        "### Pre-registered test: post-tax annual returns vs 60/40 VanEck\n",
        report.md_table(tests_post[tcols]), "",
        "### Same test, pre-tax\n", report.md_table(tests_pre[tcols]), "",
        "### Primary strategies vs equal weight (post-tax)\n",
        report.md_table(tests_vs_ew[["mean_annual_excess", "p_one_sided", "sharpe_diff", "p_bootstrap_sharpe"]]), "",
        "### Diagnostics: the same six rules without the regime overlay (not hypothesis tests)\n",
        report.md_table(tbl_d[cols], pct_cols=pct), "",
        report.md_table(tests_diag[tcols]), "",
        "### Calendar-year returns, post-tax\n",
        report.md_table(annual.rename(columns=SHORT), pct_cols=tuple(annual.rename(columns=SHORT).columns)), "",
        "## Phase 2.4 - regime breakdown (post-tax)\n",
        "Share of OOS trading days by regime: " + ", ".join(f"{k} {v:.1%}" for k, v in share.items()) +
        ". Stagflation never occurs in the OOS window under the locked definition.\n",
        "Sharpe by regime:\n", report.md_table(reg_pivot_sharpe), "",
        "Max drawdown by regime (regime days concatenated):\n", report.md_table(reg_pivot_mdd, pct_cols=tuple(reg_pivot_mdd.columns)), "",
        "### Gate 3 checks for every primary strategy\n",
        report.md_table(g3_all.drop(columns=["note"]).dropna(subset=["days"]).set_index("strategy"),
                        pct_cols=("strategy_mdd", "ew_mdd")), figs,
    ]
    return gate2, winners, "\n".join(md_txt), strat, diag


def run_sensitivity_section(md, splits, spec, outdir):
    sens = study.sensitivities(md, splits, spec)
    _save(sens, "sensitivities", outdir)
    show = sens[["sharpe_pre", "sharpe_post", "cagr_post", "mdd_post", "turnover_pre", "tax_pct_yr"]].rename(index=SHORT, level=1)
    show = show.reset_index().rename(columns={"name": "portfolio"})
    return "\n".join(["## Sensitivities (reported, never used for decisions)\n",
                       report.md_table(show, index=False, pct_cols=("cagr_post", "mdd_post", "turnover_pre")), ""])


def cmd_all(args, phases=("gate0", "gate1", "phase2")):
    spec = SPEC
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    md = data.build_market_data(spec)
    splits = study.make_splits(md, spec)
    verdicts = {"oos_start": str(splits[0].exec_date.date()), "oos_end": spec.study_end, "n_rebalances": len(splits)}
    parts = [f"# VanEck allocation study - generated report\n",
             f"Data through {md.index[-1].date()}; OOS window {splits[0].exec_date.date()} to {spec.study_end}; "
             f"{len(splits)} monthly rebalances; cash/risk-free = 13-week T-bill; taxes 37% FIFO; "
             f"costs tiered by dollar volume. See docs/SPEC.md for every locked rule.\n"]
    plots = not args.no_plots
    g0_ok, txt = run_gate0_section(md, spec, outdir, plots)
    verdicts["gate0"] = g0_ok; parts.append(txt)
    if "gate1" in phases and (g0_ok or args.force):
        g1_ok, txt, base = run_gate1_section(md, splits, spec, outdir, plots)
        verdicts["gate1"] = g1_ok; parts.append(txt)
        if "phase2" in phases and (g1_ok or args.force):
            g2_ok, winners, txt, strat, diag = run_phase2_section(md, splits, spec, outdir, plots, base, args.boot,
                                                                 informational=not g1_ok)
            verdicts["gate2"] = g2_ok; verdicts["gate2_winners"] = winners; parts.append(txt)
            parts.append(run_sensitivity_section(md, splits, spec, outdir))
        elif "phase2" in phases:
            parts.append("## Phase 2\n\nNot run: Gate 1 failed. Re-run with `--force` for informational results.\n")
    elif "gate1" in phases:
        parts.append("## Gate 1\n\nNot run: Gate 0 failed. Re-run with `--force`.\n")
    (outdir / "REPORT.md").write_text("\n".join(parts))
    (outdir / "gates.json").write_text(json.dumps(verdicts, indent=2, default=str))
    print(json.dumps(verdicts, indent=2, default=str))
    print(f"report written to {outdir / 'REPORT.md'}")
    return 0


def cmd_validate(args):
    """Hold-out evaluation (2025+). Only meaningful if Phase 2 produced winners."""
    spec = SPEC
    outdir = Path(args.out) / "holdout"; outdir.mkdir(parents=True, exist_ok=True)
    md = data.build_market_data(spec)
    splits = study.make_splits(md, spec, start=spec.fund_start, end=md.index[-1])
    splits = [s for s in splits if s.exec_date >= pd.Timestamp(spec.holdout_start)]
    end = md.index[-1]
    base = study.run_baselines(md, splits, spec, end=end)
    strat = study.run_strategies(md, splits, spec, end=end, overlay=True)
    tbl = pd.concat([study.summary_table(base), study.summary_table(strat)])
    _save(tbl, "holdout_summary", outdir)
    print(tbl[["cagr_post", "sharpe_pre", "sharpe_post", "mdd_post", "turnover_pre"]].to_string())
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="vaneck", description="VanEck ETF allocation study")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download/refresh the raw data cache"); f.add_argument("--force", action="store_true")
    for name, phases in (("gate0", ("gate0",)), ("gate1", ("gate0", "gate1")), ("phase2", ("gate0", "gate1", "phase2")), ("all", ("gate0", "gate1", "phase2"))):
        s = sub.add_parser(name, help=f"run through {name}")
        s.add_argument("--force", action="store_true", help="continue past a failed gate (results labelled informational)")
        s.add_argument("--no-plots", action="store_true")
        s.add_argument("--boot", type=int, default=2000, help="bootstrap draws for the supplementary Sharpe test")
        s.add_argument("--out", default=str(REPORT_DIR))
        s.set_defaults(func=lambda a, ph=phases: cmd_all(a, ph))
    v = sub.add_parser("validate", help="evaluate on the untouched 2025+ hold-out"); v.add_argument("--out", default=str(REPORT_DIR))
    f.set_defaults(func=cmd_fetch); v.set_defaults(func=cmd_validate)
    args = p.parse_args(argv)
    return args.func(args)
