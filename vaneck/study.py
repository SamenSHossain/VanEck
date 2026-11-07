"""Orchestration: run baselines and strategies, evaluate the gates, compute statistics."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import metrics
from .baselines import BASELINES, buy_and_hold_targets, equal_weight
from .calendar import Split, walk_forward_splits
from .config import AUX_TICKERS, SPEC, UNIVERSE, Spec
from .costs import CostModel
from .data import MarketData
from .portfolio import SimResult, build_targets, simulate
from .regimes import regime_calendar
from .stats import annual_test, bonferroni_table, bootstrap_sharpe_diff, monthly_test
from .strategies import make_strategy, strategy_grid, strategy_name

TRADEABLES = list(UNIVERSE) + list(AUX_TICKERS)
PRIMARY_BENCHMARK = "60/40 VanEck (60% EW equity funds / 40% BND)"


@dataclass
class Run:
    name: str
    kind: str                 # baseline | strategy | diagnostic | sensitivity
    pre: SimResult
    post: SimResult

    def summary(self) -> dict:
        s_pre = metrics.perf_summary(self.pre.nav, self.pre.rf)
        s_post = metrics.perf_summary(self.post.nav, self.post.rf)
        years = (self.pre.nav.index[-1] - self.pre.nav.index[0]).days / 365.25
        avg_nav = self.pre.nav.mean()
        return {
            "name": self.name, "kind": self.kind,
            "cagr_pre": s_pre["cagr"], "cagr_post": s_post["cagr"],
            "vol_pre": s_pre["ann_vol"], "vol_post": s_post["ann_vol"],
            "sharpe_pre": s_pre["sharpe"], "sharpe_post": s_post["sharpe"],
            "mdd_pre": s_pre["max_drawdown"], "mdd_post": s_post["max_drawdown"],
            "calmar_post": s_post["calmar"],
            "turnover_pre": metrics.annualized_turnover(self.pre.turnover, self.pre.nav),
            "cost_bps_yr": 1e4 * self.pre.costs.sum() / avg_nav / years,
            "tax_pct_yr": 100.0 * self.post.taxes_paid.sum() / self.post.nav.mean() / years,
            "sharpe_tax_haircut": (s_post["sharpe"] - s_pre["sharpe"]) / s_pre["sharpe"] if s_pre["sharpe"] else np.nan,
        }


def _run_targets(md, targets, name, kind, spec, cost_model, end) -> Run:
    pre = simulate(md, targets, spec=spec, taxed=False, cost_model=cost_model, end=end, name=name)
    post = simulate(md, targets, spec=spec, taxed=True, cost_model=cost_model, end=end, name=name)
    return Run(name=name, kind=kind, pre=pre, post=post)


def run_fn(md, splits, fn, name, kind, spec=SPEC, cost_model=None, end=None) -> Run:
    cost_model = cost_model or CostModel(spec)
    tgt = build_targets(md, splits, fn, TRADEABLES)
    return _run_targets(md, tgt, name, kind, spec, cost_model, end or spec.study_end)


def run_baselines(md, splits, spec=SPEC, cost_model=None, end=None) -> dict[str, Run]:
    cost_model = cost_model or CostModel(spec)
    end = end or spec.study_end
    runs = {name: run_fn(md, splits, fn, name, "baseline", spec, cost_model, end) for name, fn in BASELINES.items()}
    bh = buy_and_hold_targets(splits[0].exec_date, "SMH").reindex(columns=TRADEABLES).fillna(0.0)
    runs["Buy&Hold SMH"] = _run_targets(md, bh, "Buy&Hold SMH", "baseline", spec, cost_model, end)
    return runs


def run_strategies(md, splits, spec=SPEC, cost_model=None, end=None, overlay=True) -> dict[str, Run]:
    cost_model = cost_model or CostModel(spec)
    kind = "strategy" if overlay else "diagnostic"
    runs = {}
    for sig, opt, ov in strategy_grid(overlay):
        fn = make_strategy(sig, opt, ov, spec)
        name = strategy_name(sig, opt, ov)
        runs[name] = run_fn(md, splits, fn, name, kind, spec, cost_model, end or spec.study_end)
    return runs


def summary_table(runs: dict[str, Run]) -> pd.DataFrame:
    return pd.DataFrame([r.summary() for r in runs.values()]).set_index("name")


# --------------------------------------------------------------------------- #
# Gate 1
# --------------------------------------------------------------------------- #
def gate1(runs: dict[str, Run], spec: Spec = SPEC) -> pd.DataFrame:
    tbl = summary_table(runs)
    ew, b = tbl.loc["EqualWeight"], tbl.loc[PRIMARY_BENCHMARK]
    classic = tbl.loc["60/40 Classic (SPY/BND)"]
    rows = {
        "EW post-tax Sharpe >= 60/40 (plan def.) + %.2f" % spec.gate1_sharpe_margin: dict(
            value=ew.sharpe_post - b.sharpe_post, threshold=spec.gate1_sharpe_margin,
            ok=bool(ew.sharpe_post - b.sharpe_post >= spec.gate1_sharpe_margin), decisive=True),
        "EW post-tax Sharpe >= 60/40 classic + %.2f (reference)" % spec.gate1_sharpe_margin: dict(
            value=ew.sharpe_post - classic.sharpe_post, threshold=spec.gate1_sharpe_margin,
            ok=bool(ew.sharpe_post - classic.sharpe_post >= spec.gate1_sharpe_margin), decisive=False),
        "EW max drawdown not worse than %.0f%%" % (100 * spec.gate1_mdd_floor): dict(
            value=ew.mdd_post, threshold=spec.gate1_mdd_floor, ok=bool(ew.mdd_post > spec.gate1_mdd_floor), decisive=True),
        "EW one-way turnover < %.0f%%/yr" % (100 * spec.gate1_turnover_max): dict(
            value=ew.turnover_pre, threshold=spec.gate1_turnover_max, ok=bool(ew.turnover_pre < spec.gate1_turnover_max), decisive=True),
        "EW post-tax Sharpe >= %.2f" % spec.gate1_posttax_sharpe_min: dict(
            value=ew.sharpe_post, threshold=spec.gate1_posttax_sharpe_min, ok=bool(ew.sharpe_post >= spec.gate1_posttax_sharpe_min), decisive=True),
        "Tax model matters: |Sharpe haircut| > 20%": dict(
            value=abs(ew.sharpe_tax_haircut), threshold=0.20, ok=bool(abs(ew.sharpe_tax_haircut) > 0.20), decisive=False),
    }
    return pd.DataFrame(rows).T


def gate_passed(table: pd.DataFrame) -> bool:
    dec = table[table["decisive"].astype(bool)]
    return bool(dec["ok"].astype(bool).all())


# --------------------------------------------------------------------------- #
# Phase 2 statistics
# --------------------------------------------------------------------------- #
def phase2_tests(strategies: dict[str, Run], benchmark: Run, spec: Spec = SPEC,
                 taxed: bool = True, n_boot: int = 2000) -> pd.DataFrame:
    rows = {}
    for name, run in strategies.items():
        x = run.post if taxed else run.pre
        y = benchmark.post if taxed else benchmark.pre
        a = annual_test(x.nav, y.nav)
        m = monthly_test(x.nav, y.nav)
        bs = bootstrap_sharpe_diff(x.nav, y.nav, x.rf, n_boot=n_boot)
        rows[name] = {
            "n_years": a["n"], "mean_annual_excess": a["mean_diff"], "t_annual": a["t"], "p_one_sided": a["p_one_sided"],
            "p_monthly_paired": m["p_one_sided"], "sharpe_diff": bs["sharpe_diff"],
            "p_bootstrap_sharpe": bs["p_one_sided"], "sharpe_diff_ci_low": bs["ci_low"], "sharpe_diff_ci_high": bs["ci_high"],
        }
    tbl = bonferroni_table(rows, spec)
    tbl["benchmark"] = benchmark.name
    tbl["basis"] = "post-tax" if taxed else "pre-tax"
    return tbl


def annual_returns_table(runs: dict[str, Run], taxed: bool = True) -> pd.DataFrame:
    cols = {}
    for name, r in runs.items():
        nav = (r.post if taxed else r.pre).nav
        cy = metrics.calendar_year_returns(nav)
        cols[name] = cy["return"].where(cy["full_year"])
    return pd.DataFrame(cols)


# --------------------------------------------------------------------------- #
# Phase 2.4 regime analysis
# --------------------------------------------------------------------------- #
def regime_series(md: MarketData, spec: Spec = SPEC) -> pd.Series:
    return regime_calendar(md.macro, spec)["regime"]


def regime_tables(runs: dict[str, Run], regimes: pd.Series, taxed: bool = True) -> dict[str, pd.DataFrame]:
    out = {}
    for name, r in runs.items():
        sim = r.post if taxed else r.pre
        out[name] = metrics.regime_breakdown(sim.nav, regimes, sim.rf)
    return out


def gate3(regime_tbls: dict[str, pd.DataFrame], winners: list[str], ew_name: str = "EqualWeight",
          spec: Spec = SPEC) -> pd.DataFrame:
    rows = []
    ew = regime_tbls[ew_name]
    for name in winners:
        t = regime_tbls[name]
        for reg in ("Crisis", "Correction", "Bull", "Neutral", "Stagflation"):
            if reg not in t.index or reg not in ew.index:
                rows.append({"strategy": name, "regime": reg, "note": "regime absent in sample"})
                continue
            rows.append({
                "strategy": name, "regime": reg, "days": int(t.loc[reg, "days"]),
                "strategy_sharpe": t.loc[reg, "sharpe"], "ew_sharpe": ew.loc[reg, "sharpe"],
                "strategy_mdd": t.loc[reg, "mdd_concat"], "ew_mdd": ew.loc[reg, "mdd_concat"],
                "mdd_within_tolerance": bool(t.loc[reg, "mdd_concat"] >= ew.loc[reg, "mdd_concat"] - spec.gate3_crisis_mdd_tolerance),
                "reduces_drawdown_vs_ew": bool(t.loc[reg, "mdd_concat"] > ew.loc[reg, "mdd_concat"]),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Sensitivities (reported, never used for decisions)
# --------------------------------------------------------------------------- #
def sensitivities(md: MarketData, splits: list[Split], spec: Spec = SPEC) -> pd.DataFrame:
    from dataclasses import replace
    rows = []
    variants = {
        "primary (IRX cash, FIFO, monthly)": spec,
        "plan-literal cash 4% flat": replace(spec, cash_yield_mode="fixed", cash_yield_fixed=0.04),
        "HIFO lot relief": replace(spec, lot_method="HIFO"),
        "no distribution tax": replace(spec, tax_distributions=False),
    }
    for label, sp in variants.items():
        cm = CostModel(sp)
        for bname, fn in (("EqualWeight", equal_weight), (PRIMARY_BENCHMARK, BASELINES[PRIMARY_BENCHMARK])):
            r = run_fn(md, splits, fn, bname, "sensitivity", sp, cm, sp.study_end)
            s = r.summary(); s["variant"] = label
            rows.append(s)
    # quarterly rebalancing of equal weight (turnover criterion sensitivity)
    q_splits = [s for s in splits if s.exec_date.month in (1, 4, 7, 10)]
    r = run_fn(md, q_splits, equal_weight, "EqualWeight", "sensitivity", spec, CostModel(spec), spec.study_end)
    s = r.summary(); s["variant"] = "quarterly rebalance"
    rows.append(s)
    return pd.DataFrame(rows).set_index(["variant", "name"])


def make_splits(md: MarketData, spec: Spec = SPEC, start=None, end=None) -> list[Split]:
    return walk_forward_splits(md.index, start or spec.fund_start, end or spec.study_end, spec)
