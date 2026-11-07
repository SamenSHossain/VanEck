import numpy as np
import pandas as pd
import pytest

from vaneck.calendar import walk_forward_splits
from vaneck.config import SPEC, UNIVERSE
from vaneck.optimizers import covariance, expected_returns, optimize
from vaneck.signals import signal_drift, signal_momentum, signal_none


@pytest.fixture
def window(synth):
    s = walk_forward_splits(synth.index, synth.index[0], synth.index[-1])[-1]
    return synth.window(end=s.signal_date, start=s.train_start)


def _check_bounds(w, pinned, bounds):
    assert w.sum() == pytest.approx(1.0, abs=1e-9)
    for t, v in pinned.items():
        assert w[t] == pytest.approx(v, abs=1e-9)
    for t, (lo, hi) in bounds.items():
        assert lo - 1e-9 <= w[t] <= hi + 1e-9


def test_minvar_respects_bounds_and_beats_equal_weight(window):
    tickers = list(UNIVERSE)
    cov = covariance(window, tickers)
    sig = signal_none(window, tickers)
    w = optimize("MinVar", cov, None, sig.pinned, sig.bounds)
    _check_bounds(w, sig.pinned, sig.bounds)
    ew = pd.Series(1.0 / len(tickers), index=tickers)
    assert w @ cov @ w <= ew @ cov @ ew + 1e-12


def test_maxsharpe_respects_bounds_and_beats_minvar_sharpe(window):
    tickers = list(UNIVERSE)
    cov = covariance(window, tickers)
    mu = expected_returns(window, tickers)
    sig = signal_none(window, tickers)
    w_ms = optimize("MaxSharpe", cov, mu, sig.pinned, sig.bounds)
    w_mv = optimize("MinVar", cov, None, sig.pinned, sig.bounds)
    _check_bounds(w_ms, sig.pinned, sig.bounds)
    sr = lambda w: (mu @ w) / np.sqrt(w @ cov @ w)
    if (mu > 0).any():
        assert sr(w_ms) >= sr(w_mv) - 1e-9


def test_maxsharpe_falls_back_to_minvar_when_all_mu_negative(window):
    tickers = list(UNIVERSE)
    cov = covariance(window, tickers)
    mu = pd.Series(-0.1, index=tickers)
    sig = signal_none(window, tickers)
    w_ms = optimize("MaxSharpe", cov, mu, sig.pinned, sig.bounds)
    w_mv = optimize("MinVar", cov, None, sig.pinned, sig.bounds)
    pd.testing.assert_series_equal(w_ms, w_mv, atol=1e-8, rtol=0)


def test_momentum_pins_top3_at_cap(window):
    tickers = list(UNIVERSE)
    sig = signal_momentum(window, tickers)
    assert len(sig.pinned) == SPEC.top_n
    assert all(v == SPEC.w_max for v in sig.pinned.values())
    top = sig.scores.sort_values(ascending=False).index[: SPEC.top_n]
    assert set(top) == set(sig.pinned)
    cov = covariance(window, tickers)
    w = optimize("MinVar", cov, None, sig.pinned, sig.bounds)
    _check_bounds(w, sig.pinned, sig.bounds)


def test_drift_weights_in_band(window):
    tickers = list(UNIVERSE)
    sig = signal_drift(window, tickers)
    assert all(SPEC.drift_lo - 1e-12 <= v <= SPEC.drift_hi + 1e-12 for v in sig.pinned.values())
    assert (sig.scores.between(0, 1)).all()
    cov = covariance(window, tickers)
    mu = expected_returns(window, tickers)
    w = optimize("MaxSharpe", cov, mu, sig.pinned, sig.bounds)
    _check_bounds(w, sig.pinned, sig.bounds)
