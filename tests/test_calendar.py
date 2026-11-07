import pandas as pd

from vaneck.calendar import rebalance_schedule, walk_forward_splits
from vaneck.config import SPEC


def test_exec_is_first_trading_day_and_signal_is_prior_close(synth):
    sched = rebalance_schedule(synth.index, synth.index[0], synth.index[-1])
    for exec_date, row in sched.iterrows():
        same_month = synth.index[(synth.index.year == exec_date.year) & (synth.index.month == exec_date.month)]
        assert exec_date == same_month[0]
        pos = synth.index.get_loc(exec_date)
        assert row["signal_date"] == synth.index[pos - 1]
        assert row["signal_date"] < exec_date


def test_jan_rebalance_uses_only_prior_year_data():
    idx = pd.bdate_range("2018-01-01", "2021-12-31")
    idx = idx[idx != pd.Timestamp("2021-01-01")]  # New Year's Day holiday
    sched = rebalance_schedule(idx, "2021-01-01", "2021-01-31")
    exec_date = sched.index[0]
    assert exec_date == pd.Timestamp("2021-01-04")
    assert sched.loc[exec_date, "signal_date"] == pd.Timestamp("2020-12-31")


def test_walk_forward_windows(synth):
    splits = walk_forward_splits(synth.index, synth.index[0], synth.index[-1])
    assert splits, "expected at least one split"
    for s in splits:
        assert s.signal_date < s.exec_date
        assert (s.signal_date - s.train_start).days >= 365 * SPEC.train_years - 3
        assert s.train_start >= synth.index[0] - pd.Timedelta(days=3)
        assert s.test_end >= s.exec_date
    # monthly roll
    months = [(s.exec_date.year, s.exec_date.month) for s in splits]
    assert len(months) == len(set(months))
