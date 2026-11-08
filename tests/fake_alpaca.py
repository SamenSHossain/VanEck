"""An in-process fake of the Alpaca REST endpoints the adapter uses (for tests only)."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class FakeAlpaca:
    """``fill_mode``: immediate | delayed (fills after ``polls_to_fill`` polls) | partial (half, then full)."""

    def __init__(self, cash: float = 1_000_000.0, positions: dict[str, float] | None = None,
                 prices: dict[str, float] | None = None, fill_mode: str = "immediate", polls_to_fill: int = 2,
                 reject_symbols: tuple[str, ...] = ()):
        self.cash = float(cash)
        self.positions = dict(positions or {})
        self.prices = dict(prices or {})
        self.fill_mode = fill_mode
        self.polls_to_fill = polls_to_fill
        self.reject_symbols = set(reject_symbols)
        self.orders: dict[str, dict] = {}
        self.by_client: dict[str, str] = {}
        self.polls: dict[str, int] = {}
        self.lock = threading.Lock()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    # -- lifecycle ------------------------------------------------------------- #
    def start(self) -> "FakeAlpaca":
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _send(self, code: int, payload=None):
                body = b"" if payload is None else json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _auth(self) -> bool:
                return bool(self.headers.get("APCA-API-KEY-ID")) and bool(self.headers.get("APCA-API-SECRET-KEY"))

            def do_GET(self):
                if not self._auth():
                    return self._send(401, {"message": "unauthorized"})
                u = urlparse(self.path)
                q = parse_qs(u.query)
                with fake.lock:
                    if u.path == "/v2/account":
                        return self._send(200, fake.account())
                    if u.path == "/v2/positions":
                        return self._send(200, fake.position_list())
                    if u.path == "/v2/clock":
                        return self._send(200, {"timestamp": _now(), "is_open": True, "next_open": _now(), "next_close": _now()})
                    if u.path == "/v2/orders:by_client_order_id":
                        oid = fake.by_client.get(q.get("client_order_id", [""])[0])
                        if oid is None:
                            return self._send(404, {"message": "order not found"})
                        return self._send(200, fake.orders[oid])
                    if u.path == "/v2/orders":
                        status = q.get("status", ["open"])[0]
                        closed = ("filled", "canceled", "rejected")
                        rows = [o for o in fake.orders.values()
                                if status == "all" or (status == "open") == (o["status"] not in closed)]
                        return self._send(200, rows)
                    if u.path.startswith("/v2/orders/"):
                        oid = u.path.rsplit("/", 1)[1]
                        if oid not in fake.orders:
                            return self._send(404, {"message": "order not found"})
                        fake.advance(oid)
                        return self._send(200, fake.orders[oid])
                return self._send(404, {"message": "no route"})

            def do_POST(self):
                if not self._auth():
                    return self._send(401, {"message": "unauthorized"})
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n) or b"{}")
                with fake.lock:
                    if urlparse(self.path).path != "/v2/orders":
                        return self._send(404, {"message": "no route"})
                    cid = body.get("client_order_id") or str(uuid.uuid4())
                    if cid in fake.by_client:
                        return self._send(422, {"code": 40010001, "message": "client_order_id must be unique"})
                    sym = body["symbol"]
                    if sym in fake.reject_symbols or sym not in fake.prices:
                        return self._send(422, {"code": 40310000, "message": f"{sym} is not tradable"})
                    oid = str(uuid.uuid4())
                    order = {"id": oid, "client_order_id": cid, "symbol": sym, "qty": str(body["qty"]), "side": body["side"],
                             "type": body.get("type", "market"), "time_in_force": body.get("time_in_force", "day"),
                             "status": "accepted", "filled_qty": "0", "filled_avg_price": None,
                             "submitted_at": _now(), "updated_at": _now(), "filled_at": None}
                    fake.orders[oid] = order
                    fake.by_client[cid] = oid
                    fake.polls[oid] = 0
                    if fake.fill_mode == "immediate":
                        fake.fill(oid, float(body["qty"]))
                    return self._send(200, order)

            def do_DELETE(self):
                if not self._auth():
                    return self._send(401, {"message": "unauthorized"})
                oid = urlparse(self.path).path.rsplit("/", 1)[1]
                with fake.lock:
                    if oid in fake.orders and fake.orders[oid]["status"] not in ("filled",):
                        fake.orders[oid]["status"] = "canceled"
                        fake.orders[oid]["updated_at"] = _now()
                        return self._send(204)
                return self._send(404, {"message": "order not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    @property
    def base_url(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    # -- state ------------------------------------------------------------------- #
    def equity(self) -> float:
        return self.cash + sum(q * self.prices.get(s, 0.0) for s, q in self.positions.items())

    def account(self) -> dict:
        return {"id": "fake", "status": "ACTIVE", "currency": "USD", "cash": f"{self.cash:.2f}", "equity": f"{self.equity():.2f}",
                "buying_power": f"{self.cash:.2f}", "portfolio_value": f"{self.equity():.2f}", "trading_blocked": False,
                "account_blocked": False, "pattern_day_trader": False}

    def position_list(self) -> list[dict]:
        return [{"symbol": s, "qty": f"{q:g}", "side": "long", "market_value": f"{q * self.prices.get(s, 0.0):.2f}",
                 "current_price": f"{self.prices.get(s, 0.0):.2f}", "avg_entry_price": f"{self.prices.get(s, 0.0):.2f}"}
                for s, q in self.positions.items() if q != 0]

    def fill(self, oid: str, qty: float) -> None:
        o = self.orders[oid]
        total = float(o["qty"])
        already = float(o["filled_qty"])
        qty = min(qty, total - already)
        if qty <= 0:
            return
        px = self.prices[o["symbol"]]
        sign = 1.0 if o["side"] == "buy" else -1.0
        self.positions[o["symbol"]] = self.positions.get(o["symbol"], 0.0) + sign * qty
        self.cash -= sign * qty * px
        if abs(self.positions[o["symbol"]]) < 1e-9:
            del self.positions[o["symbol"]]
        filled = already + qty
        o["filled_qty"] = f"{filled:g}"
        o["filled_avg_price"] = f"{px:.4f}"
        o["status"] = "filled" if abs(filled - total) < 1e-9 else "partially_filled"
        o["updated_at"] = _now()
        if o["status"] == "filled":
            o["filled_at"] = _now()

    def advance(self, oid: str) -> None:
        """Called on each poll of an open order."""
        o = self.orders[oid]
        if o["status"] in ("filled", "canceled", "rejected"):
            return
        self.polls[oid] += 1
        total = float(o["qty"])
        if self.fill_mode == "partial":
            if self.polls[oid] == 1:
                self.fill(oid, total / 2.0)
            elif self.polls[oid] >= 2:
                self.fill(oid, total)
        elif self.fill_mode == "delayed" and self.polls[oid] >= self.polls_to_fill:
            self.fill(oid, total)
