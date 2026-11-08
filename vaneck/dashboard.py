"""Static status page rendered from the operational store and the paper book."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .config import REPORT_DIR
from .store import Store

CSS = """
:root{--bg:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--axis:#c3c2b7;--ok:#0ca30c;--warn:#fab219;--block:#d03b3b;--accent:#2a78d6;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#0d0d0d;--surface:#1a1a19;
--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--accent:#3987e5;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;
--axis:#383835;--accent:#3987e5}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}.sub{color:var(--ink2);margin:0 0 20px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:14px 16px}
.tile .l{color:var(--ink2);font-size:13px}.tile .v{font-size:26px;font-weight:600;margin:4px 0 2px}
.tile .d{color:var(--muted);font-size:13px}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:13px;font-weight:600;color:#fff}
.ok{background:var(--ok)}.warn{background:var(--warn);color:#0b0b0b}
.block,.alert,.blocked{background:var(--block)}.dry_run,.submitted,.info{background:var(--accent)}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--grid);
border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums;
font-size:14px}
th{color:var(--ink2);font-weight:600;background:var(--bg)}tr:last-child td{border-bottom:none}
td.t{white-space:nowrap}
svg{display:block;width:100%;height:120px}.note{color:var(--muted);font-size:13px}
"""


def _pill(status: str | None) -> str:
    s = (status or "n/a").lower()
    return f'<span class="pill {html.escape(s)}">{html.escape(s)}</span>'


def _tile(label: str, value: str, detail: str = "") -> str:
    return (f'<div class="tile"><div class="l">{html.escape(label)}</div><div class="v">{value}</div>'
            f'<div class="d">{html.escape(detail)}</div></div>')


def _sparkline(values: list[float], color: str = "var(--accent)") -> str:
    if len(values) < 2:
        return '<p class="note">no equity history yet</p>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    w, h, pad = 1000, 120, 6
    pts = [(pad + i * (w - 2 * pad) / (len(values) - 1), h - pad - (v - lo) / span * (h - 2 * pad))
           for i, v in enumerate(values)]
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    last = pts[-1]
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="paper equity">'
            f'<polyline fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" points="{d}"/>'
            f'<circle cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="4" fill="{color}" stroke="var(--surface)" stroke-width="2"/>'
            "</svg>")


def _book_summary(book_path: Path | None) -> tuple[list[float], str]:
    if not book_path or not Path(book_path).exists():
        return [], "no paper book"
    book = json.loads(Path(book_path).read_text())
    equity = [float(v) for _, v in sorted(book.get("marks", {}).items())]
    if not equity:
        return [], "paper book has no marks"
    peak = float(book.get("peak_equity", equity[-1]))
    line = (f"{book.get('name', 'paper')} · as of {book.get('as_of')} · equity {equity[-1]:,.0f} · "
            f"peak {peak:,.0f} · fills {len(book.get('fills', []))}")
    return equity, line


def render(store: Store, book_path: Path | None = None, out: Path = REPORT_DIR / "dashboard.html") -> Path:
    health = store.latest("health")
    decision = store.latest("decisions")
    monitor = store.latest("monitor")
    run = store.latest("runs")
    equity, book_line = _book_summary(book_path)
    dpay = json.loads(decision["payload"]) if decision else {}
    mpay = json.loads(monitor["payload"]) if monitor else {}

    health_detail = (f"as of {health['as_of']}, {health['n_block']} block / {health['n_warn']} warn"
                     if health else "no check recorded")
    decision_detail = (f"{decision['strategy']} · signal {decision['signal_date']} · {decision['regime']} · "
                       f"{decision['n_orders']} orders" if decision else "none")
    monitor_detail = (f"{monitor['strategy']} · {monitor['n_alerts']} alerts · as of {monitor['as_of']}"
                      if monitor else "none")
    run_line = f" · last run {html.escape(run['created_utc'])} ({html.escape(run['command'])})" if run else ""

    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>VanEck Operations</title><style>", CSS, "</style></head><body><main>",
        "<h1>VanEck allocation study · operations</h1>",
        f"<p class='sub'>Rendered from the local run history{run_line}</p>",
        "<div class='tiles'>",
        _tile("Data health", _pill(health["status"] if health else None), health_detail),
        _tile("Latest decision", _pill(decision["status"] if decision else None), decision_detail),
        _tile("Monitor", _pill(monitor["status"] if monitor else None), monitor_detail),
        _tile("Alerts (recent)", str(len(store.recent("alerts", 100))), "see timeline"),
        "</div>",
        "<h2>Paper book</h2>", f"<p class='note'>{html.escape(book_line)}</p>", _sparkline(equity),
    ]
    if dpay:
        parts.append("<h2>Latest decision</h2><table><tr><th>field</th><th>value</th></tr>")
        for k in ("run_id", "policy", "exec_date", "equity", "cash"):
            v = dpay.get(k)
            shown = f"{v:,.0f}" if isinstance(v, (int, float)) else str(v if v is not None else "legacy full rebalance")
            parts.append(f"<tr><td>{k}</td><td>{html.escape(shown)}</td></tr>")
        targets = ", ".join(f"{k} {100 * v:.1f}%" for k, v in (dpay.get("targets") or {}).items())
        parts.append(f"<tr><td>targets</td><td>{html.escape(targets)}</td></tr>")
        flagged = [c for c in dpay.get("risk", {}).get("checks", []) if c["status"] != "pass"]
        flags = "; ".join(f"{c['name']}: {c['detail']}" for c in flagged) or "none"
        parts.append(f"<tr><td>risk flags</td><td>{html.escape(flags)}</td></tr></table>")
    if mpay.get("alerts"):
        items = "".join(f"<li>{html.escape(a)}</li>" for a in mpay["alerts"])
        parts.append(f"<h2>Monitor alerts</h2><ul>{items}</ul>")
    parts.append("<h2>Timeline</h2><table><tr><th>time (UTC)</th><th>kind</th><th>summary</th></tr>")
    for e in store.timeline(30):
        parts.append(f"<tr><td class='t'>{html.escape(e['time'])}</td><td>{html.escape(e['kind'])}</td>"
                     f"<td>{html.escape(e['summary'])}</td></tr>")
    parts.append("</table></main></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts))
    return out
