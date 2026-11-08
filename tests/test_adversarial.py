"""Mutation tests of the lookahead guard: the detector must fire on a strategy that cheats."""
import numpy as np
import pandas as pd
from test_lookahead import _perturb_future  # noqa: E402 - pytest prepends tests/ to sys.path

from vaneck import portfolio
from vaneck.calendar import walk_forward_splits
from vaneck.config import AUX_TICKERS, UNIVERSE

TRADEABLES = list(UNIVERSE) + list(AUX_TICKERS)


def _make_peeking(dataset):
    def peeking(win, split):
        # cheats: ignores the window and reads the dataset 20 days *after* the execution date
        fut = dataset.adj_close.loc[split.exec_date:].iloc[:20][list(UNIVERSE)]
        score = np.exp(10.0 * (fut.iloc[-1] / fut.iloc[0] - 1.0))
        return (score / score.sum()).to_dict()
    return peeking


def test_perturbation_detector_catches_a_peeking_strategy(synth):
    splits = walk_forward_splits(synth.index, synth.index[0], synth.index[-1])
    split = splits[len(splits) // 2]
    perturbed = _perturb_future(synth, split.signal_date, seed=7)
    base = portfolio.build_targets(synth, [split], _make_peeking(synth), TRADEABLES).iloc[0]
    other = portfolio.build_targets(perturbed, [split], _make_peeking(perturbed), TRADEABLES).iloc[0]
    assert not np.allclose(base.to_numpy(), other.to_numpy()), "detector failed to notice a strategy using future data"


def test_window_never_contains_rows_after_end(synth):
    rng = np.random.default_rng(0)
    for pos in rng.integers(10, len(synth.index) - 1, size=25):
        end = synth.index[pos]
        win = synth.window(end=end)
        assert win.index.max() <= end and win.macro.index.max() <= end
        assert win.distributions.index.max() <= end
        assert pd.Timestamp(win.end) == end
