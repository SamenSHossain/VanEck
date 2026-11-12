"""Feedback loop: reconcile the paper book with the research engine and raise alerts."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import metrics
from .broker import PaperBroker
from .config import REPORT_DIR, SPEC, Spec
from .costs import CostModel
from .data import MarketData
from .execution import ExecutionPolicy
from .portfolio import simulate
from .risk import RiskLimits
from .study import TRADEABLES


def _decisions(decisions_dir: Path, strategy: str) -> list[dict]:
    out = []
    for p in sorted(decisions_dir.glob("*.json")):
        d = json.loads(p.read_text())
        if d.get("strategy") == strategy:
            out.append(d)
    return out


def monitor(md: MarketData, broker: PaperBroker, decisions_dir: Path, strategy: str, spec: Spec = SPEC,
            limits: RiskLimits = RiskLimits(), cost_model: CostModel | None = None,
            tracking_tolerance_per_year: float = 0.01, out_dir: Path = REPORT_DIR,
            monitor_history: list[dict] | None = None, expectations_dir: Path | None = None,
            state_dir: Path | None = None) -> dict:
    from . import retire
    from .store import STATE_DIR
    cost_model = cost_model or CostModel(spec)
    state_dir = state_dir or STATE_DIR
    expectations_dir = expectations_dir or decisions_dir.parent
    curve = broker.equity_curve()
    rf = md.cash_yield(spec)
    book = broker._require()
    decisions = _decisions(decisions_dir, strategy)
    submitted = [d for d in decisions if d["status"] == "submitted"]
    alerts: list[str] = []
    rep: dict = {"strategy": strategy, "as_of": str(curve.index[-1].date()), "n_days": int(len(curve)),
                 "decisions": {s: sum(d["status"] == s for d in decisions) for s in
                               sorted({d["status"] for d in decisions})}}
    if len(curve) < 2:
        rep["alerts"] = ["not enough history to monitor"]
        return rep

    years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1e-9)
    perf = metrics.perf_summary(curve, rf)
    fills = broker.fills()
    if len(fills):
        funding_date = fills["date"].iloc[0]
        rebal = fills[fills["date"] != funding_date]
        turn = float(rebal["notional"].sum() / (2.0 * curve.mean()) / years)
    else:
        turn = 0.0
    rep["realized"] = {
        "total_return": perf["total_return"], "cagr": perf["cagr"], "ann_vol": perf["ann_vol"], "sharpe": perf["sharpe"],
        "max_drawdown": perf["max_drawdown"], "current_drawdown": broker.drawdown(),
        "turnover_one_way_per_year": turn, "costs_dollars": broker.realized_costs(),
        "costs_bps_per_year": 1e4 * broker.realized_costs() / curve.mean() / years,
        "dividends_received": float(sum(c["amount"] for c in book["cash_flows"] if c["type"] == "dividend")),
    }
    if broker.drawdown() <= limits.drawdown_halt + 0.05:
        alerts.append(f"drawdown {broker.drawdown():.1%} within 5 points of the halt level {limits.drawdown_halt:.0%}")

    # -- reconciliation with the research engine over the same decisions ---------- #
    # (1) mechanical: research engine fed the weights actually executed -> should agree tightly
    # (2) implementation gap: research engine fed the intended targets -> cost of the risk controls
    def _research(weight_key: str, policy: ExecutionPolicy | None = None):
        rows = {pd.Timestamp(d["exec_date"]): d[weight_key] for d in submitted if d.get(weight_key)}
        if not rows:
            return None
        cols = [c for c in TRADEABLES] + [c for c in md.adj_close.columns if c not in TRADEABLES]
        tg = pd.DataFrame(rows).T.reindex(columns=cols).fillna(0.0).sort_index()
        tg = tg.loc[:, (tg != 0).any(axis=0) | tg.columns.isin(TRADEABLES)]
        return simulate(md, tg, spec=spec, taxed=False, cost_model=cost_model, end=curve.index[-1],
                        initial=float(book["initial_cash"]), name=weight_key, policy=policy)

    if submitted:
        tol = tracking_tolerance_per_year * years
        rec = {"tolerance": tol, "paper_total_return": float(curve.iloc[-1] / curve.iloc[0] - 1)}
        capped = ExecutionPolicy(name="capped", max_participation=limits.max_participation)
        for key, label, pol in (("executed_weights", "executed", None), ("targets", "intended", None),
                                ("targets", "intended_capped", capped)):
            research = _research(key, pol)
            if research is None:
                continue
            joined = pd.concat([curve, research.nav.rename("nav")], axis=1).dropna()
            pr, rr = joined["paper_equity"].pct_change().dropna(), joined["nav"].pct_change().dropna()
            cum = float(joined["paper_equity"].iloc[-1] / joined["paper_equity"].iloc[0]
                        - joined["nav"].iloc[-1] / joined["nav"].iloc[0])
            rec[f"research_{label}_total_return"] = float(joined["nav"].iloc[-1] / joined["nav"].iloc[0] - 1)
            rec[f"cumulative_difference_vs_{label}"] = cum
            rec[f"tracking_error_ann_vs_{label}"] = float((pr - rr).std(ddof=1) * np.sqrt(252)) if len(pr) > 2 else np.nan
            rec[f"research_{label}_costs_dollars"] = float(research.costs.sum())
            if label == "executed" and abs(cum) > tol:
                alerts.append(f"paper vs research (executed weights) differ by {cum:+.2%}, tolerance {tol:.2%}: "
                              "engine disagreement, investigate")
            if label == "intended" and abs(cum) > tol:
                alerts.append(f"implementation gap vs intended targets {cum:+.2%} exceeds {tol:.2%}: "
                              "risk controls or fills are moving the book away from the research portfolio")
            if label == "intended_capped" and abs(cum) > tol:
                alerts.append(f"implementability gap {cum:+.2%} exceeds {tol:.2%}: "
                              "even with the participation cap applied in research the book cannot track the targets")
        rec["paper_costs_dollars"] = broker.realized_costs()
        rep["reconciliation"] = rec

    # -- decision hygiene -------------------------------------------------------- #
    blocked = [d for d in decisions if d["status"] == "blocked"]
    if blocked:
        alerts.append(f"{len(blocked)} blocked decision(s): " + ", ".join(d["signal_date"] for d in blocked[-3:]))
    scaled = [d["signal_date"] for d in submitted
              if any(c["name"] == "participation" and c["status"] == "warn" for c in d["risk"]["checks"])]
    rep["participation_scaled_rebalances"] = scaled
    if decisions:
        last = max(pd.Timestamp(d["signal_date"]) for d in decisions)
        gap = (curve.index[-1] - last).days
        rep["days_since_last_decision"] = int(gap)
        if gap > 40:
            alerts.append(f"no decision for {gap} days")
    # -- pre-registered retirement rules (SPEC s.17) ------------------------------ #
    exp = retire.load_expectations(strategy, expectations_dir)
    if exp is not None:
        results = retire.evaluate(curve, rf, exp, monitor_history=monitor_history or [])
        verdict = retire.overall(results)
        rep["retirement"] = {**retire.results_to_dict(results), "expectations_frozen_utc": exp.get("frozen_utc")}
        for r in results:
            if r.status in ("review", "retire", "halt"):
                alerts.append(f"retirement rule {r.rule} -> {r.status.upper()}: {r.detail}")
        if verdict in ("retire", "halt"):
            retire.set_flag(strategy, verdict, results, state_dir)
    else:
        rep["retirement"] = {"status": "no expectations frozen", "rules": []}
    rep["alerts"] = alerts
    rep["status"] = "alert" if alerts else "ok"

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"monitor_{strategy.replace('/', '-').replace(' ', '_')}.json").write_text(json.dumps(rep, indent=2, default=str))
    (out_dir / f"monitor_{strategy.replace('/', '-').replace(' ', '_')}.md").write_text(monitor_markdown(rep))
    return rep


def monitor_markdown(rep: dict) -> str:
    lines = [f"# Monitor: {rep['strategy']} (as of {rep['as_of']})", "", f"Status: **{rep.get('status', 'n/a').upper()}**", ""]
    if rep.get("alerts"):
        lines += ["Alerts:"] + [f"- {a}" for a in rep["alerts"]] + [""]
    for section in ("realized", "reconciliation"):
        if section in rep:
            lines += [f"## {section}", "", "| metric | value |", "|---|---|"]
            for k, v in rep[section].items():
                lines.append(f"| {k} | {v:.4f} |" if isinstance(v, float) else f"| {k} | {v} |")
            lines.append("")
    if rep.get("retirement", {}).get("rules"):
        lines += ["## retirement rules (SPEC s.17)", "", f"Overall: **{rep['retirement']['status'].upper()}**", "",
                  "| rule | status | value | threshold | detail |", "|---|---|---|---|---|"]
        for r in rep["retirement"]["rules"]:
            lines.append(f"| {r['rule']} | {r['status']} | {r['value']} | {r['threshold']} | {r['detail']} |")
        lines.append("")
    lines += ["## decisions", "", "| status | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in rep.get("decisions", {}).items()]
    if rep.get("participation_scaled_rebalances"):
        lines += ["", "Rebalances where orders were scaled by the participation limit: "
                  + ", ".join(rep["participation_scaled_rebalances"])]
    return "\n".join(lines) + "\n"
