"""Implementation shortfall of submitted decisions: what was intended, what the controls let through, what it cost.

Everything here is read from the decision records, so it describes what the pipeline actually did:

* distance to target before and after trading (one-way, cash as the remainder),
* the orders the participation cap scaled and how much of the wanted notional was ordered,
* explicit cost (the cost model's charge on each fill) and delay cost (the move from the decision price, the
  signal-date close, to the fill price, the execution-date close).
"""
from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

_SCALED = re.compile(r"scaled from (\d+) to (\d+) shares")


def distance(a: dict, b: dict) -> float:
    """One-way distance between two weight sets: half the sum of absolute differences, cash as each set's remainder."""
    def with_cash(w: dict) -> dict:
        funds = {k: float(v) for k, v in w.items() if k != "CASH"}
        funds["CASH"] = 1.0 - sum(funds.values())
        return funds

    a, b = with_cash(a), with_cash(b)
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))


def wanted_notional(order: dict) -> float:
    """Order notional before the participation cap, from the planner's own note; the ordered notional if uncapped."""
    m = _SCALED.search(str(order.get("note") or ""))
    return float(m.group(1)) * float(order["price"]) if m else float(order["notional"])


def pre_trade_weights(d: dict, fills: list[dict], close: pd.DataFrame) -> dict | None:
    """Fund weights the decision started from: positions built by earlier fills, at the signal-date close, over the
    record's equity.

    Rebuilt from the fill history rather than read from the record: records written before the pipeline captured
    ``current_weights`` ahead of the fills hold post-trade weights in that field.
    """
    signal, executed_on = pd.Timestamp(d["signal_date"]), pd.Timestamp(d["exec_date"])
    equity = float(d.get("equity") or 0.0)
    if signal not in close.index or equity <= 0:
        return None
    shares: dict[str, float] = defaultdict(float)
    for f in fills:
        if pd.Timestamp(f["date"]) < executed_on:
            shares[f["ticker"]] += (1.0 if f["side"] == "BUY" else -1.0) * float(f["shares"])
    prices = close.loc[signal]
    weights: dict[str, float] = {}
    for ticker, n in shares.items():
        if abs(n) <= 1e-9:
            continue
        price = prices.get(ticker)
        if price is None or pd.isna(price):
            return None                      # a holding without a price that day: no honest weight to report
        weights[ticker] = n * float(price) / equity
    return weights


def decision_row(d: dict, before: dict | None = None) -> dict:
    """Shortfall figures of one decision record; ``before`` is the fund weights it started from."""
    orders, fills = d["risk"]["orders"], d.get("fills") or []
    equity = float(d["equity"])
    decided = {(o["ticker"], o["side"]): float(o["price"]) for o in orders}
    scaled = [o["ticker"] for o in orders if _SCALED.search(str(o.get("note") or ""))]
    wanted = sum(wanted_notional(o) for o in orders)
    ordered = sum(float(o["notional"]) for o in orders)
    explicit = sum(float(f["cost"]) for f in fills)
    # a buy filled above its decision price, or a sell filled below it, is a loss
    delay = 0.0
    for f in fills:
        key = (f["ticker"], f["side"])
        if key in decided:
            delay += (1.0 if f["side"] == "BUY" else -1.0) * float(f["shares"]) * (float(f["price"]) - decided[key])
    executed = d.get("executed_weights")
    return {
        "signal_date": d["signal_date"], "exec_date": d["exec_date"], "status": d["status"], "equity": equity,
        "gap_before": distance(before, d["targets"]) if before is not None else None,
        "gap_after": distance(executed, d["targets"]) if executed else None,
        "orders": len(orders), "scaled": len(scaled), "dropped": len(d["risk"].get("dropped") or []),
        "scaled_tickers": ", ".join(scaled) or "none",
        "wanted_notional": wanted, "ordered_notional": ordered, "filled_notional": sum(float(f["notional"]) for f in fills),
        "ordered_share": ordered / wanted if wanted > 0 else None,
        "explicit_cost": explicit, "delay_cost": delay,
        "explicit_bps": 1e4 * explicit / equity if equity > 0 else None,
        "delay_bps": 1e4 * delay / equity if equity > 0 else None,
        "shortfall_bps": 1e4 * (explicit + delay) / equity if equity > 0 else None,
    }


def table(decisions: list[dict], fills: list[dict] | None = None, close: pd.DataFrame | None = None) -> list[dict]:
    """One row per submitted decision, oldest first. With the book's fills and closing prices, each row also carries
    the distance to target before trading."""
    rows = []
    for d in decisions:
        if d.get("status") != "submitted":
            continue
        before = pre_trade_weights(d, fills, close) if fills is not None and close is not None else None
        rows.append(decision_row(d, before))
    return sorted(rows, key=lambda r: r["signal_date"])


def risk_checks(decisions: list[dict]) -> list[dict]:
    """Each pre-trade check across the decisions: how often it passed, warned or blocked, and its latest reading."""
    out: dict[str, dict] = {}
    for d in sorted(decisions, key=lambda x: x["signal_date"]):
        for c in d["risk"]["checks"]:
            row = out.setdefault(c["name"], {"check": c["name"], "decisions": 0, "passed": 0, "warned": 0, "blocked": 0})
            row["decisions"] += 1
            row[{"pass": "passed", "warn": "warned", "block": "blocked"}.get(c["status"], "warned")] += 1
            row.update(last_value=c.get("value"), limit=c.get("limit"), last_detail=c.get("detail") or "")
    return list(out.values())
