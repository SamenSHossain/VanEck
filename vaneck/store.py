"""Operational history in SQLite: runs, health reports, decisions, monitor reports and alerts."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .config import ROOT

STATE_DIR = ROOT / "state"
DB_PATH = STATE_DIR / "vaneck.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, created_utc TEXT, command TEXT, status TEXT,
    config_hash TEXT, data_hash TEXT, git_sha TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS health (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_utc TEXT, as_of TEXT, status TEXT,
    n_block INTEGER, n_warn INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT PRIMARY KEY, created_utc TEXT, strategy TEXT, signal_date TEXT, exec_date TEXT, status TEXT,
    regime TEXT, health_status TEXT, risk_status TEXT, n_orders INTEGER, n_fills INTEGER, equity REAL, payload TEXT);
CREATE TABLE IF NOT EXISTS monitor (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_utc TEXT, strategy TEXT, as_of TEXT, status TEXT,
    n_alerts INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_utc TEXT, source TEXT, severity TEXT, message TEXT, delivered INTEGER);
"""


def _now() -> str:
    from .audit import utc_now
    return utc_now()


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- writers ------------------------------------------------------------- #
    def record_run(self, manifest: dict, status: str, payload: dict | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?)",
            (manifest["run_id"], manifest["created_utc"], manifest["command"], status, manifest.get("config_hash"),
             manifest.get("data_hash"), (manifest.get("git") or {}).get("sha"), json.dumps(payload or {}, default=str)))
        self.conn.commit()

    def record_health(self, report: dict) -> None:
        checks = report.get("checks", [])
        self.conn.execute(
            "INSERT INTO health (created_utc, as_of, status, n_block, n_warn, payload) VALUES (?,?,?,?,?,?)",
            (_now(), report.get("as_of"), report.get("status"), sum(c["status"] == "block" for c in checks),
             sum(c["status"] == "warn" for c in checks), json.dumps(report, default=str)))
        self.conn.commit()

    def record_decision(self, d: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["run_id"], d["created_utc"], d["strategy"], d["signal_date"], d["exec_date"], d["status"], d.get("regime"),
             d["health"]["status"], d["risk"]["status"], len(d["risk"]["orders"]), len(d.get("fills") or []),
             float(d.get("equity") or 0.0), json.dumps(d, default=str)))
        self.conn.commit()

    def record_monitor(self, rep: dict) -> None:
        self.conn.execute(
            "INSERT INTO monitor (created_utc, strategy, as_of, status, n_alerts, payload) VALUES (?,?,?,?,?,?)",
            (_now(), rep.get("strategy"), rep.get("as_of"), rep.get("status"), len(rep.get("alerts") or []),
             json.dumps(rep, default=str)))
        self.conn.commit()

    def record_alert(self, source: str, severity: str, message: str, delivered: bool) -> None:
        self.conn.execute("INSERT INTO alerts (created_utc, source, severity, message, delivered) VALUES (?,?,?,?,?)",
                          (_now(), source, severity, message, int(delivered)))
        self.conn.commit()

    # -- readers ------------------------------------------------------------- #
    def recent(self, table: str, n: int = 20) -> list[dict]:
        order = "created_utc DESC, rowid DESC"
        rows = self.conn.execute(f"SELECT * FROM {table} ORDER BY {order} LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def latest(self, table: str) -> dict | None:
        rows = self.recent(table, 1)
        return rows[0] if rows else None

    def timeline(self, n: int = 30) -> list[dict]:
        events: list[dict] = []
        for r in self.recent("runs", n):
            events.append({"time": r["created_utc"], "kind": "run", "summary": f"{r['command']} {r['status']}"})
        for r in self.recent("health", n):
            events.append({"time": r["created_utc"], "kind": "health",
                           "summary": f"{r['status']} (block {r['n_block']}, warn {r['n_warn']}) as of {r['as_of']}"})
        for r in self.recent("decisions", n):
            summary = f"{r['strategy']} {r['signal_date']} {r['status']} ({r['n_orders']} orders, {r['n_fills']} fills)"
            events.append({"time": r["created_utc"], "kind": "decision", "summary": summary})
        for r in self.recent("monitor", n):
            summary = f"{r['strategy']} {r['status']} ({r['n_alerts']} alerts)"
            events.append({"time": r["created_utc"], "kind": "monitor", "summary": summary})
        for r in self.recent("alerts", n):
            summary = f"{r['severity']} {r['source']}: {r['message']}" + ("" if r["delivered"] else " (not delivered)")
            events.append({"time": r["created_utc"], "kind": "alert", "summary": summary})
        return sorted(events, key=lambda e: e["time"], reverse=True)[:n]
