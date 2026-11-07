import numpy as np
import pandas as pd
import pytest

from vaneck import metrics, portfolio
from vaneck.baselines import buy_and_hold_targets, equal_weight
from vaneck.calendar import walk_forward_splits
from vaneck.config import AUX_TICKERS, SPEC, UNIVERSE, Spec
from vaneck.costs import CostModel


class ZeroCost(CostModel):
    def trade_cost(self, ticker, notional, adv):
        return 0.0


def test_buy_and_hold_matches_price_path(synth):
    spec = Spec(cash_yield_mode="fixed", cash_yield_fixed=0.0)
    tgt = buy_and_hold_targets(synth.index[300], "SMH")
    res = portfolio.simulate(synth, tgt, spec=spec, taxed=False, cost_model=ZeroCost(spec, override_file=None))
    px = synth.adj_close["SMH"].loc[res.nav.index]
    expected = spec.aum * px / px.iloc[0]
    pd.testing.assert_series_equal(res.nav, expected.rename(res.nav.name), rtol=1e-10)
    assert res.turnover.empty
    assert res.trades.shape[0] == 1


def test_equal_weight_rebalances_to_target_and_tracks_turnover(synth):
    spec = Spec(cash_yield_mode="fixed", cash_yield_fixed=0.0)
    splits = walk_forward_splits(synth.index, synth.index[0], synth.index[-1])
    tradeables = list(UNIVERSE) + list(AUX_TICKERS)
    tgt = portfolio.build_targets(synth, splits, equal_weight, tradeables)
    res = portfolio.simulate(synth, tgt, spec=spec, taxed=False, cost_model=ZeroCost(spec, override_file=None))
    for d in tgt.index[1:4]:
        w = res.weights.loc[d, list(UNIVERSE)]
        assert np.allclose(w, 1.0 / len(UNIVERSE), atol=1e-9)
    assert (res.turnover > 0).all() and (res.turnover < 0.2).all()
    # with zero costs and no tax the NAV equals a self-financing EW path
    assert res.costs.sum() == 0.0
    assert res.taxes_paid.empty


def test_post_tax_nav_never_exceeds_pre_tax(synth):
    splits = walk_forward_splits(synth.index, synth.index[0], synth.index[-1])
    tradeables = list(UNIVERSE) + list(AUX_TICKERS)
    tgt = portfolio.build_targets(synth, splits, equal_weight, tradeables)
    pre = portfolio.simulate(synth, tgt, taxed=False)
    post = portfolio.simulate(synth, tgt, taxed=True)
    assert (post.nav <= pre.nav + 1e-6).all()
    assert post.taxes_paid.sum() > 0


def test_metrics_on_known_series():
    idx = pd.bdate_range("2020-01-01", periods=540)
    nav = pd.Series(np.linspace(100, 121, 540), index=idx)
    rf = pd.Series(0.0, index=idx)
    assert metrics.max_drawdown(nav)[0] == pytest.approx(0.0)
    nav2 = pd.Series([100, 110, 99, 120, 90], index=pd.bdate_range("2020-01-01", periods=5), dtype=float)
    mdd, peak, trough = metrics.max_drawdown(nav2)
    assert mdd == pytest.approx(90 / 120 - 1)
    assert peak == nav2.index[3] and trough == nav2.index[4]
    cy = metrics.calendar_year_returns(nav)
    assert cy.loc[2021, "full_year"] and not cy.loc[2020, "full_year"]
    assert np.isfinite(metrics.sharpe(nav, rf))
