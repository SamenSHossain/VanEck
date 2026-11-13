# Pre-registered specification (locked)

This file records every modelling decision the backtests depend on, including
how ambiguities in `revised-vaneck-project.md` were resolved. The rules were
implemented in `vaneck/config.py` and the strategy code **before** any strategy
result was computed, except the tie rules stated on 2026-11-10 and the
harvesting boundary changed on 2026-11-11 (section 20). This file was committed
together with the first results, so the repository history alone cannot show
that order. Sections 1 to 17 were fixed before the results they govern. One
threshold in them was changed afterwards: on 2026-11-11 the harvesting rule of
section 14 was set to treat a fund bought exactly 30 days earlier as recently
bought, which restated the P3 column (section 20).

Text added after results were known is marked where it stands: section 18
(descriptive displays), section 19 (the hold-out protocol, written before the
hold-out is opened), the corrections and tie rules of 2026-11-10, the
harvesting change of 2026-11-11 and the corrections of 2026-11-13 (section 20,
with notes in sections 6, 8, 11, 12 and 14), which restate some numbers and say
which. On 2026-11-13 descriptions in sections 12, 13, 15, 17 and 18 were also
corrected to match what the code does; no rule changed. The hold-out row of
section 1 was reworded on 2026-11-10 to refer to section 19. Where the implementation deviates from the plan text, the deviation
and its reason are listed in section 12. Approximations the code makes inside
these rules, each too small to change a result, are listed in section 21.

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
| Hold-out | 2025-01-01 onward, untouched by every gate; sealed, and opened only by `python -m vaneck validate --unseal` under section 19 |

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
  groups: none (Gate 0 found no pair above 0.95). A purchase on the day of the
  sale counts (made explicit on 2026-11-13; see section 20).
* Taxes for year Y are paid out of the portfolio at the first rebalance of year
  Y+1 (annual settlement); net losses carry forward. The $3,000 ordinary-income
  offset is ignored. Wash reversals triggered after a year has been settled are
  charged in the following year, the year of the replacement purchase (timing
  approximation). Until 2026-11-13 the code booked them to the settled year,
  where they were never charged; see section 20.
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
  * Ties in a ranking go to the fund listed first in section 1. (Stated on 2026-11-10; see section 20.)
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

Gate 3 was never reached: no strategy passed Gate 2. The code tabulates the
drawdown criterion in every regime for every primary strategy, but it
implements neither the regime-breadth criterion nor a Gate 3 verdict. (Noted
on 2026-11-13.)

**Turnover** = sum over rebalances of |trades| / (2 x NAV), i.e. one-way, excluding the initial funding trade, annualised by calendar time.

The CLI stops at a failed gate. `--force` continues; Phase 2 run after a failed
Gate 1 is labelled "informational" (a Gate 1 run forced past a failed Gate 0
carries no label; Gate 0 passed in this study).

## 12. Deviations from the plan text (all decided before running)

| Plan text | Implementation | Reason |
|---|---|---|
| Cash earns a flat 4% | 13-week T-bill yield (^IRX); 4% flat kept as a sensitivity | A flat 4% over 2017-2021 (T-bills averaged about 1%, and near 0% in 2020-2021) would reward every cash-holding regime action for free and bias Phase 2 toward the overlay |
| "Pull 5-year daily prices", period 2019-2024 | All common history from 2014-05-16; OOS 2017-06 to 2024-12 (7 full years) | 5 years of data cannot hold a 3-year training window plus 5 OOS years; more OOS years strengthen the annual t-test |
| CRB Commodity Index | S&P GSCI (^SPGSCI) | A broad commodity index available from the same free source as every other series |
| Crisis "hold hedges only (OUNZ, GDX, VGIT)" | VGIT pulled as an auxiliary tradeable; each hedge capped at the 20% position limit, remainder cash | Keeps the position cap consistent; VGIT is not in the 9-fund universe |
| Four regimes | Fifth "Neutral" label for days matching none | The four definitions do not partition the state space |
| 15% cap in the scaffold table vs 5-20% in Phase 2 | 15% applies to the Phase 1 scaffold (non-binding at 1/9); 5-20% to allocation rules | Both appear in the plan |
| "T-test on annual returns across 5 OOS years" | 7 full OOS years (2018-2024) | Data permits more |
| Rebalance uses data "as of market close on rebalance date" and "Jan 2021 rebalance uses only data through Dec 2020" | Signal on prior close, execution at the rebalance-day close | The only reading consistent with both statements |
| Bid-ask pulled from brokers | Tiered table by dollar volume with an override file | No broker quote feed available in this environment; the override path is the plan's intended input |
| Phase 1.3: "pre-tax and post-tax backtests differ by > 20%" | Reported in the Gate 1 checks as equal weight's post-tax Sharpe haircut (> 20%), not decisive | It measures how much taxes matter, not whether equal weight beats 60/40; the decisive Sharpe and drawdown criteria of section 11 are already post-tax. (Row added on 2026-11-13; the code has done this since the first commit.) |
| Overall success and failure: post-tax Sharpe above 1.0, below 0.5 | Not used | The gates decide. Equal weight's post-tax Sharpe of 0.37 would also count as a failure under the plan's own definition. (Row added on 2026-11-13.) |

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
| cost reserve | 10 bps of equity withheld from targets (with an execution policy P0 to P3, 10 bps of the cash balance) | - |

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
  37% rate this is HIFO with a long-term preference. Lots whose gain per unit
  differs by less than 1e-9 dollars count as equal and are relieved oldest
  first. (Stated on 2026-11-10; see section 20.)
* **Drift band.** A position (or its exposure group, see harvesting) is traded
  only when its weight is outside target x (1 +/- 0.20); it is then traded back
  to target. If buys exceed the cash raised by sells plus existing cash, buys
  are scaled down pro rata (no leverage, no forced gain realisation). Cash above
  1% of equity is deployed to the most underweight groups toward target.
* **Harvesting.** At each monthly decision, for every held fund, lots whose
  unrealized loss is at least 5% of the lot's basis and at least 5 bps of
  portfolio equity are sold, provided the fund was not bought in the previous
  30 days. Day 30 counts as inside (set on 2026-11-11; see section 20). Proceeds buy the fund's substitute, which then serves the fund's
  target as part of the same exposure group. A fund that was loss-sold is not
  repurchased for 31 days. When the substitute itself qualifies for harvesting,
  it is sold and the original fund is bought back (roles alternate).
* **Substitute map** (a different issuer for every pair, and a different index
  for every pair except SPY->IVV, which both track the S&P 500; OUNZ and IAU both
  hold physical gold, and BND and AGG track two versions of the US Aggregate
  bond index): SMH->SOXX,
  MOAT->QUAL, GDX->RING, ANGL->FALN, REMX->PICK, HAP->GNR, FLTR->FLOT,
  PFXF->PFF, OUNZ->IAU, SPY->IVV, BND->AGG, VGIT->IEI. Physical gold trusts
  (OUNZ/IAU) and the two S&P 500 funds (SPY/IVV) are the greyest pairs under the
  substantially-identical standard; the result is reported with and without the
  gold pair. (The parenthesis was corrected on 2026-11-13. SPY is held only by
  60/40 Classic, the reference portfolio, so the SPY/IVV pair touches no
  verdict.)
* **Taxes.** Single 37% rate as in the main study. Sensitivity: short-term
  gains at 46% (37% federal + 3.8% NIIT + about 5% state), long-term at 37%.
* **Portfolios.** EqualWeight and 60/40 VanEck (the Gate 1 pair), 60/40
  Classic (reference), DriftxMinVar without overlay (informational; the best
  diagnostic rule by pre-tax Sharpe, with lower turnover than DriftxMaxSharpe,
  which is marginally ahead post-tax).
* **Pre-registered readout.** (a) The Gate 1 margin (EW post-tax Sharpe minus
  60/40 VanEck post-tax Sharpe) under each policy; the verdict changes only if
  the margin reaches 0.20 under a policy applied to both portfolios. (b) The
  post-tax Sharpe gain of each policy over P0 for each portfolio. (c) Pre-tax
  tracking error of P2/P3 against P0 (the exposure fidelity cost of trading less).

**Correction, 2026-11-10 (after the results were known).** The rules above are
unchanged; this records a defect in how one of them was coded. "A fund that was
loss-sold is not repurchased for 31 days" read the loss-sale date from the
fund's holding record, and a fund that has been sold out completely has no
holding record. The window was therefore skipped exactly when the whole
position had been sold: the planner could swap back into a fund 30 days after
selling it at a loss, and the ledger then disallowed that loss as a wash sale.
In the research simulator the outcome also depended on floating-point residue
in the unit count, which is how it was found: a Python 3.11 and a Python 3.13
environment disagreed on one such swap. The planner now takes the ledger's
loss-sale date for every fund, held or not, in the research simulator and in
the paper and live brokers.

Repurchases inside the window in the P3 runs before the correction: equal
weight 1 (post-tax), 60/40 VanEck 1 (post-tax), DriftxMinVar 5 pre-tax and 4
post-tax. After: none. Effect on the P3 results:

| P3, post-tax Sharpe | Before | After |
|---|---|---|
| Equal weight | 0.4442 | 0.4441 |
| 60/40 VanEck | 0.3875 | 0.3696 |
| 60/40 Classic | 0.5316 | 0.5316 |
| DriftxMinVar | 0.4000 | 0.4027 |
| Gate 1 margin under P3 | 0.0567 | 0.0744 |

P0 to P2, the main study, extensions 2 and 3 and every verdict are unchanged.
The frozen reference results (`tests/golden`) were refrozen with this change.

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
  p of the best strategy (a studentised variant; White's original statistic is
  not studentised).
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
with `python -m vaneck retire clear --by NAME --reason TEXT`, which records
both (the command does not enforce them: the defaults are "operator" and an
empty reason). "Review" raises an alert and is recorded. A rule that
has fewer observations than it needs reports "insufficient history" and does
not fire. With the paper rehearsal (12 months) R1 and R2 can fire; R3 needs at
least 14 months of marks.

## 18. Dashboard diagnostics (descriptive, added after the results were known)

The interactive dashboard shows a few quantities that were not in the plan or
in sections 14 to 16. They were added on 2026-11-10, after every result above
had been seen. They describe the results; no gate, test or retirement rule
depends on them, and none of them is a new hypothesis test.

| Display | Definition |
|---|---|
| Gate 1 margin interval | 95% percentile interval of the post-tax Sharpe difference, equal weight minus each 60/40 benchmark, from the paired moving-block bootstrap of section 10 (daily returns, 21-day blocks, 2,000 draws, seed 0). The same routine already gives the supplementary Sharpe intervals of the Phase 2 strategies. Also shown: the share of draws at or above the Gate 1 requirement of +0.20. |
| Rolling 12-month edge | Trailing 252-trading-day post-tax return and Sharpe ratio (daily excess returns, annualised) of equal weight and the six locked rules, minus those of 60/40 VanEck, sampled at month ends. Neighbouring windows share most of their days, so counts of windows "ahead" are not independent observations. |
| Target weights | The weights each strategy decided at every rebalance, cash as the residual, with the regime the overlay saw on the signal date. |
| Assumption table | The main study's sensitivity runs (flat 4% cash, HIFO lots, no distribution tax, quarterly rebalancing of equal weight) restated as the Gate 1 margin under each assumption. |
| Paper book against the bands | Calendar-month returns and the drawdown path of the paper book against the frozen expectation values of section 17. The rule verdicts still come from `vaneck/retire.py`. |
| Frozen-results check | The golden tables recomputed by the current engine and compared value by value within the stored tolerances, with the configuration and data hashes of the stored results the dashboard reads (the export itself, the main report, the extension tables, the golden files and the expectation bands). The paper book, decision records and check outputs carry no hashes. |
| Operator state | The switches `python -m vaneck status` reads (data health gate, halt flag, retirement flags, approval signing, risk limits, monitor, last decision) as they stood when the data was exported, with the alert log and the halt history. Not a live reading. |
| Input inventory | Every stored input series with its role, provider, span, fetch time and file hash, beside the health checks of section 13 and the cross-validation checks of `python -m vaneck check --cross`. |
| Implementation shortfall | Per submitted decision of the paper book: the one-way distance to target before trading (positions from earlier fills at the signal-date close) and after it (executed weights at the execution-date close), the share of the wanted notional the participation cap let through, the explicit cost (the cost model's charge on the fills) and the delay cost (fill price against the signal-date close, signed so that a loss is positive). Read from the decision records and the book's fill history. |
| Capacity check | The paper book's latest equity over the capacity section 16 estimates for its strategy, with the modelled drag and tracking error at the largest tested size not above the book. |

A reading of the first row, for the record: the interval for equal weight
against 60/40 VanEck is about -0.04 to +0.18. It includes zero and lies below
the +0.20 the plan required, so on this sample the Gate 1 failure is not a
near miss, and the lead itself is not distinguishable from zero. This is a
description of the same data that decided Gate 1, not additional evidence.

## 19. The hold-out (sealed; this protocol was written before it was opened)

Section 1 sets aside everything from 2025-01-01 onward. Until 2026-11-10 the
only rule was that nothing touches it. This section fixes what happens if it
is opened. It was written by someone who had seen every result above and none
of the hold-out's, and it can be amended freely until the hold-out is opened,
not after.

* **State.** The hold-out is sealed. `python -m vaneck validate` reports the
  seal and how many trading days and rebalances wait behind it, and exits 2.
  Counting dates reads no prices.
* **Opening.** `python -m vaneck validate --unseal --by NAME --reason TEXT`
  opens it. The record (`reports/holdout/unsealed.json`: who, when, why, the
  last bar evaluated, the configuration and data hashes, the commit) is
  written before any hold-out number is computed, so a look cannot go
  unlogged. It opens once: a second `--unseal` is refused and only the frozen
  tables are shown.
* **Evaluation** (`vaneck/holdout.py`). The four baselines and the six locked
  rules, started fresh at the first rebalance on or after the hold-out start,
  with the main study's costs, taxes and walk-forward windows, through the
  last bar in the data on the day it is opened. Reported: the same summary
  table as Gate 1 and Phase 2, and the post-tax Sharpe margin of equal weight
  over each 60/40 benchmark with the paired block-bootstrap interval of
  section 10.
* **Reading.** Gate 1 failed in sample, so the hold-out cannot confirm a
  strategy and no gate is decided again. It is a consistency check on the one
  in-sample observation worth checking: equal weight's small lead over 60/40
  VanEck (+0.07 post-tax Sharpe, 95% interval about -0.04 to +0.18). With
  under two years of data the hold-out interval will be wide. Three verdicts
  are allowed and no others, checked in this order: "contradicts the lead"
  when the hold-out interval lies wholly below zero; "consistent" when the
  hold-out margin lies inside the in-sample interval; "uninformative"
  otherwise. The verdict is read from the reported margin and interval; no
  code computes it. (The order was added on 2026-11-13, before the hold-out
  was opened: without it a margin inside the in-sample interval whose own
  interval lies below zero met two verdicts.)
* **Afterwards.** The tables are frozen at the last bar evaluated. Data that
  arrives later is new out-of-sample data, and a later look at it needs its
  own entry in this section before it is taken.
* **What does not open it.** Data health checks and dry-run decisions on the
  newest bar read recent prices to check the data and to form signals. None
  of them computes the performance of a strategy or of a benchmark after
  2024-12-31, and the dashboard shows no performance after that date; it does
  show data-check readings, input-file spans and decision records for newer
  bars. The scheduled monthly job makes its dry run on a fresh book and runs
  the monitor only once the hold-out has been opened (section 20, 2026-11-13).

## 20. Corrections and clarifications of 2026-11-10 (after the results were known)

The frozen results did not reproduce on every machine: the same commit passed
the golden test on one CI runner and failed it on another. Four causes were
found. Each was a decision that the rules above did not state and the code
left to floating-point rounding or to a library's choice of algorithm. Each is
now an explicit rule. No pre-registered threshold was changed.

1. **The no-rebuy window** was dropped for a fund that had been sold out
   completely. This is the one real defect; see the correction note in
   section 14.
2. **Ties in a signal's ranking.** A drift score is a count of up-days out of
   60, so equal scores are common: in 26 of the 91 rebalances the top-3 cut
   fell inside a tie. The ranking used a library sort that does not define the
   order of equal keys and chooses its algorithm by CPU. Rule: equal scores go
   to the fund listed first in section 1. Against the machine that produced
   the first results, the favoured set changes in 2 of the 91 rebalances
   (2020-10-30 and 2021-12-31: ANGL instead of PFXF, both at the same score).
3. **Ties between tax lots.** Two lots bought at the same price have the same
   gain per unit, up to rounding in `basis / units`. Which was relieved first
   under TAXOPT or HIFO rested on the last bits (the HAP lots of 2021-08-02
   and 2021-12-01, first decided on 2022-02-01), and changed the character of
   later gains. Rule: gains per unit within 1e-9 dollars are equal, and equal
   lots are relieved oldest first. No frozen value moved on the original
   machine.
4. **Rounding residue.** A sale that would leave less than 1e-6 units closes
   the lot and the position, and trades below 1e-4 dollars are not generated.
   The thresholds used before (1e-12 units, 1e-9 dollars) were the size of the
   residue itself for a portfolio of this size. No frozen value moved.

Effect of rule 2, the only one that moves results on the original machine. No
gate, significance outcome or verdict changed. The other statistics of the four
Drift rules moved with it, including their rows in extensions 1 to 3
(DriftxMinVar P3 post-tax Sharpe 0.4027 to 0.4041). The post-tax Sharpe ratios
of the main study:

| Post-tax Sharpe | Before | After |
|---|---|---|
| DriftxMinVar+Regime | 0.2365 | 0.2370 |
| DriftxMaxSharpe+Regime | 0.2250 | 0.2247 |
| DriftxMinVar (no overlay) | 0.3728 | 0.3738 |
| DriftxMaxSharpe (no overlay) | 0.3741 | 0.3745 |

Check made after the change: the 248 frozen values were recomputed under
AVX-512, AVX2 and SSE-only arithmetic kernels, three BLAS kernels,
single-threaded BLAS, and two environments (Python 3.11 with numpy 2.4, Python
3.13 with numpy 2.5). All agree within 4e-7 relative. The largest spread is on
the MaxSharpe strategies, most likely from the optimizer's stopping tolerance;
the golden test allows 1e-6. `docs/OPERATIONS.md` gives the command.

**Change of 2026-11-11, decided by the owner of this protocol.** The
harvesting rule of section 14 skips a fund "bought in the previous 30 days".
The code tested `days < 30`, so a fund bought exactly 30 days earlier was
still harvested, while the ledger's wash-sale window of section 6 includes day
30 and disallowed part of that loss. This happened four times in the P3 runs,
with $1,275 to $47,171 disallowed. The test is now `days < 31`, which agrees
with the ledger. With monthly rebalancing a 30-day gap is common, so this
defers a number of harvests by a month and restates the P3 column:

| P3 | Before | After |
|---|---|---|
| Equal weight, post-tax Sharpe | 0.4441 | 0.4404 |
| 60/40 VanEck, post-tax Sharpe | 0.3696 | 0.4029 |
| DriftxMinVar, post-tax Sharpe | 0.4041 | 0.4099 |
| Gate 1 margin under P3 | 0.0744 | 0.0375 |

The margin stays far below 0.20. P0 to P2 and everything outside extension 1
are unchanged.

### Corrections of 2026-11-13 (after an independent check of every number)

A full re-check of the reports against the code and the data found one defect
that moves results, one rule the code applied more narrowly than section 6
states, one rounding error in a formula, and two operational faults. No
pre-registered threshold was changed, and no gate, test outcome or verdict
moved.

1. **Late wash-sale reversals were never charged.** Section 6 charges a
   reversal that arrives after its loss year has been settled in the following
   year. The ledger booked it to the loss year, which was already settled, so
   the tax was never paid. It happens when a fund sold at a loss in December
   is bought back at the first rebalance of January, after that day's
   settlement (in the main study, losses of 2018-12-03 reversed on 2019-01-02).
   The ledger now charges such a reversal in the year of the purchase. The four
   baselines under P0 are unaffected. Post-tax Sharpe ratios fall by at most
   0.0013; the largest moves:

   | Post-tax Sharpe | Before | After |
   |---|---|---|
   | DriftxMinVar+Regime | 0.2370 | 0.2364 |
   | DriftxMaxSharpe+Regime | 0.2247 | 0.2241 |
   | DriftxMinVar (no overlay) | 0.3738 | 0.3727 |
   | DriftxMaxSharpe (no overlay) | 0.3745 | 0.3732 |
   | MomentumxMinVar (no overlay) | 0.2675 | 0.2665 |
   | MomentumxMaxSharpe (no overlay) | 0.2631 | 0.2620 |
   | Gate 1 margin under P1 | 0.0719 | 0.0718 |

   In extension 2 the best rule's observed edge (DriftxMaxSharpe, +1.43% a
   year) would now take 65 years to detect instead of 64, and the reality-check
   p over all twelve rules is 0.36 instead of 0.35. The frozen results were
   refrozen; 42 values moved.
2. **Same-day purchases.** The ledger did not treat a purchase made on the day
   of a loss sale as a wash sale, on either side, although section 6 counts 30
   days "before or after". It now does. No number moves: the planner makes one
   net trade per fund per day, and no wash-sale group is configured, so a
   same-day repurchase never occurs in the study.
3. **Newey-West lag.** floor(0.75 n^(1/3)) was computed with a floating-point
   power, which gives one lag too few when n is a perfect cube (64, 512,
   1728). It is now computed with an exact cube root. At the study's n = 90 the
   lag is 3 either way; no number moves.
4. **Operations.** The continuous-integration health step judged the frozen
   data cache against the wall clock, so it would have failed every run from
   2026-11-17; it now judges against the cache's last bar. The scheduled
   monthly job used the committed 2024 paper book, which a decision rolls
   forward to the signal date, so a run would have marked that book through
   the sealed hold-out, and its monitor step would have computed returns inside
   it. The job now decides on a fresh book, as of the last trading day of the
   previous month, and monitors only once the hold-out has been opened. The
   job had not yet run.

## 21. Known approximations

Each of these was measured on 2026-11-13 and is too small to change any result.
They are recorded so that a reader can reproduce the numbers exactly.

* **Risk-free timing.** Cash grows on day t at the previous day's T-bill yield,
  while the Sharpe ratio subtracts day t's own yield. Aligning the two moves
  equal weight's post-tax Sharpe from 0.3686 to 0.3688 and leaves the Gate 1
  margin at 0.0706.
* **Negative T-bill yields** are floored at zero for the cash sleeve and the
  risk-free rate: 7 closes, 2020-03-19 to 2020-03-27, as low as -0.105%.
* **Wash sales are judged on each sale's net result**, spreading one average
  loss per unit over the lots sold, not lot by lot. Applied lot by lot, equal
  weight's post-tax Sharpe would be 0.36824 instead of 0.36864 and the Gate 1
  margin 0.07065 instead of 0.07057.
* **The reality-check p** of section 15 is the studentised stepdown p of the
  best strategy, not White's original unstudentised statistic.
* **Romano-Wolf statistics** are studentised with the plain standard error
  (sd / sqrt(n)) in the sample and in every resample, not with a
  dependence-robust one. The best rule's stepdown t is 1.03 against a
  Newey-West t of 1.14; no rejection is near either way.
* **The bootstrap p of section 10** is the share of resampled Sharpe
  differences at or below zero, which inverts a percentile interval; it is
  not computed under an imposed null. A null-centred p on the same draws
  differs by at most about 0.03 (measured before the 2026-11-13 correction).
* **The deflated Sharpe ratio** uses the raw count of strategies (6 or 12) as
  the number of trials. The rules are closely related, so the effective number
  of independent trials is smaller and the deflation errs on the conservative
  side.
* **The monthly minimum detectable effect** of section 15 uses the plain
  standard deviation of the monthly differences. With the Newey-West error of
  the paired test it would be about 3.7% a year instead of 4% (Bonferroni).
* **The regime tables** (Phase 2, Gate 3 and the dashboard) classify each day
  by that day's close, while the overlay acts on the regime of the signal date.
  The tables describe what happened in each regime; they are not what the
  overlay saw. `reports/FINDINGS.md` section 5 shows how much this matters.
