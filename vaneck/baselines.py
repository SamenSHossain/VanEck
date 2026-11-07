"""Phase 1.4 baselines: equal weight, two 60/40 definitions, buy-and-hold SMH."""
from __future__ import annotations

import pandas as pd

from .calendar import Split
from .config import EQUITY_FUNDS, SPEC, UNIVERSE
from .data import MarketData


def equal_weight(win: MarketData, split: Split, tickers=UNIVERSE, cap: float = SPEC.scaffold_cap) -> dict:
    w = 1.0 / len(tickers)
    if w > cap:
        raise ValueError("equal weight exceeds scaffold cap")
    return {t: w for t in tickers}


def sixty_forty_vaneck(win: MarketData, split: Split) -> dict:
    """Plan definition: 60% equal-weight VanEck equity funds, 40% bond proxy (BND)."""
    w = {t: 0.60 / len(EQUITY_FUNDS) for t in EQUITY_FUNDS}
    w["BND"] = 0.40
    return w


def sixty_forty_classic(win: MarketData, split: Split) -> dict:
    """Reference: 60% SPY / 40% BND."""
    return {"SPY": 0.60, "BND": 0.40}


def buy_and_hold_targets(exec_date, ticker: str = "SMH") -> pd.DataFrame:
    """A single row: buy once, never rebalance."""
    return pd.DataFrame({ticker: [1.0]}, index=pd.DatetimeIndex([exec_date], name="exec_date"))


BASELINES = {
    "EqualWeight": equal_weight,
    "60/40 VanEck (60% EW equity funds / 40% BND)": sixty_forty_vaneck,
    "60/40 Classic (SPY/BND)": sixty_forty_classic,
}
