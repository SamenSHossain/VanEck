# VanEck ETF allocation study

Can simple allocation rules beat equal weight and 60/40 on nine VanEck ETFs
(SMH, MOAT, GDX, ANGL, REMX, HAP, FLTR, PFXF, OUNZ) out of sample, after
trading costs and taxes? This repository implements the gated plan in
[`docs/revised-vaneck-project.md`](docs/revised-vaneck-project.md) as a
reproducible Python study, and wraps it in the operational layer needed to run
the same decisions as a monthly process: data health gates, pre-trade risk
controls, a paper broker with audited decision records, and a monitor that
reconciles execution against research.

**Research answer so far: no.** Gate 0 passes (the funds are genuinely
diversified), but Gate 1 fails: monthly-rebalanced equal weight beats the
plan's 60/40 by only 0.07 Sharpe after tax, not the required 0.20, and its
turnover is above the plan's limit. Run informationally, none of the six locked
allocation rules comes close to Bonferroni significance, and the pre-specified
regime overlay subtracts value. The write-up is in
[`reports/FINDINGS.md`](reports/FINDINGS.md); the generated tables and figures
are in [`reports/REPORT.md`](reports/REPORT.md).

## Quick start

```bash
pip install -r requirements.txt
make test            # 57 tests: lookahead invariance, tax lots, risk controls, paper-vs-research reconciliation
make lint            # ruff
make report          # Gate 0 -> Gate 1 -> Phase 2 (+ sensitivities), writes reports/
```

## Three layers

| Layer | What it does | Modules |
|---|---|---|
| **Research** | Pre-registered walk-forward study: data, lookahead guard, cost and tax models, baselines, locked strategies, Bonferroni-corrected tests, regime analysis, gates | `config` `data` `calendar` `universe` `costs` `tax` `portfolio` `baselines` `regimes` `signals` `optimizers` `strategies` `stats` `metrics` `study` `report` |
| **Infrastructure** | Reliable, observable runs: run manifests (git SHA, config hash, data hash), data health gate with exit codes, structured logs, CI on every push, scheduled monthly cycle | `audit` `health` `cli` `.github/workflows` |
| **Trading** | Disciplined decisions: explicit pre-trade risk limits, idempotent decision records, paper broker with real share/dividend/split mechanics, monitor that reconciles paper against research and raises alerts | `risk` `broker` `live` `monitor` |

The operational layer is exercised with equal weight as a benchmark control,
because no rule earned deployment. See [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
for the runbook.

## Commands

| Command | What it does |
|---|---|
| `python -m vaneck fetch [--force]` | refresh the raw Yahoo Finance cache in `data/raw/` (the committed cache reproduces everything offline) |
| `python -m vaneck gate0` / `gate1` / `phase2` / `all [--force]` | run the study through that gate; without `--force` it stops at the first failed gate, as the plan prescribes |
| `python -m vaneck validate` | the untouched 2025+ hold-out (only meaningful after a Phase 2 winner; deliberately not run) |
| `python -m vaneck check [--json]` | data health report; exit code 2 on a blocking problem |
| `python -m vaneck paper init|status|reset` | manage the paper book |
| `python -m vaneck rebalance --strategy S [--as-of D] [--submit]` | one decision: health, targets, orders, risk checks, decision record; dry run unless `--submit` |
| `python -m vaneck replay --strategy S --start D --end D` | operational rehearsal of the monthly cycle through the paper broker over a past window |
| `python -m vaneck monitor --strategy S [--mark]` | reconcile the paper book with the research engine; exit code 1 on alerts |

Add `--log-json` before the command for structured logs.

## What is locked where

Every modelling decision lives in `vaneck/config.py`; every operational limit in
`vaneck/risk.py` and `vaneck/health.py`. Both are documented, with the reasons
for each resolved ambiguity, in [`docs/SPEC.md`](docs/SPEC.md). Changing a
value there after looking at results is a protocol violation.

## Layout

```
vaneck/
  config.py      locked specification (universe, windows, caps, costs, taxes, regimes, gates)
  data.py        yfinance fetch + cache, aligned MarketData, window() lookahead guard, synthetic data
  calendar.py    first-trading-day execution dates, prior-close signal dates, 3y/1y monthly splits
  universe.py    Gate 0: correlations, 60-day rolling correlations, theme clusters, redundancy
  costs.py       spread tiers by dollar volume + slippage + fees (override file supported)
  tax.py         FIFO/HIFO tax lots, wash sales, distribution tax, annual settlement, carryforward
  portfolio.py   daily research simulator (pre- and post-tax NAV, turnover, costs, taxes)
  baselines.py   equal weight, 60/40 (plan definition and SPY/BND), buy-and-hold SMH
  regimes.py     ex-ante regime calendar (Crisis > Stagflation > Bull > Correction > Neutral)
  signals.py     None / Momentum / Drift -> pinned weights and bounds
  optimizers.py  long-only min-variance and max-Sharpe with box bounds (SLSQP)
  strategies.py  3 signals x 2 optimizers, regime overlay
  stats.py       paired one-sided t-tests, block bootstrap of Sharpe differences, Bonferroni
  metrics.py     Sharpe, drawdown, calendar-year returns, turnover, regime breakdown
  study.py       orchestration: runs, Gate 1, Phase 2 tests, Gate 3, sensitivities
  report.py      markdown tables and figures
  audit.py       run manifests: run id, git SHA, config hash, data hash, versions
  health.py      data health checks with block/warn severities
  risk.py        pre-trade risk limits, order construction, risk report
  broker.py      Broker protocol and the paper broker (shares, cash, dividends, splits, fills)
  live.py        idempotent rebalance decision pipeline, decision records, replay
  monitor.py     paper-vs-research reconciliation and alerts
  cli.py         python -m vaneck ...
tests/           57 tests across all layers
data/raw/        cached daily history per ticker (Open/High/Low/Close/Adj Close/Volume/Dividends/Splits)
docs/            the plan, the pre-registered SPEC, the operations runbook
reports/         REPORT.md, FINDINGS.md, tables/*.csv, figures/*.png, gates.json, run_manifest.json, monitor_*.md
paper/           paper book and decision records from the 2024 rehearsal and the latest dry run
.github/         ci.yml (lint, tests, deterministic dry-run decision), monthly.yml (scheduled cycle)
```

## Data

Daily data from Yahoo Finance via `yfinance`, cached verbatim so the study is
reproducible without network access. Total-return prices are Yahoo's adjusted
close; distribution yields for the tax model come from the dividend series and
the unadjusted close. Macro inputs: S&P 500, VIX, 10-year yield, 13-week
T-bill, and the S&P GSCI as the commodity-index proxy (the CRB index is not
available on Yahoo).

## Cloud environment

Sessions run in a personal "VanEck" cloud environment whose network allowlist
contains the Yahoo Finance hosts plus stooq.com and fred.stlouisfed.org as
fallbacks, and whose setup script installs `requirements.txt`.
