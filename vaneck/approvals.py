"""Approvals as code: a global halt flag, signed approval of a dry-run decision before it may be
submitted, and a two-person rule for changes to the risk limits.

Signatures are HMAC-SHA256 with the shared secret in ``VANECK_APPROVAL_KEY``.
Without a key, approvals are recorded unsigned and accepted unless
``VANECK_REQUIRE_SIGNED=1``; every record names the operator and the time.
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
from dataclasses import fields
from pathlib import Path

from .audit import utc_now
from .risk import RiskLimits
from .store import STATE_DIR

KEY_ENV = "VANECK_APPROVAL_KEY"
REQUIRE_SIGNED_ENV = "VANECK_REQUIRE_SIGNED"
HALT_FILE = STATE_DIR / "HALT"
HALT_LOG = STATE_DIR / "halt.log"
LIMITS_FILE = STATE_DIR / "limits.json"
LIMITS_PROPOSAL = STATE_DIR / "limits_proposal.json"


def operator() -> str:
    return os.environ.get("VANECK_OPERATOR") or getpass.getuser()


def _key() -> str | None:
    return os.environ.get(KEY_ENV) or None


def _sign(payload: str, key: str | None = None) -> str | None:
    key = key or _key()
    return hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest() if key else None


def _valid(payload: str, signature: str | None, key: str | None = None) -> tuple[bool, str]:
    key = key or _key()
    if key is None:
        if os.environ.get(REQUIRE_SIGNED_ENV) == "1":
            return False, "signed approvals required but no key configured"
        return True, "unsigned (no approval key configured)"
    if not signature:
        return False, "approval is unsigned but a key is configured"
    ok = hmac.compare_digest(signature, _sign(payload, key) or "")
    return ok, "signature valid" if ok else "signature invalid"


def signing_state() -> tuple[str, bool]:
    """How approvals are checked on this machine, and whether that refuses every approval."""
    if _key():
        return "signed approvals enforced", False
    if os.environ.get(REQUIRE_SIGNED_ENV) == "1":
        return "signing required but no key configured", True
    return "no key configured, unsigned approvals accepted", False


# --------------------------------------------------------------------------- #
# Halt flag
# --------------------------------------------------------------------------- #
def is_halted(halt_file: Path = HALT_FILE) -> dict | None:
    return json.loads(halt_file.read_text()) if halt_file.exists() else None


def set_halt(on: bool, reason: str, by: str | None = None, halt_file: Path = HALT_FILE, log_file: Path = HALT_LOG) -> dict:
    rec = {"halted": on, "reason": reason, "by": by or operator(), "at": utc_now()}
    halt_file.parent.mkdir(parents=True, exist_ok=True)
    if on:
        halt_file.write_text(json.dumps(rec, indent=2))
    elif halt_file.exists():
        halt_file.unlink()
    with log_file.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


# --------------------------------------------------------------------------- #
# Decision approvals
# --------------------------------------------------------------------------- #
def decision_hash(decision: dict) -> str:
    """Hash of what the approver saw: strategy, signal date, targets and post-risk orders."""
    core = {
        "strategy": decision["strategy"], "signal_date": decision["signal_date"],
        "targets": {k: round(float(v), 6) for k, v in sorted(decision.get("targets", {}).items())},
        "orders": sorted((o["ticker"], o["side"], round(float(o["shares"]), 6)) for o in decision["risk"]["orders"]),
    }
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:16]


def approval_path(approvals_dir: Path, strategy: str, signal_date: str) -> Path:
    from .live import slug
    return approvals_dir / f"{signal_date}_{slug(strategy)}.json"


def approve(decision: dict, by: str | None, approvals_dir: Path, key: str | None = None) -> dict:
    by = by or operator()
    h = decision_hash(decision)
    at = utc_now()
    payload = f"{decision['run_id']}|{h}|{by}|{at}"
    rec = {"run_id": decision["run_id"], "strategy": decision["strategy"], "signal_date": decision["signal_date"],
           "decision_hash": h, "by": by, "at": at, "signature": _sign(payload, key), "payload": payload}
    approvals_dir.mkdir(parents=True, exist_ok=True)
    approval_path(approvals_dir, decision["strategy"], decision["signal_date"]).write_text(json.dumps(rec, indent=2))
    return rec


def verify(decision: dict, approvals_dir: Path, key: str | None = None) -> tuple[bool, str]:
    p = approval_path(approvals_dir, decision["strategy"], decision["signal_date"])
    if not p.exists():
        return False, f"no approval on file for {decision['strategy']} {decision['signal_date']} (run `approve <dry-run id>`)"
    rec = json.loads(p.read_text())
    if rec["decision_hash"] != decision_hash(decision):
        return False, (f"approved orders differ from the current orders "
                       f"(approved {rec['decision_hash']}, now {decision_hash(decision)})")
    ok, why = _valid(rec["payload"], rec.get("signature"), key)
    return ok, f"approved by {rec['by']} at {rec['at']} ({why})"


# --------------------------------------------------------------------------- #
# Two-person rule for risk limits
# --------------------------------------------------------------------------- #
_LIMIT_FIELDS = {f.name: f.type for f in fields(RiskLimits)}


def propose_limits(changes: dict, by: str | None = None, proposal: Path = LIMITS_PROPOSAL, key: str | None = None) -> dict:
    unknown = set(changes) - set(_LIMIT_FIELDS)
    if unknown:
        raise ValueError(f"unknown limit fields: {sorted(unknown)}")
    by = by or operator()
    at = utc_now()
    payload = json.dumps(changes, sort_keys=True) + f"|{by}|{at}"
    rec = {"changes": changes, "proposed_by": by, "at": at,
           "approvals": [{"by": by, "at": at, "signature": _sign(payload, key), "payload": payload}]}
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(json.dumps(rec, indent=2))
    return rec


def approve_limits(by: str | None = None, proposal: Path = LIMITS_PROPOSAL, active: Path = LIMITS_FILE,
                   key: str | None = None) -> dict:
    if not proposal.exists():
        raise FileNotFoundError("no limits proposal to approve")
    rec = json.loads(proposal.read_text())
    by = by or operator()
    if any(a["by"] == by for a in rec["approvals"]):
        return {**rec, "activated": False, "note": f"{by} already approved; a second, different person is required"}
    at = utc_now()
    payload = json.dumps(rec["changes"], sort_keys=True) + f"|{by}|{at}"
    rec["approvals"].append({"by": by, "at": at, "signature": _sign(payload, key), "payload": payload})
    distinct = {a["by"] for a in rec["approvals"] if _valid(a["payload"], a.get("signature"), key)[0]}
    if len(distinct) >= 2:
        rec["activated_at"] = at
        active.write_text(json.dumps(rec, indent=2))
        proposal.unlink()
        return {**rec, "activated": True}
    proposal.write_text(json.dumps(rec, indent=2))
    return {**rec, "activated": False}


def load_limits(active: Path = LIMITS_FILE, key: str | None = None) -> tuple[RiskLimits, str]:
    """Risk limits in force: defaults, overridden only by a two-person-approved file."""
    if not active.exists():
        return RiskLimits(), "defaults"
    rec = json.loads(active.read_text())
    distinct = {a["by"] for a in rec.get("approvals", []) if _valid(a["payload"], a.get("signature"), key)[0]}
    if len(distinct) < 2:
        return RiskLimits(), "defaults (limits file lacks two valid approvals; ignored)"
    return RiskLimits(**rec["changes"]), f"overrides {rec['changes']} approved by {sorted(distinct)}"
