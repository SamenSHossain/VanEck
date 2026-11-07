"""Transaction-cost model (Phase 1.3).

one_way_cost = (quoted_spread / 2 + slippage) * |notional| + regulatory fee per trade

The quoted spread is looked up by the fund's trailing average dollar volume
(``cost[fund][volume_level]``) unless an explicit override is supplied in
``data/spreads_override.csv`` (columns: ticker, spread_bps) from live quotes.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import DATA_DIR, SPEC, Spec
from .data import MarketData


class CostModel:
    def __init__(self, spec: Spec = SPEC, overrides: dict[str, float] | None = None,
                 override_file: Path | None = DATA_DIR / "spreads_override.csv",
                 impact_bps_per_pct_adv: float = 0.0):
        self.spec = spec
        self.overrides = dict(overrides or {})
        self.impact = impact_bps_per_pct_adv
        if override_file is not None and Path(override_file).exists():
            tbl = pd.read_csv(override_file)
            self.overrides.update({str(r.ticker): float(r.spread_bps) for r in tbl.itertuples()})

    # -- lookups ------------------------------------------------------------ #
    def spread_bps(self, ticker: str, adv_dollars: float) -> float:
        if ticker in self.overrides:
            return self.overrides[ticker]
        if adv_dollars is None or pd.isna(adv_dollars):
            adv_dollars = 0.0
        for floor, bps in self.spec.spread_tiers_bps:
            if adv_dollars >= floor:
                return bps
        return self.spec.spread_tiers_bps[-1][1]

    def one_way_bps(self, ticker: str, adv_dollars: float, notional: float = 0.0) -> float:
        bps = self.spread_bps(ticker, adv_dollars) / 2.0 + self.spec.slippage_bps
        if self.impact and adv_dollars and adv_dollars > 0:
            bps += self.impact * (100.0 * abs(notional) / adv_dollars)
        return bps

    def trade_cost(self, ticker: str, notional: float, adv_dollars: float) -> float:
        """Dollar cost of trading ``notional`` dollars (either direction)."""
        n = abs(float(notional))
        if n <= 0.0:
            return 0.0
        return n * self.one_way_bps(ticker, adv_dollars, n) / 1e4 + self.spec.fee_per_trade

    # -- data helpers -------------------------------------------------------- #
    def adv_table(self, md: MarketData) -> pd.DataFrame:
        """Trailing average dollar volume, lagged one day (known at the signal date)."""
        dv = md.dollar_volume()
        return dv.rolling(self.spec.adv_lookback, min_periods=20).mean().shift(1)

    def cost_table(self, md: MarketData, asof) -> pd.DataFrame:
        adv = self.adv_table(md).loc[:asof].iloc[-1]
        rows = []
        for t, a in adv.items():
            rows.append({"ticker": t, "adv_dollars": a, "spread_bps": self.spread_bps(t, a),
                         "one_way_bps": self.one_way_bps(t, a),
                         "round_trip_bps": 2 * self.one_way_bps(t, a)})
        return pd.DataFrame(rows).set_index("ticker")
