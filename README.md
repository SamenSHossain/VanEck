# VanEck ETF allocation study

Can simple allocation rules beat equal weight and 60/40 on nine VanEck ETFs
(SMH, MOAT, GDX, ANGL, REMX, HAP, FLTR, PFXF, OUNZ) out of sample, after
trading costs and taxes? This repository implements the gated plan in
[`docs/revised-vaneck-project.md`](docs/revised-vaneck-project.md) as a
reproducible Python study.

**Answer so far: no.** Gate 0 passes (the funds are genuinely diversified), but
Gate 1 fails: monthly-rebalanced equal weight beats the plan's 60/40 by only
0.07 Sharpe after tax, not the required 0.20, and its turnover is above the
plan's limit. Run informationally, none of the six locked allocation rules
comes close to Bonferroni significance, and the pre-specified regime overlay
subtracts value. The write-up is in [`reports/FINDINGS.md`](reports/FINDINGS.md);
the auto-generated tables and figures are in [`reports/REPORT.md`](reports/REPORT.md).

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                 # 41 tests incl. lookahead-bias checks
python -m vaneck all --force        # Gate 0 -> Gate 1 -> Phase 2 (+ sensitivities), writes reports/
```

`python -m vaneck all` without `--force` stops at the first failed gate, as the
plan prescribes. Other commands:

| Command | What it does |
|---|---|
| `python -m vaneck fetch [--force]` | refresh the raw Yahoo Finance cache in `data/raw/` (the committed cache is enough to reproduce everything offline) |
| `python -m vaneck gate0` | correlation / theme / redundancy analysis only |
| `python -m vaneck gate1` | baselines and the Gate 1 go/no-go |
| `python -m vaneck phase2 [--force]` | the six locked strategies, statistics, regime breakdown |
| `python -m vaneck validate` | the untouched 2025+ hold-out (only meaningful if Phase 2 had a winner; it did not, so this has deliberately not been run) |

## What is locked where

Every modelling decision lives in `vaneck/config.py` and is documented, with
the reasons for each resolved ambiguity, in [`docs/SPEC.md`](docs/SPEC.md).
Changing a value there after looking at results is a protocol violation.

## Layout

```
vaneck/
  config.py      locked specification (universe, windows, caps, costs, taxes, regimes, gates)
  data.py        yfinance fetch + cache, aligned MarketData, window() lookahead guard, synthetic data
  calendar.py    first-trading-day execution dates, prior-close signal dates, 3y/1y monthly splits
  universe.py    Gate 0: correlations, 60-day rolling correlations, theme clusters, redundancy
  costs.py       spread tiers by dollar volume + slippage + fees (override file supported)
  tax.py         FIFO/HIFO tax lots, wash sales, distribution tax, annual settlement, carryforward
  portfolio.py   daily simulator (pre- and post-tax NAV, turnover, costs, taxes) and build_targets()
  baselines.py   equal weight, 60/40 (plan definition and SPY/BND), buy-and-hold SMH
  regimes.py     ex-ante regime calendar (Crisis > Stagflation > Bull > Correction > Neutral)
  signals.py     None / Momentum / Drift -> pinned weights and bounds
  optimizers.py  long-only min-variance and max-Sharpe with box bounds (SLSQP)
  strategies.py  3 signals x 2 optimizers, regime overlay
  stats.py       paired one-sided t-tests, block bootstrap of Sharpe differences, Bonferroni
  metrics.py     Sharpe, drawdown, calendar-year returns, turnover, regime breakdown
  study.py       orchestration: runs, Gate 1, Phase 2 tests, Gate 3, sensitivities
  report.py      markdown tables and figures
  cli.py         python -m vaneck ...
tests/           lookahead invariance, calendar, tax lots & wash sales, costs, optimizers, regimes, metrics
data/raw/        cached daily history (Open/High/Low/Close/Adj Close/Volume/Dividends) per ticker
docs/            the plan, the pre-registered SPEC
reports/         REPORT.md, FINDINGS.md, tables/*.csv, figures/*.png, gates.json
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
