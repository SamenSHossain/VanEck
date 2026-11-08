"""SPEC section 16: shared liquidity model and the capacity curve."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .baselines import equal_weight, sixty_forty_vaneck
from .calendar import Split
from .config import SPEC, Spec
from .costs import CostModel
from .data import MarketData
from .execution import ExecutionPolicy
from .portfolio import build_targets, simulate
from .strategies import make_strategy
from .study import PRIMARY_BENCHMARK, TRADEABLES

AUM_GRID = (1e6, 3e6, 1e7, 3e7, 1e8, 3e8, 1e9)
PORTFOLIOS = {
    "EqualWeight": equal_weight,
    PRIMARY_BENCHMARK: sixty_forty_vaneck,
    "DriftxMinVar": make_strategy("Drift", "MinVar", False),
    "MomentumxMaxSharpe": make_strategy("Momentum", "MaxSharpe", False),
}


class FrictionlessCostModel(CostModel):
    def trade_cost(self, ticker, notional, adv_dollars, daily_vol=None):
        return 0.0


def capacity_curve(md: MarketData, splits: list[Split], spec: Spec = SPEC, aums=AUM_GRID,
                   portfolios: dict | None = None, max_participation: float = 0.10, impact_coef: float = 1.0,
                   drag_limit_bps: float = 25.0, te_limit_bps: float = 50.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pre-tax friction drag and tracking error against a frictionless run across an AUM grid."""
    portfolios = portfolios or PORTFOLIOS
    spec_imp = replace(spec, impact_coef=impact_coef)
    cm = CostModel(spec_imp)
    free_cm = FrictionlessCostModel(spec)
    policy = ExecutionPolicy(name=f"cap {max_participation:.0%}", max_participation=max_participation)
    rows = []
    for pname, fn in portfolios.items():
        tgt = build_targets(md, splits, fn, TRADEABLES)
        free = simulate(md, tgt, spec=spec, taxed=False, cost_model=free_cm, end=spec.study_end, name="frictionless")
        years = (free.nav.index[-1] - free.nav.index[0]).days / 365.25
        for aum in aums:
            sim = simulate(md, tgt, spec=spec_imp, taxed=False, cost_model=cm, end=spec.study_end,
                           initial=aum, name=f"{pname}|{aum:.0e}", policy=policy)
            d = sim.returns - free.returns.reindex(sim.returns.index)
            n_trades = int((sim.trades != 0).sum().sum())
            rows.append({
                "portfolio": pname, "aum": aum,
                "drag_bps_yr": 1e4 * sim.costs.sum() / sim.nav.mean() / years,
                "tracking_error_bps_yr": 1e4 * float(d.std(ddof=1) * np.sqrt(252)),
                "cum_shortfall": float(sim.nav.iloc[-1] / sim.nav.iloc[0] - free.nav.iloc[-1] / free.nav.iloc[0]),
                "cagr": float((sim.nav.iloc[-1] / sim.nav.iloc[0]) ** (1 / years) - 1),
                "turnover_pre": float(sim.turnover.sum() / years),
                "capped_trade_share": float(sim.capped.sum() / n_trades) if (n_trades and sim.capped is not None) else 0.0,
            })
    curve = pd.DataFrame(rows).set_index(["portfolio", "aum"])
    cap_rows = []
    for pname in portfolios:
        c = curve.loc[pname]
        drag_hit = bool((c["drag_bps_yr"] > drag_limit_bps).any())
        te_hit = bool((c["tracking_error_bps_yr"] > te_limit_bps).any())
        ok = c[(c["drag_bps_yr"] <= drag_limit_bps) & (c["tracking_error_bps_yr"] <= te_limit_bps)]
        cap_rows.append({"portfolio": pname, "capacity_aum": float(ok.index.max()) if len(ok) else 0.0,
                         "drag_limit_bps": drag_limit_bps, "te_limit_bps": te_limit_bps,
                         "binding": "+".join(x for x, hit in (("drag", drag_hit), ("tracking", te_hit)) if hit)})
    return curve, pd.DataFrame(cap_rows).set_index("portfolio")
