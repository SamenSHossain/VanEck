import pandas as pd
import pytest
from fake_alpaca import FakeAlpaca

from vaneck import live
from vaneck.alpaca import AlpacaBroker, AlpacaClient
from vaneck.broker import PaperBroker
from vaneck.livebroker import LiveBroker
from vaneck.reconcile import reconcile
from vaneck.risk import Order, RiskLimits

LOOSE = RiskLimits(max_participation=10.0, max_turnover_one_way=2.0)


@pytest.fixture
def fake():
    f = FakeAlpaca(cash=1_000_000.0, prices={"SMH": 200.0, "GDX": 40.0, "OUNZ": 30.0}).start()
    yield f
    f.stop()


def _broker(fake, **kw):
    client = AlpacaClient(fake.base_url, "key", "secret")
    return AlpacaBroker(client, poll_interval=0.0, poll_timeout=5.0, sleep=lambda s: None, **kw)


def test_moc_orders_fill_and_states_are_recorded(fake):
    br = _broker(fake)
    chk = br.check()
    assert chk["account_status"] == "ACTIVE" and chk["cash"] == pytest.approx(1_000_000.0)
    orders = [Order("SMH", "BUY", 100, 200.0), Order("GDX", "BUY", 500, 40.0)]
    fills = br.submit(orders, pd.Timestamp("2024-06-03"), run_id="run1")
    assert {f.ticker: f.shares for f in fills} == {"SMH": 100, "GDX": 500}
    assert br.positions() == {"SMH": 100.0, "GDX": 500.0}
    assert br.cash() == pytest.approx(1_000_000.0 - 100 * 200 - 500 * 40)
    rec = br.records[0]
    assert rec.time_in_force == "cls" and rec.client_order_id == "run1-SMH-buy" and rec.status == "filled"
    assert rec.transitions and rec.transitions[-1][1] == "filled"


def test_delayed_and_partial_fills(fake):
    fake.fill_mode = "partial"
    br = _broker(fake)
    fills = br.submit([Order("SMH", "BUY", 100, 200.0)], pd.Timestamp("2024-06-03"), run_id="run2")
    rec = br.records[0]
    assert [s for _, s in rec.transitions] == ["accepted", "partially_filled", "filled"]
    assert fills[0].shares == 100 and rec.filled_avg_price == pytest.approx(200.0)
    fake.fill_mode = "delayed"
    fake.polls_to_fill = 3
    fills = br.submit([Order("GDX", "BUY", 10, 40.0)], pd.Timestamp("2024-06-03"), run_id="run3")
    assert fills[0].shares == 10 and br.records[-1].status == "filled"


def test_duplicate_client_order_id_is_adopted_not_resubmitted(fake):
    br = _broker(fake)
    br.submit([Order("SMH", "BUY", 10, 200.0)], pd.Timestamp("2024-06-03"), run_id="dup")
    br2 = _broker(fake)
    fills = br2.submit([Order("SMH", "BUY", 10, 200.0)], pd.Timestamp("2024-06-03"), run_id="dup")
    assert len(fake.orders) == 1
    assert br2.records[0].error and "duplicate" in br2.records[0].error
    assert fills[0].shares == 10 and fake.positions["SMH"] == 10


def test_rejected_order_is_recorded_without_fill(fake):
    fake.reject_symbols = {"OUNZ"}
    br = _broker(fake)
    fills = br.submit([Order("OUNZ", "BUY", 5, 30.0), Order("SMH", "BUY", 1, 200.0)], pd.Timestamp("2024-06-03"), run_id="rj")
    assert [f.ticker for f in fills] == ["SMH"]
    rec = next(r for r in br.records if r.symbol == "OUNZ")
    assert rec.status == "rejected" and rec.error


def test_reconcile_detects_breaks():
    prices = pd.Series({"SMH": 200.0, "GDX": 40.0, "OUNZ": 30.0})
    rep = reconcile({"SMH": 100, "GDX": 50}, {"SMH": 100, "GDX": 45, "OUNZ": 10}, prices,
                    local_cash=1000.0, broker_cash=1250.0, as_of="2024-06-03", cash_tol=10.0)
    kinds = {b.symbol: b.kind for b in rep.breaks}
    assert kinds == {"GDX": "qty", "OUNZ": "missing_local"} and rep.cash_break and rep.status == "break"
    assert "GDX" in rep.to_markdown()
    clean = reconcile({"SMH": 100}, {"SMH": 100}, pd.Series({"SMH": 200.0}), 10.0, 10.5, "2024-06-03", cash_tol=1.0)
    assert clean.clean


def test_livebroker_rebalance_end_to_end_and_break_blocks(synth, tmp_path):
    md = synth
    sig = md.index[-40]
    nxt = md.index[-39]
    prices = {t: float(md.close.at[nxt, t]) for t in md.close.columns}
    fake = FakeAlpaca(cash=5_000_000.0, prices=prices).start()
    try:
        shadow = PaperBroker(tmp_path / "shadow.json", md)
        shadow.init(5_000_000.0, sig)
        lb = LiveBroker(_broker(fake), shadow, md)
        d = live.rebalance(md, "EqualWeight", lb, tmp_path / "dec", as_of=sig, dry_run=False, limits=LOOSE,
                           require_approval=False)
        assert d["status"] == "submitted" and len(d["fills"]) == 9
        assert d["reconcile"]["status"] == "ok" and len(d["venue_orders"]) == 9
        assert shadow.positions() == pytest.approx(fake.positions)
        assert all(v["status"] == "filled" for v in d["venue_orders"])
        # someone trades in the account outside the pipeline -> next decision is blocked by the break
        fake.positions["SMH"] += 7
        sig2 = md.index[-20]
        d2 = live.rebalance(md, "EqualWeight", lb, tmp_path / "dec", as_of=sig2, dry_run=False, limits=LOOSE,
                            require_approval=False)
        assert d2["status"] == "blocked" and d2["reconcile"]["status"] == "break"
        assert any(b["symbol"] == "SMH" and b["diff_qty"] == 7 for b in d2["reconcile"]["breaks"])
        # adopting the venue state records a transfer and clears the break
        rep = lb.sync(sig2, adopt=True)
        assert rep.clean and shadow.positions()["SMH"] == pytest.approx(fake.positions["SMH"])
        assert shadow.book["cash_flows"][-1]["type"] == "adjustment"
    finally:
        fake.stop()
