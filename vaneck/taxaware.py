"""SPEC section 14: the execution-policy ladder (P0 calendar FIFO ... P3 loss harvesting)."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import numpy as np
import pandas as pd

from . import metrics
from .baselines import equal_weight, sixty_forty_classic, sixty_forty_vaneck
from .calendar import Split
from .config import SPEC, SUBSTITUTES, Spec
from .costs import CostModel
from .data import MarketData
from .execution import ExecutionPolicy, presets
from .portfolio import SimResult, build_targets, simulate
from .strategies import make_strategy
from .study import PRIMARY_BENCHMARK, TRADEABLES

PORTFOLIOS = {
    "EqualWeight": equal_weight,
    PRIMARY_BENCHMARK: sixty_forty_vaneck,
    "60/40 Classic (SPY/BND)": sixty_forty_classic,
    "DriftxMinVar": make_strategy("Drift", "MinVar", False),
}


def _row(portfolio: str, policy: ExecutionPolicy, pre: SimResult, post: SimResult, p0_pre: SimResult | None) -> dict:
    s_pre, s_post = metrics.perf_summary(pre.nav, pre.rf), metrics.perf_summary(post.nav, post.rf)
    years = (pre.nav.index[-1] - pre.nav.index[0]).days / 365.25
    te = np.nan
    if p0_pre is not None:
        d = pre.returns - p0_pre.returns.reindex(pre.returns.index)
        te = float(d.std(ddof=1) * np.sqrt(252))
    realized = post.ledger.realized_by_year
    return {
        "portfolio": portfolio, "policy": policy.name,
        "cagr_pre": s_pre["cagr"], "cagr_post": s_post["cagr"], "vol_post": s_post["ann_vol"],
        "sharpe_pre": s_pre["sharpe"], "sharpe_post": s_post["sharpe"], "mdd_post": s_post["max_drawdown"],
        "turnover_pre": metrics.annualized_turnover(pre.turnover, pre.nav),
        "cost_bps_yr": 1e4 * pre.costs.sum() / pre.nav.mean() / years,
        "tax_pct_yr": 100.0 * post.taxes_paid.sum() / post.nav.mean() / years,
        "realized_gains_total": float(sum(realized.values())),
        "harvests": int(post.harvests.sum()) if post.harvests is not None else 0,
        "tracking_error_vs_p0_pre": te,
    }


def run_ladder(md: MarketData, splits: list[Split], spec: Spec = SPEC, policies: dict[str, ExecutionPolicy] | None = None,
               portfolios: dict | None = None, cost_model: CostModel | None = None) -> tuple[pd.DataFrame, dict]:
    policies = policies or presets(spec.lot_method)
    portfolios = portfolios or PORTFOLIOS
    cost_model = cost_model or CostModel(spec)
    rows, sims = [], {}
    for pname, fn in portfolios.items():
        tgt = build_targets(md, splits, fn, TRADEABLES)
        p0_pre = None
        for label, pol in policies.items():
            kw: dict[str, Any] = dict(spec=spec, cost_model=cost_model, end=spec.study_end, name=f"{pname}|{label}", policy=pol)
            pre = simulate(md, tgt, taxed=False, **kw)
            post = simulate(md, tgt, taxed=True, **kw)
            if p0_pre is None:
                p0_pre = pre
            sims[(pname, label)] = (pre, post)
            rows.append(_row(pname, pol, pre, post, p0_pre))
    tbl = pd.DataFrame(rows).set_index(["portfolio", "policy"])
    base = tbl.xs(list(policies)[0], level="policy")["sharpe_post"]
    tbl["sharpe_post_gain_vs_p0"] = [r.sharpe_post - base[cast(tuple, label)[0]] for label, r in tbl.iterrows()]
    return tbl, sims


def gate1_margins(tbl: pd.DataFrame, spec: Spec = SPEC) -> pd.DataFrame:
    rows = []
    for pol in tbl.index.get_level_values("policy").unique():
        ew = tbl.loc[("EqualWeight", pol)]
        b = tbl.loc[(PRIMARY_BENCHMARK, pol)]
        margin = ew.sharpe_post - b.sharpe_post
        rows.append({"policy": pol, "ew_sharpe_post": ew.sharpe_post, "benchmark_sharpe_post": b.sharpe_post,
                     "margin": margin, "threshold": spec.gate1_sharpe_margin,
                     "gate1_pass": bool(margin >= spec.gate1_sharpe_margin)})
    return pd.DataFrame(rows).set_index("policy")


def sensitivities(md: MarketData, splits: list[Split], spec: Spec = SPEC) -> pd.DataFrame:
    """Short-term tax rate 46% and the harvesting map without the physical-gold pair."""
    pols = presets(spec.lot_method)
    p0, p3 = pols["P0 calendar, FIFO"], pols["P3 + loss harvesting"]
    ports = {k: PORTFOLIOS[k] for k in ("EqualWeight", PRIMARY_BENCHMARK)}
    rows = []
    st_spec = replace(spec, tax_rate_short=0.46)
    tbl_st, _ = run_ladder(md, splits, st_spec, {p0.name: p0, p3.name: p3}, ports, CostModel(st_spec))
    for label, r in tbl_st.iterrows():
        p, pol = cast(tuple, label)
        rows.append({"variant": "short-term gains taxed at 46%", "portfolio": p, "policy": pol,
                     "sharpe_post": r.sharpe_post, "cagr_post": r.cagr_post, "tax_pct_yr": r.tax_pct_yr})
    no_gold = {k: v for k, v in SUBSTITUTES.items() if k != "OUNZ"}
    p3b = p3.with_(name="P3 without OUNZ/IAU pair", substitutes=tuple(no_gold.items()))
    tbl_ng, _ = run_ladder(md, splits, spec, {p0.name: p0, p3b.name: p3b}, ports, CostModel(spec))
    for label, r in tbl_ng.iterrows():
        p, pol = cast(tuple, label)
        rows.append({"variant": "harvesting without OUNZ/IAU", "portfolio": p, "policy": pol,
                     "sharpe_post": r.sharpe_post, "cagr_post": r.cagr_post, "tax_pct_yr": r.tax_pct_yr})
    return pd.DataFrame(rows).set_index(["variant", "portfolio", "policy"])
