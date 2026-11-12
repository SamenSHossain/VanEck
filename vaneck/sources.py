"""Secondary data sources for cross-validation (SPEC infrastructure: never trust one vendor).

* FRED (fred.stlouisfed.org) serves the macro inputs: VIX close, 10-year CMT,
  3-month bill, S&P 500 level (10 years). No key needed.
* Stooq is wired as a price provider but currently answers automated requests
  with a JavaScript challenge; the provider detects that and reports itself
  unavailable instead of returning garbage. A keyed vendor (Tiingo, EODHD)
  slots in behind the same interface.
Everything is cached under ``data/secondary`` with a manifest, like the primary cache.
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Protocol

import pandas as pd
import requests

from .config import DATA_DIR

log = logging.getLogger(__name__)
SECONDARY_DIR = DATA_DIR / "secondary"
FRED_SERIES: dict[str, str] = {"vix": "VIXCLS", "tnx": "DGS10", "irx": "DTB3", "spx": "SP500"}
UA = {"User-Agent": "vaneck-research/0.1 (+https://github.com/SamenSHossain/VanEck)"}


class SourceUnavailable(RuntimeError):
    """The provider answered, but not with data (challenge page, HTML error, empty body)."""


class PriceProvider(Protocol):
    name: str

    def fetch(self, ticker: str) -> pd.DataFrame: ...


# --------------------------------------------------------------------------- #
# FRED
# --------------------------------------------------------------------------- #
def fetch_fred(series_id: str, timeout: int = 40) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    text = r.text
    if not text.startswith("observation_date"):
        raise SourceUnavailable(f"FRED {series_id}: unexpected payload {text[:60]!r}")
    df = pd.read_csv(io.StringIO(text), na_values=["."])
    s = pd.Series(df[series_id].astype(float).to_numpy(), index=pd.DatetimeIndex(pd.to_datetime(df["observation_date"])),
                  name=series_id)
    return s.dropna()


# --------------------------------------------------------------------------- #
# Stooq (interface kept; provider currently blocked by a JS challenge)
# --------------------------------------------------------------------------- #
class StooqProvider:
    name = "stooq"

    def fetch(self, ticker: str, timeout: int = 40) -> pd.DataFrame:
        sym = ticker.lower() if ticker.startswith("^") else f"{ticker.lower()}.us"
        r = requests.get(f"https://stooq.com/q/d/l/?s={sym}&i=d", headers=UA, timeout=timeout)
        r.raise_for_status()
        if r.text.lstrip().startswith("<") or "Date,Open" not in r.text[:200]:
            raise SourceUnavailable("stooq returned a challenge/HTML page instead of CSV")
        df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"]).set_index("Date").sort_index()
        return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


PROVIDERS: dict[str, PriceProvider] = {"stooq": StooqProvider()}


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def update_secondary_cache(secondary_dir: Path = SECONDARY_DIR, price_tickers: list[str] | None = None,
                           providers: tuple[str, ...] = ("stooq",)) -> dict:
    """Refresh FRED series and try each price provider; failures are recorded, not raised."""
    secondary_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = secondary_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    now = pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    for name, sid in FRED_SERIES.items():
        try:
            s = fetch_fred(sid)
            s.to_csv(secondary_dir / f"fred_{sid}.csv", header=True)
            manifest[f"fred:{sid}"] = {"status": "ok", "fetched_utc": now, "first": str(s.index[0].date()),
                                       "last": str(s.index[-1].date()), "rows": int(len(s)), "maps_to": name}
        except Exception as exc:  # noqa: BLE001
            manifest[f"fred:{sid}"] = {"status": "failed", "fetched_utc": now, "error": repr(exc)[:200]}
            log.warning("FRED %s failed: %r", sid, exc)
    for pname in providers:
        prov = PROVIDERS[pname]
        for t in price_tickers or []:
            try:
                df = prov.fetch(t)
                df.to_csv(secondary_dir / f"{pname}_{t.replace('^', 'IDX_')}.csv")
                manifest[f"{pname}:{t}"] = {"status": "ok", "fetched_utc": now, "first": str(df.index[0].date()),
                                            "last": str(df.index[-1].date()), "rows": int(len(df))}
            except Exception as exc:  # noqa: BLE001
                manifest[f"{pname}:{t}"] = {"status": "unavailable", "fetched_utc": now, "error": repr(exc)[:200]}
                log.warning("%s %s unavailable: %r", pname, t, exc)
                break  # one challenge page means the provider is down; do not hammer it
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def load_secondary(secondary_dir: Path = SECONDARY_DIR) -> dict:
    out: dict = {"fred": {}, "prices": {}}
    for name, sid in FRED_SERIES.items():
        p = secondary_dir / f"fred_{sid}.csv"
        if p.exists():
            s = pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0].astype(float)
            s.index = pd.DatetimeIndex(s.index)
            out["fred"][name] = s
    for p in secondary_dir.glob("stooq_*.csv"):
        t = p.stem.replace("stooq_", "").replace("IDX_", "^")
        out["prices"][t] = pd.read_csv(p, index_col=0, parse_dates=True)
    manifest = secondary_dir / "manifest.json"
    out["manifest"] = json.loads(manifest.read_text()) if manifest.exists() else {}
    return out
