"""Alpaca paper/live trading adapter implementing the ``Broker`` protocol.

Only REST endpoints are used: /v2/account, /v2/positions, /v2/clock,
/v2/orders. Orders are market-on-close by default (the research convention),
carry a ``client_order_id`` derived from the decision run id so a re-submission
is rejected by the venue instead of duplicating, and are polled to a terminal
state. Every state transition is kept in an ``OrderRecord`` so the decision
record can show exactly what happened.

Credentials come from ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY``; the base
URL from ``APCA_API_BASE_URL`` (default: the paper endpoint). Nothing here is
specific to the sandbox: point it at the fake server in tests or at Alpaca.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field

import pandas as pd
import requests

from .broker import Fill
from .risk import Order

log = logging.getLogger(__name__)
PAPER_URL = "https://paper-api.alpaca.markets"
TERMINAL = {"filled", "canceled", "expired", "rejected", "done_for_day", "stopped", "suspended"}
OPEN = {"new", "accepted", "pending_new", "partially_filled", "accepted_for_bidding", "pending_cancel",
        "pending_replace", "calculated"}


class BrokerError(RuntimeError):
    pass


@dataclass
class OrderRecord:
    client_order_id: str
    symbol: str
    side: str
    qty: float
    order_type: str
    time_in_force: str
    broker_order_id: str | None = None
    status: str = "pending_submit"
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    submitted_at: str | None = None
    updated_at: str | None = None
    transitions: list[tuple[str, str]] = field(default_factory=list)   # (utc time, status)
    error: str | None = None

    def update(self, payload: dict) -> None:
        status = str(payload.get("status", self.status))
        if status != self.status:
            self.transitions.append((pd.Timestamp.now("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"), status))
        self.status = status
        self.broker_order_id = payload.get("id", self.broker_order_id)
        self.filled_qty = float(payload.get("filled_qty") or 0.0)
        fap = payload.get("filled_avg_price")
        self.filled_avg_price = float(fap) if fap not in (None, "") else None
        self.submitted_at = payload.get("submitted_at", self.submitted_at)
        self.updated_at = payload.get("updated_at", self.updated_at)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    def to_dict(self) -> dict:
        return asdict(self)


class AlpacaClient:
    """Thin, explicit HTTP layer so the adapter logic stays testable."""

    def __init__(self, base_url: str | None = None, key_id: str | None = None, secret: str | None = None,
                 session: requests.Session | None = None, timeout: float = 20.0):
        self.base_url = (base_url or os.environ.get("APCA_API_BASE_URL") or PAPER_URL).rstrip("/")
        self.key_id = key_id or os.environ.get("APCA_API_KEY_ID", "")
        self.secret = secret or os.environ.get("APCA_API_SECRET_KEY", "")
        self.session = session or requests.Session()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.secret)

    def _headers(self) -> dict:
        return {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret, "Accept": "application/json"}

    def _request(self, method: str, path: str, **kw) -> dict | list | None:
        url = f"{self.base_url}{path}"
        try:
            r = self.session.request(method, url, headers=self._headers(), timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise BrokerError(f"{method} {path}: {exc!r}") from exc
        if r.status_code == 204:
            return None
        if r.status_code >= 400:
            raise BrokerError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def account(self) -> dict:
        return self._request("GET", "/v2/account")  # type: ignore[return-value]

    def positions(self) -> list[dict]:
        return self._request("GET", "/v2/positions")  # type: ignore[return-value]

    def clock(self) -> dict:
        return self._request("GET", "/v2/clock")  # type: ignore[return-value]

    def submit_order(self, body: dict) -> dict:
        return self._request("POST", "/v2/orders", json=body)  # type: ignore[return-value]

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/v2/orders/{order_id}")  # type: ignore[return-value]

    def get_order_by_client_id(self, client_order_id: str) -> dict | None:
        try:
            return self._request("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id})  # type: ignore[return-value]
        except BrokerError as exc:
            if "404" in str(exc):
                return None
            raise

    def list_orders(self, status: str = "open", limit: int = 500) -> list[dict]:
        return self._request("GET", "/v2/orders", params={"status": status, "limit": limit})  # type: ignore[return-value]

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/v2/orders/{order_id}")


class AlpacaBroker:
    """``Broker`` protocol over Alpaca. Quantities are shares; cash and equity come from the account."""

    def __init__(self, client: AlpacaClient | None = None, time_in_force: str = "cls",
                 poll_interval: float = 2.0, poll_timeout: float = 600.0, sleep=time.sleep):
        self.client = client or AlpacaClient()
        self.time_in_force = time_in_force
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._sleep = sleep
        self.records: list[OrderRecord] = []

    # -- state ------------------------------------------------------------------ #
    def account(self) -> dict:
        return self.client.account()

    def positions(self) -> dict[str, float]:
        return {p["symbol"]: float(p["qty"]) for p in self.client.positions() if float(p["qty"]) != 0.0}

    def position_values(self) -> dict[str, float]:
        return {p["symbol"]: float(p["market_value"]) for p in self.client.positions()}

    def cash(self) -> float:
        return float(self.account()["cash"])

    def equity(self, date=None) -> float:
        return float(self.account()["equity"])

    def market_open(self) -> bool:
        return bool(self.client.clock().get("is_open"))

    def check(self) -> dict:
        acct = self.account()
        clk = self.client.clock()
        return {"base_url": self.client.base_url, "account_status": acct.get("status"), "cash": float(acct["cash"]),
                "equity": float(acct["equity"]), "trading_blocked": bool(acct.get("trading_blocked")),
                "account_blocked": bool(acct.get("account_blocked")), "market_open": bool(clk.get("is_open")),
                "next_open": clk.get("next_open"), "next_close": clk.get("next_close")}

    # -- execution --------------------------------------------------------------- #
    def submit(self, orders: list[Order], date, run_id: str = "manual", wait: bool = True) -> list[Fill]:
        """Submit whole-share orders, poll to a terminal state, return fills (partial fills included)."""
        records: list[OrderRecord] = []
        for o in orders:
            qty = int(o.shares) if float(o.shares).is_integer() else o.shares
            if qty <= 0:
                continue
            cid = f"{run_id}-{o.ticker}-{o.side.lower()}"[:48]
            rec = OrderRecord(client_order_id=cid, symbol=o.ticker, side=o.side.lower(), qty=float(qty),
                              order_type="market", time_in_force=self.time_in_force)
            existing = self.client.get_order_by_client_id(cid)
            if existing is not None:                      # idempotency: the venue already has this order
                rec.update(existing)
                rec.error = "duplicate client_order_id: adopted the existing order"
                log.warning("order %s already exists at the venue (status %s); not resubmitting", cid, rec.status)
            else:
                body = {"symbol": o.ticker, "qty": str(qty), "side": rec.side, "type": "market",
                        "time_in_force": self.time_in_force, "client_order_id": cid}
                try:
                    rec.update(self.client.submit_order(body))
                except BrokerError as exc:
                    rec.status, rec.error = "rejected", str(exc)
                    log.error("order %s rejected: %s", cid, exc)
            records.append(rec)
        self.records.extend(records)
        if wait:
            self.wait_for_fills(records)
        fills = []
        for rec in records:
            if rec.filled_qty > 0 and rec.filled_avg_price:
                notional = rec.filled_qty * rec.filled_avg_price
                fills.append(Fill(date=str(pd.Timestamp(date).date()), ticker=rec.symbol, side=rec.side.upper(),
                                  shares=rec.filled_qty, price=rec.filled_avg_price, notional=notional, cost=0.0))
        return fills

    def wait_for_fills(self, records: list[OrderRecord]) -> None:
        deadline = time.monotonic() + self.poll_timeout
        pending = [r for r in records if r.broker_order_id and not r.terminal]
        while pending and time.monotonic() < deadline:
            self._sleep(self.poll_interval)
            for rec in pending:
                try:
                    rec.update(self.client.get_order(rec.broker_order_id))  # type: ignore[arg-type]
                except BrokerError as exc:
                    rec.error = str(exc)
            pending = [r for r in pending if not r.terminal]
        for rec in pending:
            rec.error = (rec.error or "") + " poll timeout: order still open"
            log.warning("order %s still %s after %.0fs", rec.client_order_id, rec.status, self.poll_timeout)

    def cancel_open(self, run_id: str | None = None) -> int:
        n = 0
        for o in self.client.list_orders("open"):
            if run_id is None or str(o.get("client_order_id", "")).startswith(run_id):
                self.client.cancel_order(o["id"])
                n += 1
        return n
