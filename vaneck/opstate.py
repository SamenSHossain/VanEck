"""Operator state: the switches the decision pipeline reads before it submits, and what they say now.

``python -m vaneck status`` and the dashboard export both read :func:`report`, so they cannot disagree.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import approvals
from .store import STATE_DIR, Store

BLOCKS = "blocks all orders"
SELL_ONLY = "sell-only"
ATTENTION = "needs attention"
NONE = "none"


def halt_history(state_dir: Path = STATE_DIR, n: int = 20) -> list[dict]:
    """Every set and clear of the global halt flag, newest first."""
    log = state_dir / "halt.log"
    if not log.exists():
        return []
    events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    return [{"time": e.get("at"), "action": "set" if e.get("halted") else "cleared", "by": e.get("by"), "reason": e.get("reason")}
            for e in reversed(events)][:n]


def report(store: Store | None = None, state_dir: Path = STATE_DIR) -> dict:
    """What would stop or restrict a submission right now, switch by switch, in the order the pipeline reads them.

    ``submission`` is ``blocked`` (nothing goes out), ``sell-only`` (a retirement flag refuses new risk) or ``open``.
    ``attention`` is what makes ``status`` exit non-zero: a block, a halt, a flag, a monitor alert, or a last decision
    that was refused.
    """
    health = store.latest("health") if store else None
    decision = store.latest("decisions") if store else None
    monitor = store.latest("monitor") if store else None
    halted = approvals.is_halted(state_dir / "HALT")
    flag_files = sorted(state_dir.glob("RETIRED_*.json"))
    flags = [json.loads(p.read_text()) for p in flag_files]
    history = halt_history(state_dir)
    _, limits_note = approvals.load_limits(state_dir / "limits.json")
    signing, signing_blocks = approvals.signing_state()

    gates: list[dict] = []

    def gate(switch: str, state: str, effect: str, detail: str = "", since: str | None = None) -> None:
        gates.append({"switch": switch, "state": state, "effect": effect, "detail": detail, "since": since})

    if health:
        gate("data health gate", health["status"], BLOCKS if health["status"] == "block" else NONE,
             f"{health['n_block']} blocking and {health['n_warn']} warning checks as of {health['as_of']}", health["created_utc"])
    else:
        gate("data health gate", "no record", NONE, "no health check has been recorded")
    if halted:
        gate("halt flag", "set", BLOCKS, f"by {halted['by']}: {halted['reason']}", halted["at"])
    elif history:
        last = history[0]
        gate("halt flag", "clear", NONE, f"last change: {last['action']} by {last['by']} ({last['reason']})", last["time"])
    else:
        gate("halt flag", "clear", NONE, "never set")
    if flags:
        for rec in flags:
            rules = ", ".join(r["rule"] for r in rec.get("rules", [])) or "no rule recorded"
            gate("retirement flag", f"{rec['strategy']}: {rec['status']}", SELL_ONLY,
                 f"new risk refused for {rec['strategy']} ({rules})", rec.get("at"))
    else:
        gate("retirement flags", "none", NONE)
    gate("approval signing", signing, BLOCKS if signing_blocks else NONE)
    pending = "; a proposed change awaits a second approver" if (state_dir / "limits_proposal.json").exists() else ""
    gate("risk limits", "defaults" if limits_note.startswith("defaults") else "overridden", NONE, limits_note + pending)
    if monitor:
        gate("monitor", monitor["status"], ATTENTION if monitor["status"] == "alert" else NONE,
             f"{monitor['strategy']}: {monitor['n_alerts']} alert(s) as of {monitor['as_of']}", monitor["created_utc"])
    if decision:
        gate("last decision", decision["status"], ATTENTION if decision["status"] in ("blocked", "halted") else NONE,
             f"{decision['strategy']} signal {decision['signal_date']}, {decision['n_orders']} order(s)", decision["created_utc"])

    effects = [g["effect"] for g in gates]
    submission = "blocked" if BLOCKS in effects else "sell-only" if SELL_ONLY in effects else "open"
    return {"submission": submission, "attention": any(e != NONE for e in effects),
            "reasons": [f"{g['switch']}: {g['state']}" for g in gates if g["effect"] != NONE], "gates": gates,
            "health": health, "decision": decision, "monitor": monitor, "halted": halted,
            "retired_flags": [p.name for p in flag_files]}
