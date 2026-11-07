"""Data health checks: the gate between a data refresh and any decision.

Each check has a severity. ``block`` means no decision may be taken on this
data; ``warn`` means proceed but surface it. The report is JSON-serialisable so
a scheduler or monitoring system can consume it, and the CLI exit code follows
the overall status.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import AUX_TICKERS, RAW_DIR, SPEC, UNIVERSE, Spec
from .data import MarketData

OK, WARN, BLOCK = "ok", "warn", "block"
_RANK = {OK: 0, WARN: 1, BLOCK: 2}


@dataclass
class Check:
    name: str
    status: str
    value: object = None
    limit: object = None
    detail: str = ""


@dataclass
class HealthReport:
    as_of: str
    checks: list[Check] = field(default_factory=list)

    @property
    def status(self) -> str:
        return max((c.status for c in self.checks), key=lambda s: _RANK[s], default=OK)

    @property
    def blocked(self) -> bool:
        return self.status == BLOCK

    def to_dict(self) -> dict:
        return {"as_of": self.as_of, "status": self.status, "checks": [asdict(c) for c in self.checks]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def to_markdown(self) -> str:
        lines = [f"Data health as of {self.as_of}: **{self.status.upper()}**", "",
                 "| check | status | value | limit | detail |", "|---|---|---|---|---|"]
        for c in self.checks:
            lines.append(f"| {c.name} | {c.status} | {c.value} | {c.limit} | {c.detail} |")
        return "\n".join(lines)


@dataclass(frozen=True)
class HealthLimits:
    recent_rows: int = 5
    jump_warn: float = 0.25          # |daily return| above this in the recent window
    ratio_lookback: int = 60         # adj/close ratio breaks within this window block
    stale_warn_days: int = 5         # calendar days since the last bar
    stale_block_days: int = 10
    cache_warn_days: int = 3         # age of the raw cache
    vix_range: tuple[float, float] = (5.0, 150.0)
    tnx_range: tuple[float, float] = (0.0, 25.0)


def _fmt_list(items, n=6) -> str:
    items = list(items)
    return ", ".join(map(str, items[:n])) + (" ..." if len(items) > n else "")


def check_market_data(md: MarketData, spec: Spec = SPEC, now: pd.Timestamp | None = None,
                      tickers: list[str] | None = None, limits: HealthLimits = HealthLimits(),
                      raw_dir: Path = RAW_DIR) -> HealthReport:
    tickers = tickers or list(UNIVERSE) + list(AUX_TICKERS)
    now = pd.Timestamp(now) if now is not None else pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)
    last = md.index[-1]
    rep = HealthReport(as_of=str(last.date()))
    tail = md.adj_close[tickers].tail(limits.recent_rows)

    # 1. every tradeable has a bar on the latest calendar date
    lagging = [t for t in tickers if md.adj_close[t].last_valid_index() != last]
    rep.checks.append(Check("last_bar_alignment", BLOCK if lagging else OK, len(lagging), 0,
                            f"lagging: {_fmt_list(lagging)}" if lagging else "all tickers priced on the latest bar"))
    # 2. no NaNs in the recent window
    nan_cols = [t for t in tickers if tail[t].isna().any()]
    rep.checks.append(Check("recent_nans", BLOCK if nan_cols else OK, len(nan_cols), 0,
                            f"NaN in last {limits.recent_rows} rows: {_fmt_list(nan_cols)}" if nan_cols else ""))
    # 3. abnormal jumps
    rets = md.adj_close[tickers].pct_change(fill_method=None).tail(limits.recent_rows).abs()
    jumpers = rets.max()[rets.max() > limits.jump_warn]
    rep.checks.append(Check("price_jump", WARN if len(jumpers) else OK,
                            round(float(rets.max().max()), 4) if rets.notna().any().any() else None, limits.jump_warn,
                            f"|return| > {limits.jump_warn:.0%}: {_fmt_list(jumpers.index)}" if len(jumpers) else ""))
    # 4. zero volume on the last bar
    zero_vol = [t for t in tickers if md.volume[t].iloc[-1] == 0]
    rep.checks.append(Check("zero_volume_last_bar", WARN if zero_vol else OK, len(zero_vol), 0, _fmt_list(zero_vol)))
    # 5. adjustment-factor breaks in the recent window (bad dividend records)
    ratio = (md.adj_close[tickers] / md.close[tickers]).tail(limits.ratio_lookback)
    breaks = [t for t in tickers if (ratio[t].diff() < -1e-6).any()]
    rep.checks.append(Check("adjustment_breaks", BLOCK if breaks else OK, len(breaks), 0,
                            f"adj/close ratio fell within {limits.ratio_lookback} rows: {_fmt_list(breaks)}" if breaks else ""))
    # 6. macro sanity
    m = md.macro.iloc[-1]
    bad = []
    if m.isna().any():
        bad.append("NaN: " + _fmt_list(m[m.isna()].index))
    if not (limits.vix_range[0] <= m["vix"] <= limits.vix_range[1]):
        bad.append(f"vix={m['vix']:.2f}")
    if not (limits.tnx_range[0] <= m["tnx"] <= limits.tnx_range[1]):
        bad.append(f"tnx={m['tnx']:.2f}")
    rep.checks.append(Check("macro_sanity", BLOCK if bad else OK, "; ".join(bad) if bad else "ok", "", ""))
    # 7. staleness of the last bar relative to now
    age = (now.normalize() - last.normalize()).days
    st = BLOCK if age > limits.stale_block_days else WARN if age > limits.stale_warn_days else OK
    rep.checks.append(Check("bar_staleness_days", st, age, limits.stale_warn_days,
                            f"last bar {last.date()}, now {now.date()}"))
    # 8. cache age
    manifest = raw_dir / "manifest.json"
    if manifest.exists():
        fetched = max(pd.Timestamp(v["fetched_utc"]) for v in json.loads(manifest.read_text()).values()).tz_localize(None)
        days = max(0.0, (now - fetched).total_seconds() / 86400.0)
        rep.checks.append(Check("cache_age_days", WARN if days > limits.cache_warn_days else OK, round(days, 1),
                                limits.cache_warn_days, f"fetched {fetched.strftime('%Y-%m-%d %H:%MZ')}"))
    else:
        rep.checks.append(Check("cache_age_days", WARN, None, limits.cache_warn_days, "no manifest"))
    # 9. universe coverage for signals (enough history)
    short = [t for t in UNIVERSE if md.adj_close[t].notna().sum() < spec.signal_lookback + 1]
    rep.checks.append(Check("signal_history", BLOCK if short else OK, len(short), 0, _fmt_list(short)))
    return rep
