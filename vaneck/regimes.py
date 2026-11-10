"""Phase 2.1: ex-ante regime classification (locked before any backtest).

Precedence when several definitions hold on the same day:
    Crisis > Stagflation > Bull > Correction > Neutral
"Neutral" collects days matching none of the four plan definitions and is
treated like Bull (hold the allocation rule).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import SPEC, Spec

REGIMES = ("Bull", "Correction", "Crisis", "Stagflation", "Neutral")


def regime_inputs(macro: pd.DataFrame) -> pd.DataFrame:
    spx, vix, tnx, crb = macro["spx"], macro["vix"], macro["tnx"], macro["crb"]
    years = pd.DatetimeIndex(spx.index).year
    prev_year_close = spx.groupby(years).last().shift(1)
    ytd = spx / prev_year_close.reindex(years).to_numpy() - 1.0
    drawdown = spx / spx.cummax() - 1.0
    lag_dates = pd.DatetimeIndex(crb.index) - pd.DateOffset(years=1)
    crb_lag = pd.Series(crb.dropna().reindex(lag_dates, method="ffill").to_numpy(), index=crb.index)
    crb_yoy = crb / crb_lag - 1.0
    return pd.DataFrame({"vix": vix, "spx_ytd": ytd, "spx_drawdown": drawdown,
                         "crb_yoy": crb_yoy, "tnx": tnx})


def classify(inputs: pd.DataFrame, spec: Spec = SPEC) -> pd.Series:
    crisis = (inputs["vix"] > spec.crisis_vix) | (inputs["spx_drawdown"] <= spec.crisis_drawdown)
    stag = (inputs["crb_yoy"] > spec.stag_crb_yoy) & (inputs["tnx"] > spec.stag_tnx)
    bull = (inputs["vix"] < spec.bull_vix) & (inputs["spx_ytd"] > 0.0)
    corr = (inputs["vix"] >= spec.corr_vix_lo) & (inputs["vix"] <= spec.corr_vix_hi) & \
           (inputs["spx_ytd"].abs() <= spec.corr_ytd_band)
    label = np.select([crisis.to_numpy(), stag.to_numpy(), bull.to_numpy(), corr.to_numpy()],
                      ["Crisis", "Stagflation", "Bull", "Correction"], default="Neutral")
    out = pd.Series(label, index=inputs.index, name="regime", dtype="object")
    out[inputs[["vix", "spx_ytd", "spx_drawdown"]].isna().any(axis=1)] = np.nan
    return out


def regime_calendar(macro: pd.DataFrame, spec: Spec = SPEC) -> pd.DataFrame:
    inputs = regime_inputs(macro)
    inputs["regime"] = classify(inputs, spec)
    return inputs


def regime_at(macro_window: pd.DataFrame, spec: Spec = SPEC) -> str:
    """Regime on the last day of a (strictly past) macro window."""
    cal = regime_calendar(macro_window, spec)
    r = cal["regime"].iloc[-1]
    return "Neutral" if pd.isna(r) else str(r)


def episodes(regimes: pd.Series) -> pd.DataFrame:
    r = regimes.dropna()
    change = (r != r.shift(1)).cumsum()
    rows = []
    for _, chunk in r.groupby(change):
        rows.append({"regime": chunk.iloc[0], "start": chunk.index[0].date(),
                     "end": chunk.index[-1].date(), "days": len(chunk)})
    return pd.DataFrame(rows)
