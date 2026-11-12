"""Implementation shortfall from decision records."""
import pandas as pd

from vaneck import shortfall

NOTE = "scaled from 5000 to 1000 shares (participation 50.0% > 10%)"


def order(ticker, side, shares, price, note=""):
    return {"ticker": ticker, "side": side, "shares": shares, "price": price, "notional": shares * price, "note": note}


def fill(ticker, side, shares, price, cost, date="2024-02-01"):
    return {"date": date, "ticker": ticker, "side": side, "shares": shares, "price": price, "notional": shares * price,
            "cost": cost}


def check(name, status, value=0, limit=0, detail=""):
    return {"name": name, "status": status, "value": value, "limit": limit, "detail": detail}


def decision(**over):
    d = {"signal_date": "2024-01-31", "exec_date": "2024-02-01", "status": "submitted", "equity": 1_000_000.0, "cash": 500_000.0,
         "targets": {"A": 0.5, "B": 0.5}, "executed_weights": {"A": 0.5, "B": 0.3},
         "risk": {"orders": [order("A", "SELL", 100, 50.0), order("B", "BUY", 1000, 20.0, NOTE)], "dropped": [],
                  "checks": [check("participation", "warn", 1, 0.1, "scaled: B")]},
         "fills": [fill("A", "SELL", 100, 49.0, 3.0), fill("B", "BUY", 1000, 20.5, 7.0)]}
    d.update(over)
    return d


def test_distance_treats_cash_as_the_remainder():
    assert shortfall.distance({}, {"A": 0.5, "B": 0.5}) == 1.0                 # all cash against a full target
    assert abs(shortfall.distance({"A": 0.5, "B": 0.3, "CASH": 0.2}, {"A": 0.5, "B": 0.5}) - 0.2) < 1e-12
    assert abs(shortfall.distance({"A": 0.5, "B": 0.3}, {"A": 0.5, "B": 0.5}) - 0.2) < 1e-12   # same without a CASH entry
    assert shortfall.distance({"A": 0.5, "B": 0.5}, {"A": 0.5, "B": 0.5}) == 0.0


def test_wanted_notional_reads_the_planners_note():
    assert shortfall.wanted_notional(order("B", "BUY", 1000, 20.0, NOTE)) == 100_000.0
    assert shortfall.wanted_notional(order("B", "BUY", 1000, 20.0)) == 20_000.0


def test_decision_row_costs_and_gaps():
    row = shortfall.decision_row(decision(), before={"A": 0.5})
    assert row["orders"] == 2 and row["scaled"] == 1 and row["scaled_tickers"] == "B" and row["dropped"] == 0
    assert row["wanted_notional"] == 105_000.0 and row["ordered_notional"] == 25_000.0
    assert abs(row["ordered_share"] - 25_000 / 105_000) < 1e-12
    assert row["explicit_cost"] == 10.0
    # the sell filled 1 below its decision price (100 lost), the buy 0.5 above (500 lost)
    assert abs(row["delay_cost"] - 600.0) < 1e-9 and abs(row["delay_bps"] - 6.0) < 1e-9
    assert abs(row["shortfall_bps"] - 6.1) < 1e-9
    assert abs(row["gap_before"] - 0.5) < 1e-12 and abs(row["gap_after"] - 0.2) < 1e-12
    assert shortfall.decision_row(decision())["gap_before"] is None             # no fill history given


def test_pre_trade_weights_come_from_earlier_fills_only():
    close = pd.DataFrame({"A": [50.0, 49.0], "B": [20.0, 20.5]}, index=pd.to_datetime(["2024-01-31", "2024-02-01"]))
    earlier, own = fill("A", "BUY", 10_000, 48.0, 0.0, date="2024-01-02"), fill("B", "BUY", 1000, 20.5, 0.0)
    fills = [earlier, own]                                    # a fill on the execution date belongs to this decision
    before = shortfall.pre_trade_weights(decision(), fills, close)
    assert before == {"A": 0.5}
    rows = shortfall.table([decision(status="dry_run"), decision()], fills, close)
    assert len(rows) == 1 and abs(rows[0]["gap_before"] - 0.5) < 1e-12
    assert shortfall.pre_trade_weights(decision(signal_date="2023-12-29"), fills, close) is None      # no price that day
    unpriced = [fill("Z", "BUY", 5, 1.0, 0.0, date="2024-01-02")]
    assert shortfall.pre_trade_weights(decision(), unpriced, close) is None                          # a holding with no price


def test_risk_checks_are_counted_per_check():
    later = decision(signal_date="2024-02-29")
    later["risk"] = {**later["risk"], "checks": [check("participation", "block", 2, 0.1, "x"), check("long_only", "pass")]}
    rows = {r["check"]: r for r in shortfall.risk_checks([later, decision()])}
    part = rows["participation"]
    assert part["decisions"] == 2 and part["warned"] == 1 and part["blocked"] == 1 and part["last_value"] == 2
    assert rows["long_only"]["passed"] == 1
