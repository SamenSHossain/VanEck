import json

import pytest

from vaneck import approvals
from vaneck.risk import RiskLimits

DECISION = {"run_id": "r1", "strategy": "EqualWeight", "signal_date": "2024-05-31",
            "targets": {"SMH": 0.111, "GDX": 0.111},
            "risk": {"orders": [{"ticker": "SMH", "side": "BUY", "shares": 100}, {"ticker": "GDX", "side": "BUY", "shares": 50}]}}


def test_halt_flag_roundtrip(tmp_path):
    hf, lg = tmp_path / "HALT", tmp_path / "halt.log"
    assert approvals.is_halted(hf) is None
    approvals.set_halt(True, "market structure event", by="alice", halt_file=hf, log_file=lg)
    assert approvals.is_halted(hf)["by"] == "alice"
    approvals.set_halt(False, "cleared", by="bob", halt_file=hf, log_file=lg)
    assert approvals.is_halted(hf) is None and len(lg.read_text().splitlines()) == 2


def test_approve_verify_and_hash_binding(tmp_path, monkeypatch):
    monkeypatch.delenv(approvals.KEY_ENV, raising=False)
    monkeypatch.delenv(approvals.REQUIRE_SIGNED_ENV, raising=False)
    ok, why = approvals.verify(DECISION, tmp_path)
    assert not ok and "no approval" in why
    approvals.approve(DECISION, "alice", tmp_path)
    ok, why = approvals.verify(DECISION, tmp_path)
    assert ok and "alice" in why and "unsigned" in why
    changed = json.loads(json.dumps(DECISION))
    changed["risk"]["orders"][0]["shares"] = 101
    ok, why = approvals.verify(changed, tmp_path)
    assert not ok and "differ" in why
    monkeypatch.setenv(approvals.REQUIRE_SIGNED_ENV, "1")
    ok, why = approvals.verify(DECISION, tmp_path)
    assert not ok and "required" in why


def test_signed_approvals_detect_tampering(tmp_path, monkeypatch):
    monkeypatch.setenv(approvals.KEY_ENV, "s3cret")
    rec = approvals.approve(DECISION, "alice", tmp_path)
    assert rec["signature"]
    ok, why = approvals.verify(DECISION, tmp_path)
    assert ok and "valid" in why
    p = approvals.approval_path(tmp_path, "EqualWeight", "2024-05-31")
    tampered = json.loads(p.read_text())
    tampered["by"] = "mallory"
    tampered["payload"] = tampered["payload"].replace("alice", "mallory")
    p.write_text(json.dumps(tampered))
    ok, why = approvals.verify(DECISION, tmp_path)
    assert not ok and "invalid" in why


def test_two_person_limits(tmp_path, monkeypatch):
    monkeypatch.setenv(approvals.KEY_ENV, "s3cret")
    prop, act = tmp_path / "proposal.json", tmp_path / "limits.json"
    approvals.propose_limits({"max_participation": 0.15}, by="alice", proposal=prop)
    lim, why = approvals.load_limits(act)
    assert lim == RiskLimits() and why == "defaults"
    r = approvals.approve_limits(by="alice", proposal=prop, active=act)
    assert not r["activated"] and "different person" in r["note"]
    r = approvals.approve_limits(by="bob", proposal=prop, active=act)
    assert r["activated"] and act.exists() and not prop.exists()
    lim, why = approvals.load_limits(act)
    assert lim.max_participation == 0.15 and "alice" in why and "bob" in why
    with pytest.raises(ValueError):
        approvals.propose_limits({"nope": 1}, by="alice", proposal=prop)
