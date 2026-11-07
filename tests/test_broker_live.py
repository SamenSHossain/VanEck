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
    def trade_cost(self, ticker, notional, adv):
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


def test_rebalance_dry_run_then_submit_then_duplicate(synth, tmp_path):
    md = synth
    sig = md.index[-30]
    br = PaperBroker(tmp_path / "book.json", md)
    br.init(5_000_000.0, sig)
    dec_dir = tmp_path / "decisions"
    d = live.rebalance(md, "EqualWeight", br, dec_dir, as_of=sig, dry_run=True, limits=LOOSE)
    assert d["status"] == "dry_run" and not d["fills"] and d["executed_weights"] is None
    assert (dec_dir / f"{sig.date()}_EqualWeight_dry_run.json").exists()
    d2 = live.rebalance(md, "EqualWeight", br, dec_dir, as_of=sig, dry_run=False, limits=LOOSE)
    assert d2["status"] == "submitted" and len(d2["fills"]) == 9
    assert abs(sum(d2["executed_weights"].values()) - 1.0) < 0.02
    d3 = live.rebalance(md, "EqualWeight", br, dec_dir, as_of=sig, dry_run=False, limits=LOOSE)
    assert d3["status"] == "duplicate" and not d3["fills"]
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
