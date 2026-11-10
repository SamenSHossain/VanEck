"""Command line entry point: ``python -m vaneck <command>``."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from . import audit, data, health, live, monitor, report, study
from .broker import PaperBroker
from .config import REPORT_DIR, SPEC
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
    if args.secondary:
        from . import sources
        sm = sources.update_secondary_cache(price_tickers=list(data.UNIVERSE))
        print("secondary sources: " + ", ".join(f"{k}={v['status']}" for k, v in sm.items()))
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
        fd = outdir / "figures"
        fd.mkdir(parents=True, exist_ok=True)
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
    _save(tbl, "gate1_baselines_summary", outdir)
    _save(g1, "gate1_checks", outdir)
    annual = study.annual_returns_table(base)
    _save(annual, "gate1_annual_returns_posttax", outdir)
    figs = ""
    if plots:
        fd = outdir / "figures"
        fd.mkdir(parents=True, exist_ok=True)
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
        t = t.copy()
        t.insert(0, "run", name)
        reg_rows.append(t.reset_index())
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
        figs = ("\n![strategy NAV](figures/phase2_nav_posttax.png)\n\n"
                "![no overlay](figures/phase2_nav_posttax_no_overlay.png)\n\n"
                "![sharpe](figures/phase2_sharpe_pre_post.png)\n\n![regimes](figures/phase2_regimes.png)\n\n"
                "![drawdowns](figures/phase2_drawdowns.png)\n")

    cols = ["cagr_pre", "cagr_post", "vol_post", "sharpe_pre", "sharpe_post", "mdd_post", "turnover_pre",
            "cost_bps_yr", "tax_pct_yr"]
    pct = ("cagr_pre", "cagr_post", "vol_post", "mdd_post", "turnover_pre")
    tcols = ["n_years", "mean_annual_excess", "t_annual", "p_one_sided", "p_monthly_paired", "sharpe_diff",
             "p_bootstrap_sharpe", "significant"]
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
        "Max drawdown by regime (regime days concatenated):\n",
        report.md_table(reg_pivot_mdd, pct_cols=tuple(reg_pivot_mdd.columns)), "",
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
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    md = data.build_market_data(spec)
    splits = study.make_splits(md, spec)
    verdicts = {"oos_start": str(splits[0].exec_date.date()), "oos_end": spec.study_end, "n_rebalances": len(splits)}
    parts = ["# VanEck allocation study - generated report\n",
             f"Data through {md.index[-1].date()}; OOS window {splits[0].exec_date.date()} to {spec.study_end}; "
             f"{len(splits)} monthly rebalances; cash/risk-free = 13-week T-bill; taxes 37% FIFO; "
             f"costs tiered by dollar volume. See docs/SPEC.md for every locked rule.\n"]
    plots = not args.no_plots
    g0_ok, txt = run_gate0_section(md, spec, outdir, plots)
    verdicts["gate0"] = g0_ok
    parts.append(txt)
    if "gate1" in phases and (g0_ok or args.force):
        g1_ok, txt, base = run_gate1_section(md, splits, spec, outdir, plots)
        verdicts["gate1"] = g1_ok
        parts.append(txt)
        if "phase2" in phases and (g1_ok or args.force):
            g2_ok, winners, txt, strat, diag = run_phase2_section(md, splits, spec, outdir, plots, base, args.boot,
                                                                 informational=not g1_ok)
            verdicts["gate2"] = g2_ok
            verdicts["gate2_winners"] = winners
            parts.append(txt)
            parts.append(run_sensitivity_section(md, splits, spec, outdir))
        elif "phase2" in phases:
            parts.append("## Phase 2\n\nNot run: Gate 1 failed. Re-run with `--force` for informational results.\n")
    elif "gate1" in phases:
        parts.append("## Gate 1\n\nNot run: Gate 0 failed. Re-run with `--force`.\n")
    (outdir / "REPORT.md").write_text("\n".join(parts))
    (outdir / "gates.json").write_text(json.dumps(verdicts, indent=2, default=str))
    manifest = audit.run_manifest("all", spec, extra={"gates": verdicts})
    audit.write_json(outdir / "run_manifest.json", manifest)
    st = _store()
    st.record_run(manifest, "ok", payload=verdicts)
    st.close()
    print(json.dumps(verdicts, indent=2, default=str))
    print(f"report written to {outdir / 'REPORT.md'}")
    return 0


def cmd_validate(args):
    """The 2025+ hold-out: sealed until `--unseal --by NAME --reason TEXT` opens it once (SPEC section 19)."""
    from . import holdout
    folder = Path(args.out) / "holdout"
    md = data.build_market_data(SPEC)
    st = holdout.status(md, SPEC, folder)
    if st["state"] == "sealed" and not args.unseal:
        print(f"hold-out: SEALED. {st['trading_days']} trading days and {st['rebalances']} rebalances wait from {st['start']} "
              f"to {st['data_through']}.")
        print("It can be opened once. `python -m vaneck validate --unseal --by NAME --reason TEXT` records who opened it")
        print("and why, runs the evaluation of SPEC section 19 and freezes the tables. Nothing was evaluated.")
        return 2
    if st["state"] == "sealed":
        try:
            results = holdout.unseal(md, args.by, args.reason, SPEC, folder, n_boot=args.boot)["results"]
        except ValueError as exc:
            print(f"refused: {exc}")
            return 2
        st = holdout.status(md, SPEC, folder)
    else:
        if args.unseal:
            print("the hold-out is already open; showing the frozen results, nothing was recomputed")
        results = holdout.complete(md, SPEC, folder)
    print(f"hold-out: OPENED by {st['opened_by']} at {st['opened_utc']} ({st['reason']}); "
          f"evaluated through {st['evaluated_through']}")
    tbl = pd.DataFrame(results["summary"]).set_index("name")
    print(tbl[["cagr_post", "sharpe_pre", "sharpe_post", "mdd_post", "turnover_pre"]].to_string())
    for m in results["margins"]:
        print(f"equal weight minus {m['benchmark']}: {m['sharpe_diff']:+.3f} post-tax Sharpe, "
              f"95% interval {m['sharpe_diff_ci_low']:+.3f} to {m['sharpe_diff_ci_high']:+.3f}")
    return 0


def _store():
    from .store import Store
    return Store()


def _notify(source: str, severity: str, message: str, payload=None, store=None) -> None:
    from .notify import notify
    notify(source, severity, message, payload=payload, store=store)


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                   "logger": record.name, "msg": record.getMessage()}
        return json.dumps(payload)


def setup_logging(as_json: bool = False, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter() if as_json else logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)


def _print_decision(d: dict) -> None:
    print(f"decision {d['run_id']}: {d['strategy']} signal {d['signal_date']} exec {d['exec_date']} "
          f"regime {d['regime']} -> {d['status'].upper()} (health {d['health']['status']}, risk {d['risk']['status']})")
    print(f"  equity {d['equity']:,.0f}  cash {d['cash']:,.0f}  decision hash {d.get('decision_hash')}"
          + (f"  approval: {d['approval']}" if d.get("approval") else ""))
    if d.get("reconcile"):
        print(f"  reconciliation: {d['reconcile']['status']} ({len(d['reconcile']['breaks'])} breaks, "
              f"cash diff {d['reconcile']['cash_diff']:+,.2f})")
    print("  targets: " + ", ".join(f"{k} {100 * v:.1f}%" for k, v in d["targets"].items()))
    for c in d["risk"]["checks"]:
        if c["status"] != "pass":
            print(f"  risk {c['status'].upper()} {c['name']}: value={c['value']} limit={c['limit']} {c['detail']}")
    for c in d["health"]["checks"]:
        if c["status"] != "ok":
            print(f"  health {c['status'].upper()} {c['name']}: value={c['value']} limit={c['limit']} {c['detail']}")
    if d["risk"]["orders"]:
        print("  orders:")
        for o in d["risk"]["orders"]:
            part = "" if o["participation"] is None else f" ({100 * o['participation']:.1f}% of ADV)"
            print(f"    {o['side']:4s} {o['ticker']:5s} {o['shares']:>10,.0f} @ {o['price']:>8.2f} "
                  f"= {o['notional']:>12,.0f}{part} {o['note']}")
    for n in d["notes"]:
        print("  note:", n)
    if d["fills"]:
        print(f"  fills: {len(d['fills'])}, total cost {sum(f['cost'] for f in d['fills']):,.0f}")


def cmd_check(args):
    md = data.build_market_data()
    rep = health.check_market_data(md)
    payload = rep.to_dict()
    if args.cross:
        from . import crossval, sources
        xrep, tables = crossval.crossval_report(md, sources.load_secondary())
        rep.checks += xrep.checks
        payload = rep.to_dict()
        payload["crossval_tables"] = {k: v.to_dict(orient="records") for k, v in tables.items()}
        for k, v in tables.items():
            _save(v, f"crossval_{k}", REPORT_DIR)
    print(rep.to_json() if args.json else rep.to_markdown())
    audit.write_json(REPORT_DIR / "health.json", payload)
    st = _store()
    st.record_health(payload)
    if rep.blocked:
        names = ", ".join(c.name for c in rep.checks if c.status == "block")
        _notify("check", "block", f"data health blocked: {names}", payload={"as_of": payload["as_of"]}, store=st)
    st.close()
    return 2 if rep.blocked else 0


def cmd_paper(args):
    md = data.build_market_data()
    broker = PaperBroker(Path(args.book), md)
    if args.action == "init":
        if broker.initialized and not args.force:
            print(f"{args.book} already exists; use --force to overwrite")
            return 1
        broker.init(args.cash, args.as_of or md.index[-1])
        print(f"initialised paper book {args.book} with {args.cash:,.0f} as of {broker.as_of.date()}")
        return 0
    if args.action == "status":
        if not broker.initialized:
            print("no paper book")
            return 1
        broker.advance_to(md.index[-1]) if args.mark else None
        print(f"book {args.book}: as of {broker.as_of.date()}, equity {broker.equity():,.0f}, cash {broker.cash():,.0f}, "
              f"drawdown {broker.drawdown():.2%}, fills {len(broker._require()['fills'])}")
        print((100 * broker.weights()).round(2).to_string())
        return 0
    if args.action == "reset":
        if not args.yes:
            print("refusing to delete without --yes")
            return 1
        Path(args.book).unlink(missing_ok=True)
        print("deleted", args.book)
        return 0
    return 1


def _make_broker(args, md):
    """paper: the simulated book; alpaca: the venue for execution with the local book as shadow ledger."""
    shadow = PaperBroker(Path(args.book), md)
    if getattr(args, "broker", "paper") == "paper":
        return shadow
    from .alpaca import AlpacaBroker, AlpacaClient
    from .livebroker import LiveBroker
    client = AlpacaClient()
    if not client.configured:
        raise SystemExit("Alpaca credentials missing: set APCA_API_KEY_ID and APCA_API_SECRET_KEY "
                         "(and APCA_API_BASE_URL for live instead of paper)")
    return LiveBroker(AlpacaBroker(client), shadow, md, adopt_breaks=bool(getattr(args, "adopt_breaks", False)))


def _policy(name: str | None):
    if not name:
        return None
    from .execution import presets
    for label, pol in presets().items():
        if label.split()[0].upper() == name.upper():
            return pol
    raise SystemExit(f"unknown policy {name}; choose P0, P1, P2 or P3")


def cmd_rebalance(args):
    from .approvals import load_limits
    md = data.build_market_data()
    broker = _make_broker(args, md)
    split = live.decision_split(md, args.as_of)
    if not broker.initialized:
        if args.init_cash is None:
            print("paper book not initialised: run `paper init` or pass --init-cash")
            return 1
        target_book = broker.shadow if hasattr(broker, "shadow") else broker
        target_book.init(args.init_cash, split.signal_date)
    limits, why = load_limits()
    print("risk limits:", why)
    d = live.rebalance(md, args.strategy, broker, Path(args.decisions), as_of=args.as_of,
                       dry_run=not args.submit, force=args.force, limits=limits, policy=_policy(args.policy),
                       approvals_dir=Path(args.approvals))
    _print_decision(d)
    st = _store()
    st.record_decision(d)
    if d["status"] in ("blocked", "awaiting_exec_bar"):
        sev = "block" if d["status"] == "blocked" else "warn"
        _notify("rebalance", sev, f"{d['strategy']} {d['signal_date']} {d['status']}: " + "; ".join(d["notes"]) if d["notes"]
                else f"{d['strategy']} {d['signal_date']} {d['status']}",
                payload={"run_id": d["run_id"], "health": d["health"]["status"], "risk": d["risk"]["status"]}, store=st)
    st.close()
    return {"dry_run": 0, "submitted": 0, "blocked": 2, "halted": 2, "unapproved": 3}.get(d["status"], 1)


def cmd_replay(args):
    md = data.build_market_data()
    decs = live.replay(md, args.strategy, Path(args.book), Path(args.decisions), args.start, args.end, cash=args.cash,
                       policy=_policy(args.policy))
    print(f"{len(decs)} decisions: " + ", ".join(f"{d['signal_date']} {d['status']}" for d in decs))
    broker = PaperBroker(Path(args.book), md)
    rep = monitor.monitor(md, broker, Path(args.decisions), args.strategy, out_dir=Path(args.out))
    print(monitor.monitor_markdown(rep))
    st = _store()
    for d in decs:
        st.record_decision(d)
    st.record_monitor(rep)
    st.close()
    return 0


def cmd_monitor(args):
    md = data.build_market_data()
    broker = PaperBroker(Path(args.book), md)
    if not broker.initialized:
        print("no paper book to monitor")
        return 1
    if args.mark:
        broker.advance_to(md.index[-1])
    st = _store()
    history = [json.loads(r["payload"]) for r in st.recent("monitor", 10) if r["strategy"] == args.strategy]
    rep = monitor.monitor(md, broker, Path(args.decisions), args.strategy, out_dir=Path(args.out), monitor_history=history)
    print(monitor.monitor_markdown(rep))
    st.record_monitor(rep)
    if rep.get("alerts"):
        _notify("monitor", "alert", f"{args.strategy}: " + " | ".join(rep["alerts"]),
                payload={"as_of": rep.get("as_of")}, store=st)
    st.close()
    return 1 if rep.get("alerts") else 0


def cmd_extensions(args):
    from . import extensions
    md = data.build_market_data()
    res = extensions.run_all(md, out=Path(args.out), n_boot=args.boot, plots=not args.no_plots)
    print(json.dumps(res, indent=2, default=str))
    st = _store()
    st.record_run(audit.run_manifest("extensions"), "ok", payload={k: v for k, v in res.items() if k != "manifest"})
    st.close()
    print(f"report written to {Path(args.out) / 'EXTENSIONS.md'}")
    return 0


def cmd_history(args):
    st = _store()
    for e in st.timeline(args.last):
        print(f"{e['time']}  {e['kind']:9s} {e['summary']}")
    st.close()
    return 0


def cmd_status(args):
    from . import opstate
    from .retire import flag_path
    st = _store()
    rep = opstate.report(st)
    st.close()
    h, d, m, halted, flags = rep["health"], rep["decision"], rep["monitor"], rep["halted"], rep["retired_flags"]
    h_line = f"{h['status']} as of {h['as_of']} ({h['n_block']} block, {h['n_warn']} warn) at {h['created_utc']}" if h else "none"
    d_line = (f"{d['strategy']} signal {d['signal_date']} -> {d['status']} ({d['n_orders']} orders) at {d['created_utc']}"
              if d else "none")
    m_line = f"{m['strategy']} {m['status']} ({m['n_alerts']} alerts) as of {m['as_of']} at {m['created_utc']}" if m else "none"
    print("health:  ", h_line)
    print("decision:", d_line)
    print("monitor: ", m_line)
    print("halt:    ", f"HALTED by {halted['by']} at {halted['at']}: {halted['reason']}" if halted else "clear")
    print("retired: ", ", ".join(flags) if flags else "none", "" if flags else f"(flags live under {flag_path('x').parent})")
    print("submit:  ", rep["submission"] + (" (" + "; ".join(rep["reasons"]) + ")" if rep["reasons"] else ""))
    return 1 if rep["attention"] else 0


def cmd_dashboard(args):
    from . import dashboard
    st = _store()
    out = dashboard.render(st, Path(args.book), Path(args.out))
    st.close()
    print(f"dashboard written to {out}")
    return 0


def cmd_webdash(args):
    from . import webdash
    md = data.build_market_data()
    paths = webdash.write(md, Path(args.out), junit=Path(args.junit), n_boot=args.boot, check_golden=not args.skip_golden)
    for q in paths:
        print(f"written {q}")
    return 0


def cmd_approve(args):
    from . import approvals
    d = live.load_decision(Path(args.decisions), args.run_id)
    if d["status"] != "dry_run":
        print(f"decision {args.run_id} is {d['status']}; only dry-run decisions are approved")
        return 1
    rec = approvals.approve(d, args.by, Path(args.approvals))
    print(f"approved {d['strategy']} {d['signal_date']} (hash {rec['decision_hash']}) by {rec['by']} at {rec['at']}"
          + ("" if rec["signature"] else " [unsigned: no VANECK_APPROVAL_KEY]"))
    print(f"submit with: python -m vaneck rebalance --strategy '{d['strategy']}' --as-of {d['signal_date']} --submit")
    return 0


def cmd_halt(args):
    from . import approvals
    rec = approvals.set_halt(args.state == "on", args.reason, args.by)
    print("HALT", "set" if rec["halted"] else "cleared", f"by {rec['by']}: {rec['reason']}")
    return 0


def _parse_kv(items):
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        v = v.strip()
        out[k.strip()] = (v.lower() == "true") if v.lower() in ("true", "false") else (int(v) if v.isdigit() else float(v))
    return out


def cmd_limits(args):
    from . import approvals
    if args.action == "show":
        lim, why = approvals.load_limits()
        print(why)
        for k, v in lim.__dict__.items():
            print(f"  {k} = {v}")
        return 0
    if args.action == "propose":
        rec = approvals.propose_limits(_parse_kv(args.set), args.by)
        print(f"proposed {rec['changes']} by {rec['proposed_by']}; "
              "a second, different approver must run `limits approve --by NAME`")
        return 0
    rec = approvals.approve_limits(args.by)
    print("activated" if rec.get("activated") else rec.get("note", "recorded; still waiting for a second approver"))
    return 0


def cmd_broker(args):
    from .alpaca import AlpacaBroker, AlpacaClient
    client = AlpacaClient()
    if not client.configured:
        print("Alpaca credentials missing: set APCA_API_KEY_ID and APCA_API_SECRET_KEY")
        return 1
    print(json.dumps(AlpacaBroker(client).check(), indent=2, default=str))
    return 0


def cmd_reconcile(args):
    from .alpaca import AlpacaBroker, AlpacaClient
    from .livebroker import LiveBroker
    md = data.build_market_data()
    client = AlpacaClient()
    if not client.configured:
        print("Alpaca credentials missing: set APCA_API_KEY_ID and APCA_API_SECRET_KEY")
        return 1
    shadow = PaperBroker(Path(args.book), md)
    if not shadow.initialized:
        print("no shadow book; run `paper init` first")
        return 1
    lb = LiveBroker(AlpacaBroker(client), shadow, md, adopt_breaks=args.adopt)
    rep = lb.sync(md.index[-1])
    print(rep.to_markdown())
    audit.write_json(REPORT_DIR / f"reconcile_{rep.as_of}.json", rep.to_dict())
    st = _store()
    if not rep.clean:
        _notify("reconcile", "block", f"{len(rep.breaks)} position break(s), cash diff {rep.cash_diff:+,.2f}", store=st)
    st.close()
    return 0 if rep.clean else 2


def cmd_expectations(args):
    from . import retire
    md = data.build_market_data()
    exp = retire.freeze_expectations(md, args.strategy, Path(args.out), policy=_policy(args.policy))
    print(json.dumps(exp, indent=2))
    return 0


def cmd_retire(args):
    from . import retire
    if args.action == "clear":
        retire.clear_flag(args.strategy, args.by or "operator", args.reason or "")
        print("flag cleared")
        return 0
    flag = retire.is_retired(args.strategy)
    print(json.dumps(flag, indent=2) if flag else f"{args.strategy}: no retirement flag")
    return 0


def cmd_work(args):
    from .approvals import load_limits
    md = data.build_market_data()
    broker = _make_broker(args, md)
    limits, why = load_limits()
    d = live.work(md, args.parent, broker, Path(args.decisions), as_of=args.as_of, dry_run=not args.submit,
                  limits=limits, policy=_policy(args.policy))
    _print_decision(d)
    st = _store()
    st.record_decision(d)
    st.close()
    return 0 if d["status"] in ("dry_run", "submitted") else 1


def cmd_golden(args):
    from . import golden
    md = data.build_market_data()
    current = golden.compute(md)
    if args.update:
        m = golden.write(current)
        print(f"golden files written to {golden.GOLDEN_DIR} (config {m['config_hash']}, data {m['data_hash']})")
        return 0
    manifest, frames = golden.load()
    changed = golden.inputs_changed(manifest)
    problems = golden.compare(current, frames)
    for c in changed:
        print("INPUT CHANGED:", c)
    for pr in problems:
        print("MISMATCH:", pr)
    print("golden check:", "OK" if not (changed or problems) else "FAIL")
    return 0 if not (changed or problems) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vaneck", description="VanEck ETF allocation study")
    p.add_argument("--log-json", action="store_true", help="structured JSON logs")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download/refresh the raw data cache")
    f.add_argument("--force", action="store_true")
    f.add_argument("--secondary", action="store_true", help="also refresh FRED (and any price provider) for cross-validation")
    phase_sets = (("gate0", ("gate0",)), ("gate1", ("gate0", "gate1")),
                  ("phase2", ("gate0", "gate1", "phase2")), ("all", ("gate0", "gate1", "phase2")))
    for name, phases in phase_sets:
        s = sub.add_parser(name, help=f"run through {name}")
        s.add_argument("--force", action="store_true", help="continue past a failed gate (results labelled informational)")
        s.add_argument("--no-plots", action="store_true")
        s.add_argument("--boot", type=int, default=2000, help="bootstrap draws for the supplementary Sharpe test")
        s.add_argument("--out", default=str(REPORT_DIR))
        s.set_defaults(func=lambda a, ph=phases: cmd_all(a, ph))
    v = sub.add_parser("validate", help="the 2025+ hold-out: shows the seal; --unseal opens it once, logged (exit 2 if sealed)")
    v.add_argument("--out", default=str(REPORT_DIR))
    v.add_argument("--unseal", action="store_true", help="open the hold-out: a one-time decision, recorded with --by, --reason")
    v.add_argument("--by", default=None, help="who opens it")
    v.add_argument("--reason", default=None, help="why it is opened now")
    v.add_argument("--boot", type=int, default=2000, help="bootstrap draws for the margin interval")
    f.set_defaults(func=cmd_fetch)
    v.set_defaults(func=cmd_validate)

    c = sub.add_parser("check", help="data health report (exit 2 on a blocking problem)")
    c.add_argument("--json", action="store_true")
    c.add_argument("--cross", action="store_true", help="add cross-validation: FRED macro, peer divergence, corporate actions")
    c.set_defaults(func=cmd_check)

    book_default, dec_default = str(live.PAPER_DIR / "book.json"), str(live.PAPER_DIR / "decisions")
    pp = sub.add_parser("paper", help="manage the paper book")
    pp.add_argument("action", choices=["init", "status", "reset"])
    pp.add_argument("--book", default=book_default)
    pp.add_argument("--cash", type=float, default=SPEC.aum)
    pp.add_argument("--as-of", default=None)
    pp.add_argument("--force", action="store_true")
    pp.add_argument("--yes", action="store_true")
    pp.add_argument("--mark", action="store_true", help="roll the book to the latest bar")
    pp.set_defaults(func=cmd_paper)

    appr_default = str(live.PAPER_DIR / "approvals")
    r = sub.add_parser("rebalance", help="one decision: health -> targets -> orders -> risk -> record (dry run unless --submit)")
    r.add_argument("--broker", choices=["paper", "alpaca"], default="paper")
    r.add_argument("--adopt-breaks", action="store_true", help="adopt venue positions as transfers when reconciliation breaks")
    r.add_argument("--approvals", default=appr_default)
    r.add_argument("--strategy", default="EqualWeight")
    r.add_argument("--as-of", default=None, help="signal date (default: latest bar)")
    r.add_argument("--submit", action="store_true", help="execute against the paper book")
    r.add_argument("--force", action="store_true")
    r.add_argument("--book", default=book_default)
    r.add_argument("--decisions", default=dec_default)
    r.add_argument("--init-cash", type=float, default=None, help="initialise the paper book if missing")
    r.add_argument("--policy", default=None, help="execution policy P0..P3 (SPEC s.14); default: legacy full rebalance")
    r.set_defaults(func=cmd_rebalance)

    rp = sub.add_parser("replay", help="operational rehearsal: monthly cycle through the paper broker over a past window")
    rp.add_argument("--strategy", default="EqualWeight")
    rp.add_argument("--start", required=True)
    rp.add_argument("--end", required=True)
    rp.add_argument("--cash", type=float, default=SPEC.aum)
    rp.add_argument("--book", default=book_default)
    rp.add_argument("--decisions", default=dec_default)
    rp.add_argument("--out", default=str(REPORT_DIR))
    rp.add_argument("--policy", default=None, help="execution policy P0..P3")
    rp.set_defaults(func=cmd_replay)

    mo = sub.add_parser("monitor", help="reconcile the paper book with the research engine and raise alerts (exit 1 on alerts)")
    mo.add_argument("--strategy", default="EqualWeight")
    mo.add_argument("--book", default=book_default)
    mo.add_argument("--decisions", default=dec_default)
    mo.add_argument("--out", default=str(REPORT_DIR))
    mo.add_argument("--mark", action="store_true", help="roll the book to the latest bar first")
    mo.set_defaults(func=cmd_monitor)

    hi = sub.add_parser("history", help="timeline of runs, health checks, decisions, monitor reports and alerts")
    hi.add_argument("--last", type=int, default=30)
    hi.set_defaults(func=cmd_history)
    stt = sub.add_parser("status", help="latest health / decision / monitor state (exit 1 if anything is blocked or alerting)")
    stt.set_defaults(func=cmd_status)
    db = sub.add_parser("dashboard", help="render reports/dashboard.html from the run history and the paper book")
    db.add_argument("--book", default=book_default)
    db.add_argument("--out", default=str(REPORT_DIR / "dashboard.html"))
    db.set_defaults(func=cmd_dashboard)

    ap = sub.add_parser("approve", help="approve a dry-run decision so it may be submitted (recorded; signed if a key is set)")
    ap.add_argument("run_id")
    ap.add_argument("--by", default=None)
    ap.add_argument("--decisions", default=dec_default)
    ap.add_argument("--approvals", default=appr_default)
    ap.set_defaults(func=cmd_approve)
    ha = sub.add_parser("halt", help="set or clear the global halt flag (submissions refused while set)")
    ha.add_argument("state", choices=["on", "off"])
    ha.add_argument("--reason", required=True)
    ha.add_argument("--by", default=None)
    ha.set_defaults(func=cmd_halt)
    li = sub.add_parser("limits", help="risk limits in force; changes need two different approvers")
    li.add_argument("action", choices=["show", "propose", "approve"])
    li.add_argument("--set", nargs="*", help="field=value pairs for propose")
    li.add_argument("--by", default=None)
    li.set_defaults(func=cmd_limits)
    bc = sub.add_parser("broker", help="check connectivity and account state at the venue")
    bc.add_argument("action", choices=["check"])
    bc.set_defaults(func=cmd_broker)
    rc = sub.add_parser("reconcile", help="reconcile the shadow book against the venue (exit 2 on breaks)")
    rc.add_argument("--book", default=book_default)
    rc.add_argument("--adopt", action="store_true")
    rc.set_defaults(func=cmd_reconcile)
    exq = sub.add_parser("expectations", help="freeze the retirement-rule expectation bands for a strategy (SPEC s.17)")
    exq.add_argument("--strategy", default="EqualWeight")
    exq.add_argument("--policy", default=None)
    exq.add_argument("--out", default=str(live.PAPER_DIR))
    exq.set_defaults(func=cmd_expectations)
    rt = sub.add_parser("retire", help="show or clear a strategy's retirement flag")
    rt.add_argument("action", choices=["show", "clear"])
    rt.add_argument("--strategy", default="EqualWeight")
    rt.add_argument("--by", default=None)
    rt.add_argument("--reason", default=None)
    rt.set_defaults(func=cmd_retire)
    wk = sub.add_parser("work", help="work the capped residual of a submitted decision on a later day")
    wk.add_argument("--parent", required=True, help="run id of the submitted decision")
    wk.add_argument("--as-of", default=None)
    wk.add_argument("--submit", action="store_true")
    wk.add_argument("--broker", choices=["paper", "alpaca"], default="paper")
    wk.add_argument("--adopt-breaks", action="store_true")
    wk.add_argument("--book", default=book_default)
    wk.add_argument("--decisions", default=dec_default)
    wk.add_argument("--policy", default=None)
    wk.set_defaults(func=cmd_work)

    g = sub.add_parser("golden", help="check (or --update) the frozen key results against the current engine and inputs")
    g.add_argument("--update", action="store_true")
    g.set_defaults(func=cmd_golden)

    ex = sub.add_parser("extensions", help="pre-registered extensions: tax-aware execution, statistical design, capacity")
    ex.add_argument("--out", default=str(REPORT_DIR))
    ex.add_argument("--boot", type=int, default=2000)
    ex.add_argument("--no-plots", action="store_true")
    ex.set_defaults(func=cmd_extensions)

    wd = sub.add_parser("webdash", help="collect results, operations state and test outcomes into the dashboard's data files")
    wd.add_argument("--out", default=str(REPORT_DIR / "dashboard_data.json"))
    wd.add_argument("--junit", default=str(REPORT_DIR / "junit.xml"), help="pytest --junitxml report to summarise")
    wd.add_argument("--boot", type=int, default=2000, help="bootstrap draws (the report's default, so figures agree)")
    wd.add_argument("--skip-golden", action="store_true", help="leave out the frozen-results check (faster)")
    wd.set_defaults(func=cmd_webdash)

    args = p.parse_args(argv)
    setup_logging(args.log_json, args.log_level)
    return args.func(args)
