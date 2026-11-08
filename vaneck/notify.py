"""Alert delivery: a webhook (Slack-compatible JSON) when configured, always a local alert log."""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path

from .store import STATE_DIR, Store

log = logging.getLogger(__name__)
WEBHOOK_ENV = "VANECK_WEBHOOK_URL"
ALERT_LOG = STATE_DIR / "alerts.log"


def notify(source: str, severity: str, message: str, payload: dict | None = None,
           store: Store | None = None, timeout: int = 10, alert_log: Path = ALERT_LOG) -> bool:
    """Deliver an alert; never raises. Returns True when the webhook accepted it."""
    delivered = False
    text = f"[vaneck] {severity.upper()} {source}: {message}"
    url = os.environ.get(WEBHOOK_ENV)
    if url:
        body = json.dumps({"text": text, "source": source, "severity": severity, "message": message,
                           "payload": payload or {}}, default=str).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - operator-configured URL
                delivered = resp.status < 300
        except Exception as exc:  # noqa: BLE001
            log.warning("webhook delivery failed: %r", exc)
    try:
        alert_log.parent.mkdir(parents=True, exist_ok=True)
        with alert_log.open("a") as fh:
            fh.write(json.dumps({"source": source, "severity": severity, "message": message, "delivered": delivered,
                                 "payload": payload or {}}, default=str) + "\n")
    except OSError as exc:
        log.warning("alert log write failed: %r", exc)
    if store is not None:
        store.record_alert(source, severity, message, delivered)
    log.warning(text + ("" if delivered else " (webhook not configured or failed)"))
    return delivered
