"""Rebalance schedule and walk-forward splits.

* Execution date  = first trading day of each month (close).
* Signal date     = ``exec_lag_days`` trading days earlier (default: the last
  close of the previous month).  Every decision taken for an execution date may
  only use data with timestamp <= signal date.
* Train window    = ``train_years`` ending at the signal date.
* Test window     = ``test_years`` starting at the execution date.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import SPEC, Spec


def month_first_trading_days(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(index, index=index)
    firsts = s.groupby([index.year, index.month]).min()
    return pd.DatetimeIndex(firsts.values)


def rebalance_schedule(index: pd.DatetimeIndex, start, end, lag: int = SPEC.exec_lag_days) -> pd.DataFrame:
    """Table of (signal_date, exec_date) pairs with ``start <= exec_date <= end``."""
    index = pd.DatetimeIndex(index).sort_values()
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    firsts = month_first_trading_days(index)
    pos = index.get_indexer(firsts)
    rows = []
    for p in pos:
        if p - lag < 0:
            continue
        exec_date = index[p]
        if exec_date < start or exec_date > end:
            continue
        rows.append({"signal_date": index[p - lag], "exec_date": exec_date})
    sched = pd.DataFrame(rows)
    if sched.empty:
        raise ValueError("no rebalance dates in range")
    return sched.set_index("exec_date")


@dataclass(frozen=True)
class Split:
    train_start: pd.Timestamp
    signal_date: pd.Timestamp   # == train_end
    exec_date: pd.Timestamp
    test_end: pd.Timestamp

    @property
    def train_end(self) -> pd.Timestamp:
        return self.signal_date


def first_valid_exec_date(index: pd.DatetimeIndex, data_start, spec: Spec = SPEC) -> pd.Timestamp:
    """First execution date whose full training window lies inside the data."""
    need = pd.Timestamp(data_start) + pd.DateOffset(years=spec.train_years)
    sched = rebalance_schedule(index, index[0], index[-1], spec.exec_lag_days)
    ok = sched[sched["signal_date"] >= need]
    if ok.empty:
        raise ValueError("not enough history for one training window")
    return ok.index[0]


def walk_forward_splits(index: pd.DatetimeIndex, data_start, end, spec: Spec = SPEC) -> list[Split]:
    """Monthly-rolling 3y-train / 1y-test splits anchored on the rebalance schedule."""
    index = pd.DatetimeIndex(index)
    end = pd.Timestamp(end)
    start = first_valid_exec_date(index, data_start, spec)
    sched = rebalance_schedule(index, start, end, spec.exec_lag_days)
    out = []
    for exec_date, row in sched.iterrows():
        sig = row["signal_date"]
        train_start = sig - pd.DateOffset(years=spec.train_years)
        test_end = min(exec_date + pd.DateOffset(years=spec.test_years) - pd.Timedelta(days=1), end)
        out.append(Split(train_start=train_start, signal_date=sig, exec_date=exec_date, test_end=test_end))
    return out
