import json

import pandas as pd
import pytest

from vaneck import live, monitor
from vaneck.broker import PaperBroker
from vaneck.config import Spec
from vaneck.costs import CostModel
from vaneck.risk import Order, RiskLimits

LOOSE = RiskLimits(max_participation=10.0, max_turnover_one_way=2.0)


class ZeroCost(CostModel):
    def trade_cost(self, ticker, notional, adv, daily_vol=None):
        return 0.0


def test_paper_broker_fills_dividends_and_splits(synth, tmp_path):
    spec = Spec(cash_yield_mode="fixed", cash_yield_fixed=0.0)
    br = PaperBroker(tmp_path / "book.json", synth, ZeroCost(spec, override_file=None), spec)
    d0 = synth.index[100]
    br.init(1_000_000.0, d0, data_vintage=str(d0.date()))
    fills = br.submit([Order("SMH", "BUY", 1000, float(synth.close.at[d0, "SMH"]))], d0)
    assert len(fills) == 1 and br.positions()["SMH"] == 1000
    assert br.cash() == pytest.approx(1_000_000.0 - 1000 * float(synth.close.at[d0, "SMH"]))
    # a quarter later: dividends have been credited as cash
    d1 = synth.index[170]
    div = float(synth.distributions.loc[d0 + pd.Timedelta(days=1):d1, "SMH"].sum())
    assert div > 0
    br.advance_to(d1)
    assert br.cash() == pytest.approx(1_000_000.0 - 1000 * float(synth.close.at[d0, "SMH"]) + 1000 * div)
    # a split after the data vintage doubles the share count
    d2 = synth.index[180]
    synth.splits.at[d2, "SMH"] = 2.0
    br.advance_to(synth.index[185])
    assert br.positions()["SMH"] == 2000
    synth.splits.at[d2, "SMH"] = 0.0
    curve = br.equity_curve()
    assert curve.index.is_monotonic_increasing and len(curve) > 80


def test_orders_keep_the_no_rebuy_window_for_a_fund_that_was_sold_out(synth, tmp_path):
    """Whole-share books never leave dust: a fund sold out at a loss has no holding view at all."""
    from vaneck.execution import ExecutionPolicy
    spec = Spec(cash_yield_mode="fixed", cash_yield_fixed=0.0)
    br = PaperBroker(tmp_path / "book.json", synth, ZeroCost(spec, override_file=None), spec)
    close = synth.close["SMH"]
    i0 = 100 + int(close.iloc[100:400].to_numpy().argmax())       # buy at a local high, sell five days later
    d0, d1 = synth.index[i0], synth.index[i0 + 5]
    assert close.at[d1] < close.at[d0]
    br.init(1_000_000.0, d0, data_vintage=str(d0.date()))
    br.submit([Order("SMH", "BUY", 1000, float(close.at[d0]))], d0)
    br.submit([Order("SMH", "SELL", 1000, float(close.at[d1]))], d1)
    assert "SMH" not in br.positions() and br.ledger.last_loss_sale["SMH"] == d1
    d2 = synth.index[synth.index.get_loc(d1) + 5]
    assert (d2 - d1).days < 31
    pol = ExecutionPolicy(harvest=True, substitutes=(("SMH", "MOAT"),))
    prices, adv = synth.close.loc[d2], br.cost_model.adv_table(synth).loc[d2]
    orders = live.orders_from_plan(d2, pd.Series({"SMH": 0.5}), br, prices, adv, pol, LOOSE, spec)
    assert [(o.ticker, o.side) for o in orders] == [("MOAT", "BUY")]
    # reloading the book from disk keeps the memory
    br2 = PaperBroker(tmp_path / "book.json", synth, ZeroCost(spec, override_file=None), spec)
    orders = live.orders_from_plan(d2, pd.Series({"SMH": 0.5}), br2, prices, adv, pol, LOOSE, spec)
    assert [(o.ticker, o.side) for o in orders] == [("MOAT", "BUY")]
    # once the window has passed the fund itself is bought again
    d3 = synth.index[synth.index.get_loc(d1) + 30]
    assert (d3 - d1).days >= 31
    br.advance_to(d3)
    orders = live.orders_from_plan(d3, pd.Series({"SMH": 0.5}), br, synth.close.loc[d3],
                                   br.cost_model.adv_table(synth).loc[d3], pol, LOOSE, spec)
    assert [(o.ticker, o.side) for o in orders] == [("SMH", "BUY")]


def test_rebalance_dry_run_then_submit_then_duplicate(synth, tmp_path):
    md = synth
    sig = md.index[-30]
    br = PaperBroker(tmp_path / "book.json", md)
    br.init(5_000_000.0, sig)
    dec_dir = tmp_path / "decisions"
    d = live.rebalance(md, "EqualWeight", br, dec_dir, as_of=sig, dry_run=True, limits=LOOSE)
    assert d["status"] == "dry_run" and not d["fills"] and d["executed_weights"] is None
    assert (dec_dir / f"{sig.date()}_EqualWeight_dry_run.json").exists()
    # submission is gated on an approval of the dry run (hash of targets and orders)
    from vaneck import approvals
    d_un = live.rebalance(md, "EqualWeight", br, dec_dir, as_of=sig, dry_run=False, limits=LOOSE,
                          approvals_dir=tmp_path / "appr", state_dir=tmp_path / "state")
    assert d_un["status"] == "unapproved" and not d_un["fills"]
    approvals.approve(d, "alice", tmp_path / "appr")
    d2 = live.rebalance(md, "EqualWeight", br, dec_dir, as_of=sig, dry_run=False, limits=LOOSE,
                        approvals_dir=tmp_path / "appr", state_dir=tmp_path / "state")
    assert d2["status"] == "submitted" and len(d2["fills"]) == 9 and "alice" in d2["approval"]
    assert abs(sum(d2["executed_weights"].values()) - 1.0) < 0.02
    d3 = live.rebalance(md, "EqualWeight", br, dec_dir, as_of=sig, dry_run=False, limits=LOOSE,
                        approvals_dir=tmp_path / "appr", state_dir=tmp_path / "state")
    assert d3["status"] == "duplicate" and not d3["fills"]
    # the halt flag refuses submissions
    approvals.set_halt(True, "test", "bob", tmp_path / "state" / "HALT", tmp_path / "state" / "halt.log")
    sig_b = md.index[-10]
    d4 = live.rebalance(md, "EqualWeight", br, dec_dir, as_of=sig_b, dry_run=False, limits=LOOSE,
                        approvals_dir=tmp_path / "appr", state_dir=tmp_path / "state")
    assert d4["status"] == "halted"
    rec = json.loads((dec_dir / f"{sig.date()}_EqualWeight_submitted.json").read_text())
    assert rec["manifest"]["config_hash"] and rec["health"]["status"] in ("ok", "warn")


def test_blocked_when_health_fails(synth, tmp_path):
    md = synth
    sig = md.index[-30]
    saved = md.adj_close.at[sig, "GDX"]
    md.adj_close.at[sig, "GDX"] = float("nan")
    br = PaperBroker(tmp_path / "book.json", md)
    br.init(1_000_000.0, sig)
    try:
        d = live.rebalance(md, "EqualWeight", br, tmp_path / "d", as_of=sig, dry_run=False, limits=LOOSE)
    finally:
        md.adj_close.at[sig, "GDX"] = saved
    assert d["status"] == "blocked" and not d["fills"]


def test_replay_reconciles_with_research_engine(synth, tmp_path):
    md = synth
    start, end = md.index[-300], md.index[-5]
    decs = live.replay(md, "EqualWeight", tmp_path / "book.json", tmp_path / "dec", start, end, cash=10e6, limits=LOOSE)
    assert all(d["status"] == "submitted" for d in decs) and len(decs) >= 12
    br = PaperBroker(tmp_path / "book.json", md)
    rep = monitor.monitor(md, br, tmp_path / "dec", "EqualWeight", limits=LOOSE, out_dir=tmp_path)
    rec = rep["reconciliation"]
    # whole-share rounding and dividends held as cash until the next rebalance are the only differences
    assert abs(rec["cumulative_difference_vs_executed"]) < 0.005
    assert abs(rec["cumulative_difference_vs_intended"]) < 0.01
    assert not [a for a in rep["alerts"] if "engine disagreement" in a]
    assert (tmp_path / "monitor_EqualWeight.md").exists()
