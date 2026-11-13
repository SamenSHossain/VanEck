"""Pre-registered rules from docs/SPEC.md that only the frozen results protected, or that nothing protected.

Each test pins a rule directly, so a change to it fails here with a readable message instead of surfacing (or
not) as a moved number in tests/test_golden.py.
"""
import inspect
import json

import numpy as np
import pandas as pd
import pytest

from vaneck import audit, golden, liquidity, monitor, portfolio, stats, study
from vaneck.config import SPEC, UNIVERSE, Spec
from vaneck.costs import CostModel
from vaneck.data import make_synthetic
from vaneck.execution import ExecutionPolicy, HoldingView, plan
from vaneck.health import HealthLimits
from vaneck.regimes import classify
from vaneck.risk import RiskLimits
from vaneck.strategies import apply_regime
from vaneck.universe import run_gate0


# --------------------------------------------------------------------------------------------------------- #
# Values stated in SPEC sections 3, 5-8, 11, 13
# --------------------------------------------------------------------------------------------------------- #
def test_spec_values_match_the_document():
    s = SPEC
    assert (s.fund_start, s.study_end, s.holdout_start) == ("2014-05-16", "2024-12-31", "2025-01-01")
    assert (s.train_years, s.test_years, s.roll_months, s.exec_lag_days, s.signal_lookback) == (3, 1, 1, 1, 60)
    # section 3 (Gate 0) and section 11 (Gate 1)
    gate0 = (s.gate0_avg_corr_max, s.gate0_min_themes, s.gate0_redundancy_corr, s.gate0_cluster_corr)
    assert gate0 == (0.75, 5, 0.95, 0.60)
    gate1 = (s.gate1_sharpe_margin, s.gate1_mdd_floor, s.gate1_turnover_max, s.gate1_posttax_sharpe_min)
    assert gate1 == (0.20, -0.40, 0.15, 0.30)
    assert s.gate3_crisis_mdd_tolerance == 0.05
    # section 5: spread tiers by trailing dollar volume, slippage and fee
    assert s.spread_tiers_bps == ((500e6, 2.0), (100e6, 4.0), (10e6, 10.0), (1e6, 20.0), (0.0, 40.0))
    assert (s.slippage_bps, s.fee_per_trade, s.adv_lookback) == (0.5, 0.001, 60)
    # section 6
    assert (s.tax_rate, s.long_term_days, s.lot_method, s.wash_sale_days, s.wash_sale_groups) == (0.37, 365, "FIFO", 30, ())
    assert s.tax_distributions is True
    # section 7
    assert (s.crisis_vix, s.crisis_drawdown, s.stag_crb_yoy, s.stag_tnx) == (25.0, -0.20, 0.20, 4.0)
    assert (s.bull_vix, s.corr_vix_lo, s.corr_vix_hi, s.corr_ytd_band, s.correction_scale) == (15.0, 15.0, 25.0, 0.10, 0.75)
    assert s.hedge_basket == ("OUNZ", "GDX", "VGIT") and s.stagflation_tilt == ("ANGL", "GDX", "FLTR")
    # section 8
    assert (s.w_min, s.w_max, s.top_n, s.drift_lo, s.drift_hi, s.other_cap) == (0.05, 0.20, 3, 0.15, 0.20, 0.10)
    # section 9
    assert (s.n_tests, s.alpha) == (6, 0.05)


def test_operational_limits_match_section_13():
    r = RiskLimits()
    assert (r.max_weight, r.max_gross, r.max_turnover_one_way, r.max_participation) == (0.20, 1.0, 0.75, 0.10)
    rest = (r.min_order_notional, r.min_cash, r.max_staleness_days, r.drawdown_halt, r.cost_reserve_bps)
    assert rest == (500.0, 0.0, 5, -0.25, 10.0)
    h = HealthLimits()
    health = (h.recent_rows, h.jump_warn, h.ratio_lookback, h.stale_warn_days, h.stale_block_days, h.cache_warn_days)
    assert health == (5, 0.25, 60, 5, 10, 3)
    assert h.vix_range == (5.0, 150.0) and h.tnx_range == (0.0, 25.0)
    assert inspect.signature(monitor.monitor).parameters["tracking_tolerance_per_year"].default == 0.01


def test_harvesting_thresholds_match_section_14():
    p = ExecutionPolicy()
    assert (p.harvest_min_loss_pct, p.harvest_min_loss_equity_bps, p.cash_deploy_threshold) == (0.05, 5.0, 0.01)
    assert (p.harvest_no_recent_buy_days, p.no_rebuy_days) == (31, 31)


# --------------------------------------------------------------------------------------------------------- #
# Gate verdicts (sections 3 and 11)
# --------------------------------------------------------------------------------------------------------- #
def test_a_gate_passes_only_when_every_decisive_criterion_passes():
    tbl = pd.DataFrame({"ok": [True, True, False], "decisive": [True, True, False]})
    assert study.gate_passed(tbl)                       # a failed non-decisive criterion does not matter
    tbl.loc[1, "ok"] = False
    assert not study.gate_passed(tbl)                   # one failed decisive criterion fails the gate


def test_gate0_fails_on_a_redundant_pair_and_records_its_thresholds():
    md = make_synthetic(n_days=900, seed=5)
    res = run_gate0(md, SPEC, start=md.index[0], end=md.index[-1])
    assert {k: v["threshold"] for k, v in res.checks.items()} == {
        k: v for k, v in zip(res.checks, (SPEC.gate0_avg_corr_max, SPEC.gate0_min_themes, SPEC.gate0_redundancy_corr))}
    twin = make_synthetic(n_days=900, seed=5)
    twin.adj_close["MOAT"] = twin.adj_close["SMH"] * 1.5          # identical returns: a redundant pair
    res = run_gate0(twin, SPEC, start=twin.index[0], end=twin.index[-1])
    assert not res.passed and any({a, b} == {"SMH", "MOAT"} for a, b, _ in res.redundant_pairs)


# --------------------------------------------------------------------------------------------------------- #
# Regimes and overlays (section 7)
# --------------------------------------------------------------------------------------------------------- #
def _regime(vix, ytd, dd=0.0, crb=0.0, tnx=2.0):
    row = pd.DataFrame({"vix": [vix], "spx_ytd": [ytd], "spx_drawdown": [dd], "crb_yoy": [crb], "tnx": [tnx]})
    return classify(row, SPEC).iloc[0]


def test_regime_boundaries_and_precedence():
    assert _regime(25.0, 0.0) == "Correction"           # VIX > 25 is Crisis; exactly 25 is not
    assert _regime(25.01, 0.0) == "Crisis"
    assert _regime(12.0, 0.05, dd=-0.20) == "Crisis"     # drawdown <= -20% is Crisis
    assert _regime(12.0, 0.05, dd=-0.1999) == "Bull"
    assert _regime(15.0, 0.05) == "Correction"           # Bull needs VIX < 15
    assert _regime(14.99, 0.05) == "Bull"
    assert _regime(14.99, 0.0) == "Neutral"              # Bull needs YTD > 0 and Correction needs VIX >= 15
    assert _regime(20.0, 0.10) == "Correction" and _regime(20.0, 0.1001) == "Neutral"
    assert _regime(12.0, 0.05, crb=0.21, tnx=4.1) == "Stagflation"     # Stagflation before Bull
    assert _regime(30.0, 0.05, crb=0.21, tnx=4.1) == "Crisis"          # Crisis before Stagflation
    assert _regime(12.0, 0.05, crb=0.20, tnx=4.1) == "Bull"            # both Stagflation tests are strict


def test_stagflation_and_crisis_overlays():
    md = make_synthetic(n_days=400, seed=1)
    w = pd.Series(1.0 / len(UNIVERSE), index=list(UNIVERSE))
    out = apply_regime(w, "Stagflation", md, SPEC)
    assert all(out[t] == pytest.approx(0.20) for t in ("ANGL", "GDX", "FLTR"))
    rest = out.drop(["ANGL", "GDX", "FLTR"])
    assert rest.sum() == pytest.approx(0.40) and (rest >= 0.05 - 1e-12).all()
    assert rest.nunique() == 1                                          # equal rule weights stay equal
    tilted = apply_regime(pd.Series([0.30, 0.02, 0.02, 0.02, 0.02, 0.02, 0.30, 0.30, 0.0], index=list(UNIVERSE)),
                          "Stagflation", md, SPEC)
    small = tilted.drop(["ANGL", "GDX", "FLTR"])
    assert (small >= 0.05 - 1e-12).all() and small.sum() == pytest.approx(0.40)
    crisis = apply_regime(w, "Crisis", md, SPEC)
    assert set(crisis.index) == {"OUNZ", "GDX", "VGIT"} and (crisis == 0.20).all()      # min(20%, 1/3) each
    assert (apply_regime(w, "Correction", md, SPEC) == w * 0.75).all()


# --------------------------------------------------------------------------------------------------------- #
# Lookahead: every field, not only prices (sections 2 and 5)
# --------------------------------------------------------------------------------------------------------- #
def test_window_cuts_every_field_at_the_signal_date():
    md = make_synthetic(n_days=300, seed=3)
    start, end = md.index[50], md.index[200]
    win = md.window(end=end, start=start)
    for name in ("adj_close", "close", "volume", "distributions", "macro", "splits"):
        frame = getattr(win, name)
        assert frame.index.min() >= start and frame.index.max() <= end, name
        assert len(frame) == 151, name


# --------------------------------------------------------------------------------------------------------- #
# Statistics (sections 10, 15, 16, 18)
# --------------------------------------------------------------------------------------------------------- #
def test_sharpe_difference_bootstrap_is_paired():
    """Two portfolios sharing most of their risk: a paired bootstrap gives a narrow interval, an unpaired one could not."""
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2015-01-01", periods=1000)
    common = rng.normal(0.0003, 0.01, len(idx))
    nav_x = pd.Series(100 * np.cumprod(1 + common + rng.normal(0, 0.0005, len(idx))), index=idx)
    nav_y = pd.Series(100 * np.cumprod(1 + common + rng.normal(0, 0.0005, len(idx))), index=idx)
    rf = pd.Series(0.0, index=idx)
    res = stats.bootstrap_sharpe_diff(nav_x, nav_y, rf, n_boot=500)
    assert res["ci_high"] - res["ci_low"] < 0.3       # unpaired resampling gives a width of about 2.5 here
    assert res["ci_low"] <= res["sharpe_diff"] <= res["ci_high"]
    assert stats.BOOT_BLOCK == 21


def test_newey_west_lag_rule_is_floor_of_three_quarters_n_to_the_third():
    for n, lag in ((90, 3), (64, 3), (1000, 7), (8, 1)):
        assert stats.newey_west_se(np.random.default_rng(n).normal(size=n))[1] == lag, n


def test_reality_check_p_is_the_best_strategys_adjusted_p():
    rng = np.random.default_rng(4)
    diffs = pd.DataFrame(rng.normal(0, 0.01, size=(90, 4)), columns=list("ABCD"))
    diffs["A"] += 0.006                                  # one strategy with a real edge
    out = stats.romano_wolf(diffs, n_boot=500, seed=0)
    best = out["t"].idxmax()
    assert best == "A"
    assert out.attrs["white_reality_check_p"] == out.loc[best, "p_adjusted_stepdown"]
    assert out.attrs["white_reality_check_p"] == out["p_adjusted_stepdown"].min()


def test_capacity_needs_both_limits_at_once():
    aum = [1e6, 3e6, 10e6]
    curve = pd.DataFrame({"portfolio": ["P"] * 3, "aum": aum, "drag_bps_yr": [5.0, 30.0, 10.0],
                          "tracking_error_bps_yr": [10.0, 20.0, 60.0]}).set_index(["portfolio", "aum"])
    cap = liquidity.capacity_table(curve, drag_limit_bps=25.0, te_limit_bps=50.0)
    assert cap.loc["P", "capacity_aum"] == 1e6           # 3M breaks drag, 10M breaks tracking
    assert cap.loc["P", "binding"] == "drag+tracking"
    curve["drag_bps_yr"] = [5.0, 6.0, 7.0]
    curve["tracking_error_bps_yr"] = [10.0, 20.0, 30.0]
    cap = liquidity.capacity_table(curve, drag_limit_bps=25.0, te_limit_bps=50.0)
    assert cap.loc["P", "capacity_aum"] == 10e6 and cap.loc["P", "binding"] == ""


# --------------------------------------------------------------------------------------------------------- #
# The frozen-results check itself (section 20)
# --------------------------------------------------------------------------------------------------------- #
def test_golden_tolerance_is_the_one_recorded_and_documented():
    manifest = json.loads((golden.GOLDEN_DIR / "manifest.json").read_text())
    assert (golden.RTOL, golden.ATOL) == (manifest["rtol"], manifest["atol"]) == (1e-6, 1e-9)
    want = {"t": pd.DataFrame({"x": [1.0, 2.0]}, index=["a", "b"])}
    assert not golden.compare({"t": want["t"] * (1 + 5e-7)}, want)
    assert golden.compare({"t": want["t"] * (1 + 5e-6)}, want)


def test_golden_inputs_check_catches_a_changed_cache_or_config(monkeypatch):
    manifest = {"config_hash": audit.config_hash(SPEC), "data_hash": "frozen"}
    monkeypatch.setattr(audit, "data_hash", lambda *a, **k: "frozen")
    assert golden.inputs_changed(manifest) == []
    monkeypatch.setattr(audit, "data_hash", lambda *a, **k: "refreshed")
    assert any("data_hash" in c for c in golden.inputs_changed(manifest))
    manifest["config_hash"] = "other"
    assert any("config_hash" in c for c in golden.inputs_changed(manifest))


# --------------------------------------------------------------------------------------------------------- #
# Planner residue (section 20, rule 4)
# --------------------------------------------------------------------------------------------------------- #
def test_trades_below_a_hundredth_of_a_cent_are_not_generated():
    prices = pd.Series({"X": 100.0, "Y": 10.0})
    hold = {"X": HoldingView("X", 100.0, [(0, 50.0, 5000.0, pd.Timestamp("2022-01-03"))]),
            "Y": HoldingView("Y", 10.0, [(0, 500.0, 5000.0, pd.Timestamp("2022-01-03"))])}
    targets = pd.Series({"X": 0.5, "Y": 0.5})
    assert plan(pd.Timestamp("2024-06-03"), targets, hold, cash=0.00005, prices=prices, policy=ExecutionPolicy()) == []
    trades = plan(pd.Timestamp("2024-06-03"), targets, hold, cash=10.0, prices=prices, policy=ExecutionPolicy())
    assert sum(t.dollars for t in trades) == pytest.approx(10.0)


# --------------------------------------------------------------------------------------------------------- #
# Simulator definitions (sections 2 and 11)
# --------------------------------------------------------------------------------------------------------- #
class _ZeroCost(CostModel):
    def trade_cost(self, ticker, notional, adv, daily_vol=None):
        return 0.0


def test_turnover_is_one_way_and_cash_earns_the_bill_yield():
    md = make_synthetic(n_days=120, seed=7)
    flat = md.adj_close.copy()
    flat.loc[:, :] = 50.0                                # flat prices: only the trades and the cash yield matter
    md = type(md)(adj_close=flat, close=flat.copy(), volume=md.volume, distributions=md.distributions * 0.0,
                  macro=md.macro, splits=md.splits)
    spec = Spec(cash_yield_mode="fixed", cash_yield_fixed=0.0365)
    d0, d1 = md.index[10], md.index[40]
    switch = pd.DataFrame({"SMH": [1.0, 0.0], "MOAT": [0.0, 1.0]}, index=[d0, d1])
    res = portfolio.simulate(md, switch, spec=spec, cost_model=_ZeroCost(spec, override_file=None), initial=1e6)
    assert res.turnover.loc[d1] == pytest.approx(1.0)   # sell everything and buy everything: 100% one way
    idle = pd.DataFrame({"SMH": [0.0]}, index=[d0])      # all cash from the first day
    res = portfolio.simulate(md, idle, spec=spec, cost_model=_ZeroCost(spec, override_file=None), initial=1e6)
    n = len(res.nav)
    assert res.nav.iloc[-1] == pytest.approx(1e6 * (1 + 0.0365 / 252) ** (n - 1), rel=1e-12)

