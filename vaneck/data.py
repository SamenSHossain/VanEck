"""Data pipeline: fetch, cache, align and validate daily data.

Design rules
------------
* Raw Yahoo history is cached verbatim under ``data/raw`` so every run is
  reproducible offline and the exact inputs are versioned with the code.
* ``MarketData.window(end)`` is the *only* way strategies receive data.  It
  slices every frame to ``<= end`` so a strategy cannot see the future.
* Total-return prices come from Yahoo's ``Adj Close`` (splits + distributions
  reinvested on the ex-date).  ``Close`` is split-adjusted only and is used
  together with ``Dividends`` to compute distribution yields for tax purposes.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from .config import AUX_TICKERS, MACRO_TICKERS, RAW_DIR, SPEC, SUBSTITUTE_TICKERS, UNIVERSE, Spec

log = logging.getLogger(__name__)

RAW_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume",
               "Dividends", "Stock Splits", "Capital Gains"]


def _fname(ticker: str) -> str:
    return ticker.replace("^", "IDX_") + ".csv"


# --------------------------------------------------------------------------- #
# Fetch & cache
# --------------------------------------------------------------------------- #
def fetch_history(ticker: str, retries: int = 4, pause: float = 2.0) -> pd.DataFrame:
    """Download the full daily history of ``ticker`` from Yahoo Finance."""
    import yfinance as yf  # imported lazily so offline use never needs it

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            hist = yf.Ticker(ticker).history(period="max", auto_adjust=False, actions=True)
            if hist is not None and not hist.empty:
                hist = hist.copy()
                idx = pd.DatetimeIndex(hist.index)
                if idx.tz is not None:
                    idx = idx.tz_localize(None)
                hist.index = idx.normalize()
                hist.index.name = "Date"
                for col in RAW_COLUMNS:
                    if col not in hist.columns:
                        hist[col] = 0.0
                hist = hist[RAW_COLUMNS].astype(float)
                hist = hist[~hist.index.duplicated(keep="last")].sort_index()
                return hist
            last_err = RuntimeError("empty response")
        except Exception as exc:  # noqa: BLE001 - network errors of many kinds
            last_err = exc
        time.sleep(pause * (2 ** attempt))
    raise RuntimeError(f"fetch failed for {ticker}: {last_err!r}")


def all_tickers() -> list[str]:
    return list(UNIVERSE) + list(AUX_TICKERS) + list(SUBSTITUTE_TICKERS) + list(MACRO_TICKERS.values())


def update_cache(tickers: list[str] | None = None, raw_dir: Path = RAW_DIR,
                 force: bool = False) -> dict:
    """Fetch any ticker missing from the cache (or all, if ``force``)."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for t in tickers or all_tickers():
        path = raw_dir / _fname(t)
        if path.exists() and not force:
            continue
        log.info("fetching %s", t)
        hist = fetch_history(t)
        hist.to_csv(path, float_format="%.6f")
        manifest[t] = {
            "file": path.name,
            "fetched_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "first": str(hist.index[0].date()),
            "last": str(hist.index[-1].date()),
            "rows": int(len(hist)),
            "source": "yfinance Ticker.history(period='max', auto_adjust=False)",
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def load_raw(ticker: str, raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    path = raw_dir / _fname(ticker)
    if not path.exists():
        raise FileNotFoundError(f"{path} missing - run `python -m vaneck fetch`")
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.DatetimeIndex(df.index).normalize()
    df.index.name = "Date"
    return df[RAW_COLUMNS].astype(float)


# --------------------------------------------------------------------------- #
# Aligned market data
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarketData:
    """Daily data aligned to the US equity trading calendar.

    All price-like frames share one index.  Columns of ``adj_close``/``close``/
    ``volume``/``distributions`` are tradeable tickers; ``macro`` holds the
    regime inputs (``spx``, ``vix``, ``tnx``, ``irx``, ``crb``).
    """

    adj_close: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    distributions: pd.DataFrame
    macro: pd.DataFrame
    splits: pd.DataFrame | None = None   # split ratio on the split date (e.g. 2.0), else 0

    # -- lookahead guard ---------------------------------------------------- #
    def window(self, end: pd.Timestamp | str, start: pd.Timestamp | str | None = None) -> "MarketData":
        """Return a copy containing only rows with ``start <= date <= end``."""
        end = pd.Timestamp(end)
        start = None if start is None else pd.Timestamp(start)
        sl = slice(start, end)
        return MarketData(
            adj_close=self.adj_close.loc[sl],
            close=self.close.loc[sl],
            volume=self.volume.loc[sl],
            distributions=self.distributions.loc[sl],
            macro=self.macro.loc[sl],
            splits=None if self.splits is None else self.splits.loc[sl],
        )

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.adj_close.index

    @property
    def end(self) -> pd.Timestamp:
        return self.adj_close.index[-1]

    def returns(self, tickers: list[str] | None = None) -> pd.DataFrame:
        px = self.adj_close if tickers is None else self.adj_close[list(tickers)]
        return px.pct_change(fill_method=None)

    def dollar_volume(self) -> pd.DataFrame:
        return (self.close * self.volume).fillna(0.0)

    def cash_yield(self, spec: Spec = SPEC) -> pd.Series:
        """Annualized money-market yield (decimal) per trading day."""
        if spec.cash_yield_mode == "fixed":
            return pd.Series(spec.cash_yield_fixed, index=self.index, name="cash_yield")
        if spec.cash_yield_mode == "irx":
            y = (self.macro["irx"] / 100.0).clip(lower=0.0).ffill().bfill()
            return y.rename("cash_yield")
        raise ValueError(f"unknown cash_yield_mode {spec.cash_yield_mode}")

    def subset(self, tickers: list[str]) -> "MarketData":
        cols = list(tickers)
        return replace(self, adj_close=self.adj_close[cols], close=self.close[cols],
                       volume=self.volume[cols], distributions=self.distributions[cols],
                       splits=None if self.splits is None else self.splits[cols])


def build_market_data(spec: Spec = SPEC, raw_dir: Path = RAW_DIR,
                      tradeables: list[str] | None = None) -> MarketData:
    """Assemble the aligned :class:`MarketData` from the raw cache."""
    if tradeables is None:
        tradeables = list(UNIVERSE) + list(AUX_TICKERS)
        tradeables += [t for t in SUBSTITUTE_TICKERS if (raw_dir / _fname(t)).exists()]
    raw = {t: load_raw(t, raw_dir) for t in tradeables}
    macro_raw = {k: load_raw(v, raw_dir) for k, v in MACRO_TICKERS.items()}

    # Trading calendar: SPY's observed trading days (NYSE), from macro_start.
    cal = load_raw("SPY", raw_dir).index
    cal = cal[cal >= pd.Timestamp(spec.macro_start)]

    def _col(name: str, fill_zero: bool = False) -> pd.DataFrame:
        frame = pd.DataFrame({t: raw[t][name] for t in tradeables}).reindex(cal)
        if fill_zero:
            return frame.fillna(0.0)
        return frame

    adj_close = _col("Adj Close")
    close = _col("Close")
    volume = _col("Volume", fill_zero=True)
    dist = (_col("Dividends", fill_zero=True) + _col("Capital Gains", fill_zero=True))

    # Forward-fill isolated gaps *after* inception only (never before listing).
    for t in tradeables:
        first = raw[t].index[0]
        mask = adj_close.index >= first
        adj_close.loc[mask, t] = adj_close.loc[mask, t].ffill()
        close.loc[mask, t] = close.loc[mask, t].ffill()

    macro = pd.DataFrame({k: v["Close"] for k, v in macro_raw.items()}).reindex(cal).ffill()
    splits = _col("Stock Splits", fill_zero=True)
    return MarketData(adj_close=adj_close, close=close, volume=volume,
                      distributions=dist, macro=macro, splits=splits)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def data_quality_report(md: MarketData, raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Per-ticker sanity checks used in Phase 1.1."""
    rows = []
    for t in md.adj_close.columns:
        px = md.adj_close[t].dropna()
        if px.empty:
            rows.append({"ticker": t, "status": "NO DATA"})
            continue
        raw = load_raw(t, raw_dir)
        cal_after = md.index[md.index >= px.index[0]]
        missing = int(len(cal_after) - raw.index.isin(cal_after).sum())
        rets = px.pct_change(fill_method=None).dropna()
        stale = int((px.diff() == 0).astype(int).groupby((px.diff() != 0).cumsum()).sum().max() or 0)
        ratio = (md.adj_close[t] / md.close[t]).dropna()
        ratio_breaks = int((ratio.diff() < -1e-6).sum())
        vol = md.volume[t].loc[px.index]
        rows.append({
            "ticker": t,
            "first": px.index[0].date(),
            "last": px.index[-1].date(),
            "rows": int(len(px)),
            "missing_days_filled": missing,
            "zero_volume_days": int((vol == 0).sum()),
            "max_abs_daily_ret": round(float(rets.abs().max()), 4),
            "n_abs_ret_gt_15pct": int((rets.abs() > 0.15).sum()),
            "longest_stale_run": stale,
            "adj_ratio_breaks": ratio_breaks,
            "n_distributions": int((md.distributions[t] > 0).sum()),
            "avg_dollar_vol_60d_last": float((md.close[t] * md.volume[t]).tail(60).mean()),
        })
    return pd.DataFrame(rows).set_index("ticker")


# --------------------------------------------------------------------------- #
# Synthetic data (tests / offline demos)
# --------------------------------------------------------------------------- #
def make_synthetic(n_days: int = 2000, tickers: list[str] | None = None, seed: int = 0,
                   start: str = "2010-01-04", n_factors: int = 3,
                   daily_vol: float = 0.012, div_yield: float = 0.02) -> MarketData:
    """Factor-model GBM prices with quarterly distributions and fake macro series."""
    rng = np.random.default_rng(seed)
    tickers = tickers or list(UNIVERSE) + list(AUX_TICKERS)
    n = len(tickers)
    idx = pd.bdate_range(start, periods=n_days)
    loadings = rng.normal(0.0, 0.6, size=(n, n_factors))
    factors = rng.normal(0.0, daily_vol, size=(n_days, n_factors))
    idio = rng.normal(0.0, daily_vol * 0.8, size=(n_days, n))
    drift = rng.normal(0.0003, 0.0002, size=n)
    rets = factors @ loadings.T + idio + drift
    adj = pd.DataFrame(100.0 * np.cumprod(1.0 + rets, axis=0), index=idx, columns=tickers)
    # Quarterly distribution on the last business day of Mar/Jun/Sep/Dec.
    dist = pd.DataFrame(0.0, index=idx, columns=tickers)
    close = adj.copy()
    q_end = idx[idx.is_quarter_end | (idx.to_period("Q") != idx.shift(1, "B").to_period("Q"))]
    for d in q_end:
        dist.loc[d] = close.loc[d] * div_yield / 4.0
    # Unadjusted close: Yahoo's Adj Close = Close x prod_{s>t}(1 - div_s / close_{s-1}),
    # so the Adj/Close ratio is <= 1 and non-decreasing through time (equal to 1 on the last bar).
    f = (1.0 - dist / adj).cumprod()
    ratio = f.iloc[-1] / f
    close = adj / ratio
    volume = pd.DataFrame(rng.integers(50_000, 5_000_000, size=(n_days, n)), index=idx, columns=tickers).astype(float)
    spx = 1000.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.011, n_days))
    vix = np.clip(18.0 + 6.0 * np.cumsum(rng.normal(0, 0.1, n_days)) % 12.0, 9.0, 60.0)
    macro = pd.DataFrame({
        "spx": spx,
        "vix": vix,
        "tnx": np.clip(2.5 + np.cumsum(rng.normal(0, 0.02, n_days)), 0.5, 6.0),
        "irx": np.clip(1.5 + np.cumsum(rng.normal(0, 0.02, n_days)), 0.0, 5.5),
        "crb": 300.0 * np.cumprod(1.0 + rng.normal(0.0001, 0.009, n_days)),
    }, index=idx)
    splits = pd.DataFrame(0.0, index=idx, columns=tickers)
    return MarketData(adj_close=adj, close=close, volume=volume, distributions=dist, macro=macro, splits=splits)
