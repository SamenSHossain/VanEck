"""Performance statistics."""
from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def daily_returns(nav: pd.Series) -> pd.Series:
    return nav.pct_change(fill_method=None).dropna()


def excess_returns(nav: pd.Series, rf_annual: pd.Series) -> pd.Series:
    r = daily_returns(nav)
    rf = rf_annual.reindex(r.index).ffill().fillna(0.0) / TRADING_DAYS
    return r - rf


def cagr(nav: pd.Series) -> float:
    nav = nav.dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    if years <= 0 or nav.iloc[0] <= 0:
        return float("nan")
    return float((nav.iloc[-1] / nav.iloc[0]) ** (1.0 / years) - 1.0)


def ann_vol(nav: pd.Series) -> float:
    return float(daily_returns(nav).std(ddof=1) * np.sqrt(TRADING_DAYS))


def sharpe(nav: pd.Series, rf_annual: pd.Series) -> float:
    ex = excess_returns(nav, rf_annual)
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else float("nan")


def sortino(nav: pd.Series, rf_annual: pd.Series) -> float:
    ex = excess_returns(nav, rf_annual)
    down = ex[ex < 0]
    dd = np.sqrt((down ** 2).mean()) if len(down) else np.nan
    return float(ex.mean() / dd * np.sqrt(TRADING_DAYS)) if dd and dd > 0 else float("nan")


def drawdown_series(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1.0


def max_drawdown(nav: pd.Series) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None]:
    dd = drawdown_series(nav)
    if dd.empty:
        return float("nan"), None, None
    trough = dd.idxmin()
    peak = nav.loc[:trough].idxmax()
    return float(dd.min()), cast(pd.Timestamp, peak), cast(pd.Timestamp, trough)


def calendar_year_returns(nav: pd.Series) -> pd.DataFrame:
    """Calendar-year returns with a flag for partial first/last years."""
    nav = nav.dropna()
    out = []
    for label, chunk in nav.groupby(pd.DatetimeIndex(nav.index).year):
        year = int(cast(int, label))
        prev = nav.loc[:chunk.index[0] - pd.Timedelta(days=1)]
        start_val = prev.iloc[-1] if len(prev) else chunk.iloc[0]
        ret = chunk.iloc[-1] / start_val - 1.0
        full = (len(prev) > 0) and (chunk.index[-1] >= pd.Timestamp(f"{year}-12-28"))
        out.append({"year": year, "return": float(ret), "full_year": bool(full)})
    return pd.DataFrame(out).set_index("year")


def monthly_returns(nav: pd.Series) -> pd.Series:
    m = nav.resample("ME").last()
    return m.pct_change(fill_method=None).dropna()


def perf_summary(nav: pd.Series, rf_annual: pd.Series) -> dict:
    mdd, peak, trough = max_drawdown(nav)
    c = cagr(nav)
    return {
        "start": nav.index[0].date(), "end": nav.index[-1].date(),
        "cagr": c, "ann_vol": ann_vol(nav), "sharpe": sharpe(nav, rf_annual),
        "sortino": sortino(nav, rf_annual), "max_drawdown": mdd,
        "calmar": (c / abs(mdd)) if mdd and mdd < 0 else float("nan"),
        "mdd_peak": peak.date() if peak is not None else None,
        "mdd_trough": trough.date() if trough is not None else None,
        "total_return": float(nav.iloc[-1] / nav.iloc[0] - 1.0),
    }


def annualized_turnover(turnover: pd.Series, nav: pd.Series) -> float:
    """Sum of one-way turnover per year (excludes the initial funding trade)."""
    t = turnover.dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    return float(t.sum() / years) if years > 0 else float("nan")


def regime_breakdown(nav: pd.Series, regimes: pd.Series, rf_annual: pd.Series) -> pd.DataFrame:
    """Return / vol / Sharpe / drawdown of a strategy within each regime's days.

    Drawdowns are computed on the regime's days concatenated (``mdd_concat``)
    and as the worst peak-to-trough inside any single contiguous episode
    (``mdd_episode``).
    """
    r = daily_returns(nav)
    rf = rf_annual.reindex(r.index).ffill().fillna(0.0) / TRADING_DAYS
    reg = regimes.reindex(r.index).ffill()
    rows = []
    for name, idx in reg.groupby(reg).groups.items():
        rr = r.loc[idx]
        if len(rr) < 5:
            continue
        ex = rr - rf.loc[idx]
        concat_nav = (1.0 + rr).cumprod()
        mdd_concat = float((concat_nav / concat_nav.cummax() - 1.0).min())
        # contiguous episodes
        episode_id = (reg != reg.shift(1)).cumsum().loc[idx]
        worst = 0.0
        for _, ep in rr.groupby(episode_id):
            n = (1.0 + ep).cumprod()
            worst = min(worst, float((n / n.cummax() - 1.0).min()))
        rows.append({
            "regime": name, "days": int(len(rr)), "share_of_days": len(rr) / len(r),
            "ann_return": float(cast(float, (1.0 + rr).prod()) ** (TRADING_DAYS / len(rr)) - 1.0),
            "ann_vol": float(rr.std(ddof=1) * np.sqrt(TRADING_DAYS)),
            "sharpe": float(ex.mean() / ex.std(ddof=1) * np.sqrt(TRADING_DAYS)) if ex.std(ddof=1) > 0 else np.nan,
            "mdd_concat": mdd_concat, "mdd_episode": worst,
        })
    return pd.DataFrame(rows).set_index("regime")
