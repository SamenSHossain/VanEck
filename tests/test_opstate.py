"""Operator state: one report for the status command and the dashboard export."""
from vaneck import approvals, audit, opstate, retire


class FakeStore:
    def __init__(self, **latest):
        self._latest = latest

    def latest(self, table):
        return self._latest.get(table)


AT = "2025-01-01T00:00:00Z"
HEALTH_OK = {"status": "ok", "n_block": 0, "n_warn": 0, "as_of": "2024-12-31", "created_utc": AT}
DECISION = {"status": "submitted", "strategy": "EqualWeight", "signal_date": "2024-11-29", "n_orders": 9, "created_utc": AT}
MONITOR = {"status": "ok", "strategy": "EqualWeight", "n_alerts": 0, "as_of": "2024-12-31", "created_utc": AT}


def gate(rep, switch):
    return next(g for g in rep["gates"] if g["switch"].startswith(switch))


def test_clean_state_is_open_and_quiet(tmp_path, monkeypatch):
    monkeypatch.delenv(approvals.KEY_ENV, raising=False)
    monkeypatch.delenv(approvals.REQUIRE_SIGNED_ENV, raising=False)
    rep = opstate.report(FakeStore(health=HEALTH_OK, decisions=DECISION, monitor=MONITOR), tmp_path)
    assert rep["submission"] == "open" and rep["attention"] is False and rep["reasons"] == []
    assert gate(rep, "halt flag")["state"] == "clear" and gate(rep, "halt flag")["detail"] == "never set"
    assert gate(rep, "risk limits")["state"] == "defaults"
    assert opstate.report(None, tmp_path)["submission"] == "open"       # no store at all: nothing recorded, nothing blocks


def test_halt_flag_blocks_and_its_history_is_newest_first(tmp_path):
    approvals.set_halt(True, "drill", by="ann", halt_file=tmp_path / "HALT", log_file=tmp_path / "halt.log")
    rep = opstate.report(FakeStore(health=HEALTH_OK), tmp_path)
    assert rep["submission"] == "blocked" and rep["attention"] and gate(rep, "halt flag")["effect"] == opstate.BLOCKS
    assert "ann" in gate(rep, "halt flag")["detail"]
    approvals.set_halt(False, "drill over", by="ann", halt_file=tmp_path / "HALT", log_file=tmp_path / "halt.log")
    rep = opstate.report(FakeStore(health=HEALTH_OK), tmp_path)
    assert rep["submission"] == "open" and gate(rep, "halt flag")["detail"].startswith("last change: cleared by ann")
    assert [e["action"] for e in opstate.halt_history(tmp_path)] == ["cleared", "set"]


def test_retirement_flag_means_sell_only(tmp_path):
    retire.set_flag("EqualWeight", "retire", [retire.RuleResult("R2_drawdown", "retire", -0.4, -0.356)], state_dir=tmp_path)
    rep = opstate.report(FakeStore(health=HEALTH_OK), tmp_path)
    assert rep["submission"] == "sell-only" and rep["attention"] and "R2_drawdown" in gate(rep, "retirement flag")["detail"]
    assert len(rep["retired_flags"]) == 1 and rep["retired_flags"][0].startswith("RETIRED_")


def test_blocking_health_check_blocks_and_alerts_only_ask_for_attention(tmp_path, monkeypatch):
    monkeypatch.delenv(approvals.KEY_ENV, raising=False)
    monkeypatch.delenv(approvals.REQUIRE_SIGNED_ENV, raising=False)
    blocked = opstate.report(FakeStore(health={**HEALTH_OK, "status": "block", "n_block": 1}), tmp_path)
    assert blocked["submission"] == "blocked" and blocked["reasons"] == ["data health gate: block"]
    noisy = opstate.report(FakeStore(health=HEALTH_OK, monitor={**MONITOR, "status": "alert", "n_alerts": 1},
                                     decisions={**DECISION, "status": "halted"}), tmp_path)
    assert noisy["submission"] == "open" and noisy["attention"]
    assert noisy["reasons"] == ["monitor: alert", "last decision: halted"]


def test_required_signing_without_a_key_refuses_everything(tmp_path, monkeypatch):
    monkeypatch.delenv(approvals.KEY_ENV, raising=False)
    monkeypatch.setenv(approvals.REQUIRE_SIGNED_ENV, "1")
    rep = opstate.report(FakeStore(health=HEALTH_OK), tmp_path)
    assert rep["submission"] == "blocked" and gate(rep, "approval signing")["effect"] == opstate.BLOCKS
    monkeypatch.setenv(approvals.KEY_ENV, "k")
    assert opstate.report(FakeStore(health=HEALTH_OK), tmp_path)["submission"] == "open"


def test_code_dirty_ignores_generated_outputs():
    generated = " M reports/dashboard/tests.json\n?? reports/dashboard/weights.json\n M paper/book.json\n M data/raw/SMH.csv"
    assert audit.code_dirty("") is False and audit.code_dirty(generated) is False
    assert audit.code_dirty(" M reports/REPORT.md\n M vaneck/webdash.py") is True
    assert audit.code_dirty("?? tests/test_new.py") is True
    assert audit.code_dirty("R  reports/a.md -> reports/b.md") is False
    assert audit.code_dirty("R  vaneck/old.py -> reports/moved.py") is True       # either side of a rename counts
    assert audit.code_dirty("R  reports/a.md -> docs/a.md") is True


def test_data_files_hashes_each_csv(tmp_path):
    (tmp_path / "A.csv").write_text("x\n1\n")
    (tmp_path / "B.csv").write_text("x\n2\n")
    (tmp_path / "manifest.json").write_text("{}")
    files = audit.data_files(tmp_path)
    assert sorted(files) == ["A.csv", "B.csv"] and files["A.csv"]["bytes"] == 4
    assert len(files["A.csv"]["sha256"]) == 12 and files["A.csv"]["sha256"] != files["B.csv"]["sha256"]
