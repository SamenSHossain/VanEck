import numpy as np
import pandas as pd
import pytest

from vaneck.calendar import walk_forward_splits
from vaneck.config import SPEC, UNIVERSE
from vaneck.optimizers import covariance, expected_returns, optimize
from vaneck.signals import drift_scores, signal_drift, signal_momentum, signal_none, top_n


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


def test_top_n_gives_ties_to_the_fund_listed_first():
    s = pd.Series({"A": 0.55, "B": 0.60, "C": 0.55, "D": 0.55, "E": 0.50})
    assert top_n(s, 3) == ["B", "A", "C"]
    assert top_n(s[["E", "D", "C", "B", "A"]], 3) == ["B", "D", "C"]       # only the listed order breaks a tie
    assert top_n(pd.Series({"A": float("nan"), "B": 0.1, "C": 0.1}), 2) == ["B", "C"]
    assert top_n(s, 9) == ["B", "A", "C", "D", "E"]


def test_drift_signal_resolves_a_tie_at_the_cut_by_listed_order(window):
    """Drift scores are counts out of 60, so ties are common; the favoured set must not depend on a sort's whim."""
    tickers = list(UNIVERSE)
    scores = drift_scores(window, tickers)
    # make the third and fourth best funds identical, in both listed orders
    ranked = top_n(scores, len(tickers))
    third, fourth = ranked[2], ranked[3]
    twin = window.adj_close.copy()
    twin[fourth] = twin[third]
    win = type(window)(adj_close=twin, close=window.close, volume=window.volume, distributions=window.distributions,
                       macro=window.macro, splits=window.splits)
    tied = drift_scores(win, tickers)
    assert tied[third] == tied[fourth]
    first_listed = min(third, fourth, key=tickers.index)
    last_listed = max(third, fourth, key=tickers.index)
    pinned = signal_drift(win, tickers).pinned
    assert first_listed in pinned and last_listed not in pinned and len(pinned) == SPEC.top_n
    # listing the funds the other way round hands the place to the other twin
    swapped = [t for t in tickers if t not in (third, fourth)] + [last_listed, first_listed]
    pinned = signal_drift(win, swapped).pinned
    assert last_listed in pinned and first_listed not in pinned
