# Pre-registered specification (locked)

This file records every modelling decision the backtests depend on, including
how ambiguities in `revised-vaneck-project.md` were resolved. All rules were
implemented in `vaneck/config.py` and the strategy code **before** any strategy
result was computed. Nothing in this document was changed in response to
results. Where the implementation deviates from the plan text, the deviation
and its reason are listed in the last section.

## 1. Data

| Item | Decision |
|---|---|
| Source | Yahoo Finance via `yfinance` (`Ticker.history(period="max", auto_adjust=False)`), cached verbatim in `data/raw/` |
| Universe | SMH, MOAT, GDX, ANGL, REMX, HAP, FLTR, PFXF, OUNZ |
| Auxiliary tradeables | SPY, BND (60/40 benchmarks), VGIT (named Crisis hedge in the plan) |
| Macro inputs | ^GSPC (S&P 500), ^VIX, ^TNX (10y yield, %), ^IRX (13-week T-bill, %), ^SPGSCI (S&P GSCI) |
| Total-return prices | Yahoo `Adj Close` (splits and distributions reinvested on the ex-date) |
| Distribution yield | `Dividends[t] / Close[t-1]` (plus ETF capital-gain distributions) |
| Calendar | SPY's trading days; macro series forward-filled onto it |
| Common start | 2014-05-16 (OUNZ inception) |
| Main window | OOS evaluation 2017-06-01 to 2024-12-31 (first execution date with a full 3-year training window) |
| Hold-out | 2025-01-01 onward, untouched by every gate; used only by `python -m vaneck validate` |

## 2. Walk-forward and execution

* **Execution date** = first trading day of each month, trades at that close.
* **Signal date** = the trading day before the execution date (the last close of
  the previous month). Every decision for an execution date uses only data with
  timestamp <= signal date; this is enforced mechanically by `MarketData.window()`
  and tested by perturbing post-signal data (`tests/test_lookahead.py`).
  Consequence: the January 2021 rebalance uses data through 2020-12-31 only.
* **Training window** = 3 years ending on the signal date (covariance estimation).
* **Signal lookback** = 60 trading days ending on the signal date.
* **Test window** = 1 year from the execution date; windows roll monthly. Because
  decisions are re-made monthly, the out-of-sample record is the chained sequence
  of monthly decisions; calendar-year slices of it are the units of the primary test.
* **Cash sleeve** earns the 13-week T-bill yield (^IRX), accrued daily; the same
  series is the risk-free rate in every Sharpe ratio. (Plan: flat 4%; see deviations.)
* **Initial capital** $10M (bottom of the plan's $10M-$100M AUM range).

## 3. Gate 0 (universe)

* Correlations use daily total returns 2014-05-16 to 2024-12-31.
* Average pairwise correlation must be < 0.75.
* Themes = average-linkage hierarchical clusters on distance 1 - rho, cut at rho = 0.60; at least 5 required.
* Redundancy: no pair with rho > 0.95.
* 60-day rolling pairwise correlations are reported as a timeline.

## 4. Baselines

| Name | Definition |
|---|---|
| EqualWeight | 1/9 in each fund, rebalanced monthly |
| 60/40 VanEck (plan definition, primary benchmark) | 60% equal-weight across the five equity funds (SMH, MOAT, GDX, REMX, HAP) + 40% BND, monthly |
| 60/40 Classic (reference) | 60% SPY + 40% BND, monthly |
| Buy&Hold SMH | 100% SMH bought once, never rebalanced |

## 5. Transaction costs

`one_way_cost = (spread/2 + 0.5 bp) x |notional| + $0.001 per trade`

| Trailing 60-day average dollar volume | Quoted spread (bps of price) |
|---|---|
| >= $500M | 2 |
| $100M-$500M | 4 |
| $10M-$100M | 10 |
| $1M-$10M | 20 |
| < $1M | 40 |

The tier is looked up at each rebalance from volume data known at the signal
date. A `data/spreads_override.csv` (ticker, spread_bps) with live broker quotes
overrides the tier table when present. Market impact is not modelled (knob
available, default off).

## 6. Taxes

* Single 37% rate on realized gains (any holding period) and on distributions.
* Lot relief: FIFO (IRS default). HIFO is reported as a sensitivity.
* Reinvested distributions increase cost basis (no double taxation of income).
* Wash sales: a loss on fund X is disallowed to the extent X (or a configured
  substantially-identical group member) was bought within 30 calendar days before
  or after; the disallowed loss is added to the replacement lot's basis. Default
  groups: none (Gate 0 found no pair above 0.95).
* Taxes for year Y are paid out of the portfolio at the first rebalance of year
  Y+1 (annual settlement); net losses carry forward. The $3,000 ordinary-income
  offset is ignored. Wash reversals triggered after a year has been settled are
  charged in the following year (timing approximation).
* Post-tax NAV = gross value - accrued unpaid liability (so the post-tax curve is
  continuous through settlements). Pre-tax and post-tax are separate simulations.

## 7. Regimes (locked ex-ante; precedence top to bottom)

| Regime | Rule | Action on the rule's weights |
|---|---|---|
| Crisis | VIX > 25 OR S&P 500 drawdown from its running peak <= -20% | hold only OUNZ, GDX, VGIT at min(20%, 1/3) each; remainder cash |
| Stagflation | S&P GSCI > +20% YoY AND 10y yield > 4% | ANGL, GDX, FLTR set to 20% each; other funds share the rest in proportion to the rule, each >= 5% |
| Bull | VIX < 15 AND S&P 500 YTD > 0 | hold the rule |
| Correction | 15 <= VIX <= 25 AND abs(S&P 500 YTD) <= 10% | scale weights by 0.75 (25% to cash) |
| Neutral | none of the above | hold the rule (treated as Bull) |

YTD uses the prior year's last close; YoY uses the close one calendar year
earlier. The regime applied at a rebalance is the regime on the signal date.

## 8. Signals, optimizers and how they compose

The plan lists 3 signals and 2 optimizers and asks for their 6 combinations
without saying how a signal and an optimizer interact. Locked rule:

* A **signal** pins the weights of its favoured funds and sets bounds for the rest.
  * None: nothing pinned; every fund bounded [5%, 20%].
  * Momentum: top 3 by 60-day return pinned at 20% each; others bounded [5%, 10%].
  * Drift: top 3 by share of up-days pinned at 15% + 5% x clip((drift - 0.5)/0.5, 0, 1); others bounded [5%, 10%].
* An **optimizer** allocates the residual budget to the non-pinned funds inside their bounds, using the covariance of daily returns over the 3-year training window:
  * MinVar: minimise total portfolio variance (pinned positions included).
  * MaxSharpe: maximise (mu'w)/sqrt(w'Sw) with mu = annualised 60-day trailing return minus the cash yield. If no feasible portfolio has positive expected excess return the optimizer falls back to MinVar.
* Solver: SLSQP from three starts (interior, MinVar, greedy-linear); weights are clipped to bounds and renormalised.

## 9. Strategy set and multiple-testing budget

* **Primary (6 tests):** {None, Momentum, Drift} x {MinVar, MaxSharpe}, each with the regime overlay of section 7. Bonferroni alpha = 0.05/6 = 0.0083.
* **Diagnostics (not hypothesis tests):** the same 6 without the overlay, reported to attribute effects to the overlay versus the rule. If these were ever promoted to tests the denominator would be 12.

## 10. Statistics

* Primary: paired one-sided t-test of full calendar-year OOS returns (strategy minus 60/40 VanEck), H1: mean > 0, post-tax. 2018-2024 gives 7 years.
* Supplementary (reported, not decisive): paired one-sided t-test on monthly returns; paired moving-block bootstrap (21-day blocks, 2000 draws) of the annualised Sharpe difference.

## 11. Gates

| Gate | Decisive criteria |
|---|---|
| 0 | avg pairwise rho < 0.75; >= 5 themes; no pair > 0.95 |
| 1 | EW post-tax Sharpe >= 60/40 VanEck post-tax Sharpe + 0.20; EW post-tax max drawdown not worse than -40%; EW one-way turnover < 15%/yr; EW post-tax Sharpe >= 0.30 |
| 2 | at least one primary strategy with p < 0.0083 (post-tax annual test) |
| 3 | winners' Crisis drawdown within 5 percentage points of EW's; benefit not confined to one regime |

**Turnover** = sum over rebalances of |trades| / (2 x NAV), i.e. one-way, excluding the initial funding trade, annualised by calendar time.

The CLI stops at a failed gate; `--force` continues and labels later phases "informational".

## 12. Deviations from the plan text (all decided before running)

| Plan text | Implementation | Reason |
|---|---|---|
| Cash earns a flat 4% | 13-week T-bill yield (^IRX); 4% flat kept as a sensitivity | A flat 4% over 2017-2021 (T-bills near 0%) would reward every cash-holding regime action for free and bias Phase 2 toward the overlay |
| "Pull 5-year daily prices", period 2019-2024 | All common history from 2014-05-16; OOS 2017-06 to 2024-12 (7 full years) | 5 years of data cannot hold a 3-year training window plus 5 OOS years; more OOS years strengthen the annual t-test |
| CRB Commodity Index | S&P GSCI (^SPGSCI) | CRB is not on Yahoo Finance; GSCI is the closest freely available broad commodity index |
| Crisis "hold hedges only (OUNZ, GDX, VGIT)" | VGIT pulled as an auxiliary tradeable; each hedge capped at the 20% position limit, remainder cash | Keeps the position cap consistent; VGIT is not in the 9-fund universe |
| Four regimes | Fifth "Neutral" label for days matching none | The four definitions do not partition the state space |
| 15% cap in the scaffold table vs 5-20% in Phase 2 | 15% applies to the Phase 1 scaffold (non-binding at 1/9); 5-20% to allocation rules | Both appear in the plan |
| "T-test on annual returns across 5 OOS years" | 7 full OOS years (2018-2024) | Data permits more |
| Rebalance uses data "as of market close on rebalance date" and "Jan 2021 rebalance uses only data through Dec 2020" | Signal on prior close, execution at the rebalance-day close | The only reading consistent with both statements |
| Bid-ask pulled from brokers | Tiered table by dollar volume with an override file | No broker quote feed available in this environment; the override path is the plan's intended input |

## 13. Operational limits (pre-registered, `vaneck/risk.py` and `vaneck/health.py`)

These govern the paper/live decision pipeline, not the research results.

| Risk control | Limit | Breach |
|---|---|---|
| long only | weights >= 0 | block |
| per-position cap | 15% for equal weight, 20% for rules, 40%/60% for the 60/40 benchmarks | block |
| gross exposure | <= 100% | block |
| one-way turnover per rebalance | <= 75% of equity | block |
| participation | order notional <= 10% of trailing 60-day dollar volume | scale down, warn |
| minimum order | >= $500 | drop |
| cash after trades and estimated costs | >= 0 | block |
| signal staleness | <= 5 calendar days | block |
| drawdown halt | book drawdown > -25% from peak | otherwise buys removed, sells kept |
| cost reserve | 10 bps of equity withheld from targets | - |

| Health check | Block | Warn |
|---|---|---|
| latest bar present for every tradeable | missing | - |
| NaNs in the last 5 rows | any | - |
| adjustment-factor break in the last 60 rows | any | - |
| macro sanity (no NaN; VIX in [5, 150]; 10y in [0, 25]) | outside | - |
| age of the last bar | > 10 days | > 5 days |
| daily move in the last 5 rows | - | > 25% |
| zero volume on the last bar | - | any |
| raw cache age | - | > 3 days |
| signal history per fund | < 61 bars | - |

Monitoring tolerance: paper book versus research engine fed the executed
weights must agree within 1% cumulative per year; the same tolerance applied
to the intended targets flags an implementation gap rather than an engine fault.

---

# Pre-registered extensions (written before any extension backtest was run)

Status: the main study is closed with Gate 1 failed. The three extensions
below are new, pre-registered analyses motivated by the main study's findings
(taxes dominate frictions; the annual test has no power; the participation
limit binds). They do not reopen the gates; they answer three stated questions
with thresholds fixed here first.

## 14. Tax-aware execution (question: does execution policy change the Gate 1 verdict?)

Policy ladder, applied identically to every portfolio and evaluated pre- and
post-tax over the same OOS window (2017-06-01 to 2024-12-31):

| Policy | Definition |
|---|---|
| P0 | monthly full rebalance to target, FIFO lots (the main study) |
| P1 | P0 with TAXOPT lot selection |
| P2 | P1 plus a relative drift band of 20% of target weight |
| P3 | P2 plus tax-loss harvesting with the substitute map below |

Rules (all fixed now):

* **TAXOPT lot selection.** When selling, lots are relieved in this order:
  lots with an unrealized loss (largest loss per unit first), then long-term
  gain lots (smallest gain per unit first), then short-term gain lots (smallest
  gain per unit first). Long term = held more than 365 days. Under the single
  37% rate this is HIFO with a long-term preference.
* **Drift band.** A position (or its exposure group, see harvesting) is traded
  only when its weight is outside target x (1 +/- 0.20); it is then traded back
  to target. If buys exceed the cash raised by sells plus existing cash, buys
  are scaled down pro rata (no leverage, no forced gain realisation). Cash above
  1% of equity is deployed to the most underweight groups toward target.
* **Harvesting.** At each monthly decision, for every held fund, lots whose
  unrealized loss is at least 5% of the lot's basis and at least 5 bps of
  portfolio equity are sold, provided the fund was not bought in the previous
  30 days. Proceeds buy the fund's substitute, which then serves the fund's
  target as part of the same exposure group. A fund that was loss-sold is not
  repurchased for 31 days. When the substitute itself qualifies for harvesting,
  it is sold and the original fund is bought back (roles alternate).
* **Substitute map** (different issuer and index for every pair): SMH->SOXX,
  MOAT->QUAL, GDX->RING, ANGL->FALN, REMX->PICK, HAP->GNR, FLTR->FLOT,
  PFXF->PFF, OUNZ->IAU, SPY->IVV, BND->AGG, VGIT->IEI. Physical gold trusts
  (OUNZ/IAU) are the greyest pair under the substantially-identical standard; the
  result is reported with and without that pair.
* **Taxes.** Single 37% rate as in the main study. Sensitivity: short-term
  gains at 46% (37% federal + 3.8% NIIT + about 5% state), long-term at 37%.
* **Portfolios.** EqualWeight and 60/40 VanEck (the Gate 1 pair), 60/40
  Classic (reference), DriftxMinVar without overlay (informational, the best
  diagnostic rule).
* **Pre-registered readout.** (a) The Gate 1 margin (EW post-tax Sharpe minus
  60/40 VanEck post-tax Sharpe) under each policy; the verdict changes only if
  the margin reaches 0.20 under a policy applied to both portfolios. (b) The
  post-tax Sharpe gain of each policy over P0 for each portfolio. (c) Pre-tax
  tracking error of P2/P3 against P0 (the exposure fidelity cost of trading less).

## 15. Statistical design (question: what edge could the tests detect?)

* **Power.** For a paired one-sided t-test, the minimum detectable effect at
  80% power is computed from the noncentral t distribution using the observed
  standard deviation of the difference series, at alpha 0.0083 (Bonferroni) and
  0.05, for the annual series (n = 7) and the monthly series (n = 90). The
  required sample size for the best diagnostic rule's observed effect is also
  reported.
* **HAC paired test.** Monthly differences versus 60/40 VanEck, Newey-West
  standard errors with Bartlett kernel and lag floor(0.75 n^(1/3)), one-sided
  p-value from t with n-1 degrees of freedom.
* **Deflated Sharpe ratio** (Bailey and Lopez de Prado 2014) on monthly
  excess returns for each strategy, with the number of trials N = 6 (primary set)
  and N = 12 (including the no-overlay diagnostics); the expected maximum Sharpe
  under the null uses the cross-sectional variance of the trials' Sharpe
  ratios. The probabilistic Sharpe ratio of the strategy-minus-benchmark series
  against a benchmark of zero is also reported.
* **Romano-Wolf stepdown.** One-sided max-t stepdown over the monthly
  difference series of all strategies in a family, circular block bootstrap
  with block length 6 and 2000 draws, studentised statistics; families: the 6
  primary strategies, and all 12. White's reality-check p-value is the adjusted
  p of the best strategy.
* **Status.** These are supplementary to the closed main study. For any future
  study the proposal is that the monthly HAC test with Romano-Wolf control
  replaces the annual Bonferroni test as the decisive one.

## 16. Liquidity model (question: at what AUM do these portfolios stop being implementable?)

* **Impact.** Square-root model: impact (bps) = 1.0 x daily volatility (bps)
  x sqrt(order notional / trailing 60-day average dollar volume), added to the
  spread and slippage cost for every fill. Daily volatility is the trailing
  60-day standard deviation of returns known at the signal date.
* **Participation cap.** No order may exceed 10% of trailing average dollar
  volume; the residual is deferred (the next decision re-targets). This is the
  same limit as the live risk control, so research and execution share it.
* **Capacity curve.** Pre-tax runs at initial capital 1M, 3M, 10M, 30M, 100M,
  300M, 1B dollars for EqualWeight, 60/40 VanEck, DriftxMinVar and
  MomentumxMaxSharpe (the highest-turnover rule). Readouts: friction drag in
  bps per year (spread + slippage + impact), and the annualised tracking error
  against the same targets run frictionless and uncapped. Capacity is the
  largest AUM with drag <= 25 bps per year and tracking error <= 50 bps per year.
* **Monitor.** The reconciliation adds a third research line that applies the
  participation cap to the intended targets; the remaining gap to the paper
  book should be whole-share rounding only.

## 17. Retirement rules for a paper or live portfolio (pre-registered before evaluation)

A deployed portfolio is switched off by rules fixed in advance, not by
judgement after a bad month. Expectation bands come from the research engine
run over the main study window (2017-06-01 to 2024-12-31) with the same
strategy and execution policy, pre-tax, and are frozen once per deployment
(`python -m vaneck expectations`). They are never updated with live data.

| Rule | Input | Trigger | Consequence |
|---|---|---|---|
| R1 realised returns | monthly return of the book | below the backtest's 5th percentile of monthly returns in 3 of the last 6 months | **retire** |
| R2 drawdown | drawdown from the book's peak | worse than 1.25 x the backtest's maximum drawdown | **retire** |
| R3 risk-adjusted | rolling 12-month Sharpe of the book | below the backtest's 5th percentile of rolling 12-month Sharpe for 3 consecutive months | review |
| R4 engine agreement | monitor's executed-weights reconciliation | beyond tolerance on 2 consecutive monitor runs | **halt** (engine or data fault, not the strategy) |
| R5 implementability | monitor's capped-intended reconciliation | beyond tolerance on 3 consecutive monitor runs | review |

"Retire" and "halt" write a flag the decision pipeline reads: new risk is
refused (sell-only decisions remain possible) until a human clears the flag
with a recorded reason. "Review" raises an alert and is recorded. A rule that
has fewer observations than it needs reports "insufficient history" and does
not fire. With the paper rehearsal (12 months) R1 and R2 can fire; R3 needs at
least 14 months of marks.
