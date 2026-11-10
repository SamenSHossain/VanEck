"""Gate 0 / Phase 1.1: is the universe allocatable?"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import cast

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from .config import SPEC, THEME_OF, UNIVERSE, Spec
from .data import MarketData


@dataclass
class Gate0Result:
    corr_full: pd.DataFrame
    avg_pairwise: float
    max_pair: tuple[str, str, float]
    rolling_avg: pd.Series
    rolling_pairs: pd.DataFrame
    clusters: pd.DataFrame
    n_themes: int
    redundant_pairs: list[tuple[str, str, float]]
    checks: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(v["ok"] for v in self.checks.values())

    def summary_table(self) -> pd.DataFrame:
        return pd.DataFrame(self.checks).T


def _upper(corr: pd.DataFrame) -> np.ndarray:
    a = corr.values
    return a[np.triu_indices_from(a, k=1)]


def cluster_themes(corr: pd.DataFrame, cut_corr: float) -> pd.DataFrame:
    dist = np.clip(1.0 - corr.values, 0.0, 2.0)
    np.fill_diagonal(dist, 0.0)
    z = linkage(squareform(dist, checks=False), method="average")
    labels = fcluster(z, t=1.0 - cut_corr, criterion="distance")
    df = pd.DataFrame({"ticker": corr.index, "cluster": labels})
    df["theme"] = df["ticker"].map(THEME_OF)
    return df.sort_values(["cluster", "ticker"]).set_index("ticker")


def run_gate0(md: MarketData, spec: Spec = SPEC, start=None, end=None,
              tickers: tuple[str, ...] = UNIVERSE) -> Gate0Result:
    start = pd.Timestamp(start or spec.fund_start)
    end = pd.Timestamp(end or spec.study_end)
    rets = md.returns(list(tickers)).loc[start:end].dropna(how="any")
    corr = rets.corr()
    up = _upper(corr)
    avg = float(up.mean())
    pairs = list(combinations(tickers, 2))
    pair_vals = [(a, b, float(cast(float, corr.loc[a, b]))) for a, b in pairs]
    max_pair = max(pair_vals, key=lambda x: x[2])

    roll = {}
    for a, b in pairs:
        roll[f"{a}/{b}"] = rets[a].rolling(spec.signal_lookback).corr(rets[b])
    rolling_pairs = pd.DataFrame(roll).dropna(how="all")
    rolling_avg = rolling_pairs.mean(axis=1).rename("avg_pairwise_corr_60d")

    clusters = cluster_themes(corr, spec.gate0_cluster_corr)
    n_themes = int(clusters["cluster"].nunique())
    redundant = [(a, b, v) for a, b, v in pair_vals if v > spec.gate0_redundancy_corr]

    checks = {
        "avg_pairwise_corr < %.2f" % spec.gate0_avg_corr_max: {
            "value": round(avg, 3), "threshold": spec.gate0_avg_corr_max, "ok": avg < spec.gate0_avg_corr_max},
        "n_themes >= %d (rho cut %.2f)" % (spec.gate0_min_themes, spec.gate0_cluster_corr): {
            "value": n_themes, "threshold": spec.gate0_min_themes, "ok": n_themes >= spec.gate0_min_themes},
        "no pair > %.2f" % spec.gate0_redundancy_corr: {
            "value": round(max_pair[2], 3), "threshold": spec.gate0_redundancy_corr, "ok": len(redundant) == 0},
    }
    return Gate0Result(corr_full=corr, avg_pairwise=avg, max_pair=max_pair, rolling_avg=rolling_avg,
                       rolling_pairs=rolling_pairs, clusters=clusters, n_themes=n_themes,
                       redundant_pairs=redundant, checks=checks)
