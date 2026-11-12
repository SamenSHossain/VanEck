"""Lookahead-bias tests: decisions must be invariant to anything after the signal date."""
import numpy as np
import pandas as pd
import pytest

from vaneck import portfolio, strategies
from vaneck.baselines import equal_weight
from vaneck.calendar import walk_forward_splits
from vaneck.config import AUX_TICKERS, UNIVERSE
from vaneck.data import MarketData


def _perturb_future(md: MarketData, after: pd.Timestamp, seed: int = 1) -> MarketData:
    rng = np.random.default_rng(seed)
    mask = md.index > after
    adj = md.adj_close.copy()
    close = md.close.copy()
    macro = md.macro.copy()
    noise = np.exp(rng.normal(0, 0.05, size=(mask.sum(), adj.shape[1])))
    adj.loc[mask] = adj.loc[mask].to_numpy() * noise
    close.loc[mask] = close.loc[mask].to_numpy() * noise
    macro.loc[mask] = macro.loc[mask].to_numpy() * np.exp(rng.normal(0, 0.1, size=(mask.sum(), macro.shape[1])))
    return MarketData(adj_close=adj, close=close, volume=md.volume, distributions=md.distributions, macro=macro)


@pytest.mark.parametrize("signal,optimizer,overlay", strategies.strategy_grid(True) + strategies.strategy_grid(False))
def test_weights_invariant_to_future_data(synth, signal, optimizer, overlay):
    splits = walk_forward_splits(synth.index, synth.index[0], synth.index[-1])
    split = splits[len(splits) // 2]
    fn = strategies.make_strategy(signal, optimizer, overlay)
    tradeables = list(UNIVERSE) + list(AUX_TICKERS)
    base = portfolio.build_targets(synth, [split], fn, tradeables).iloc[0]
    other = portfolio.build_targets(_perturb_future(synth, split.signal_date), [split], fn, tradeables).iloc[0]
    pd.testing.assert_series_equal(base, other, atol=1e-12, rtol=0)


def test_window_never_exposes_future(synth):
    splits = walk_forward_splits(synth.index, synth.index[0], synth.index[-1])
    for s in splits[::7]:
        win = synth.window(end=s.signal_date, start=s.train_start)
        assert win.index[-1] <= s.signal_date
        assert win.index[0] >= s.train_start
        assert win.macro.index[-1] <= s.signal_date


def test_perturbing_future_changes_nothing_before_exec_date(synth):
    """Full simulation NAV up to a date must not depend on data after that date."""
    splits = walk_forward_splits(synth.index, synth.index[0], synth.index[-1])
    tradeables = list(UNIVERSE) + list(AUX_TICKERS)
    cut = splits[len(splits) // 2].exec_date
    tgt = portfolio.build_targets(synth, splits, equal_weight, tradeables)
    a = portfolio.simulate(synth, tgt, taxed=True).nav.loc[:cut]
    md2 = _perturb_future(synth, cut)
    tgt2 = portfolio.build_targets(md2, splits, equal_weight, tradeables)
    b = portfolio.simulate(md2, tgt2, taxed=True).nav.loc[:cut]
    pd.testing.assert_series_equal(a, b)
