# Why allocation rules fail on VanEck ETFs: findings

> Revision 2026-11-08: the wash-sale accounting was corrected (shares sold, or
> already sold, no longer count as their own replacement). Gate 1 numbers are
> unchanged; Phase 2 post-tax Sharpe ratios moved by at most 0.01. Tables below
> are the corrected values.

Study window: 9 VanEck funds, out-of-sample 2017-06-01 to 2024-12-31 (91
monthly rebalances, 7 full calendar years), $10M taxable account, costs tiered
by liquidity, 37% tax with FIFO lots and wash-sale rules, cash at the 13-week
T-bill rate. Every rule was fixed before any result was computed
(`docs/SPEC.md`). Generated tables and figures: `REPORT.md`.

## 1. Bottom line

* **Gate 0 passes.** The nine funds are genuinely diversified: average pairwise
  correlation 0.39, five distinct themes, no redundant pair (highest is
  MOAT/HAP at 0.78). The universe is allocatable.
* **Gate 1 fails.** After tax, monthly-rebalanced equal weight beats the plan's
  60/40 (60% equal-weight VanEck equity funds, 40% BND) by 0.07 Sharpe; the plan
  requires 0.20. Equal weight's one-way turnover is 21% a year against a 15%
  limit. It is also worse than a plain SPY/BND 60/40 after tax.
* **Phase 2, run informationally, confirms the stop.** None of the six locked
  strategies beats 60/40 at the Bonferroni threshold; all six have *negative*
  mean annual excess return against it after tax. The regime overlay the plan
  prescribes is the single largest destroyer of value.
* **Recommendation under the plan's own decision rule: stop, or pivot.** The
  levers that could change the answer are structural (tax-aware execution,
  position limits, universe), not better signals.

## 2. Gate 0: the universe

| Check | Value | Threshold | Result |
|---|---|---|---|
| Average pairwise correlation | 0.39 | < 0.75 | pass |
| Distinct themes (clusters at rho >= 0.60) | 5 | >= 5 | pass |
| Highest pairwise correlation | 0.78 (MOAT/HAP) | <= 0.95 | pass |

Themes: gold (GDX, OUNZ), credit/income (ANGL, PFXF), equity beta (SMH, MOAT,
HAP), rare earths (REMX), floating rate (FLTR). The 60-day rolling average
correlation never exceeded 0.67 and averaged 0.32. Correlation is not the
problem.

## 3. Gate 1: baselines

| Portfolio | CAGR pre-tax | CAGR post-tax | Sharpe pre-tax | Sharpe post-tax | Max drawdown | Turnover (one-way) | Costs | Taxes |
|---|---|---|---|---|---|---|---|---|
| Equal weight (1/9, monthly) | 9.9% | 6.9% | 0.55 | 0.37 | -28.6% | 20.9%/yr | 3.9 bps/yr | 2.5% of NAV/yr |
| 60/40 VanEck (plan definition) | 8.4% | 5.6% | 0.49 | 0.30 | -25.3% | 22.2%/yr | 2.9 bps/yr | 2.5% of NAV/yr |
| 60/40 SPY/BND | 9.2% | 7.4% | 0.62 | 0.48 | -22.1% | 10.7%/yr | 0.6 bps/yr | 1.3% of NAV/yr |
| Buy & hold SMH | 26.9% | 26.8% | 0.82 | 0.81 | -45.7% | 0 | 0.1 bps/yr | 0 |

| Gate 1 criterion | Value | Threshold | Result |
|---|---|---|---|
| EW post-tax Sharpe minus 60/40 VanEck post-tax Sharpe | +0.07 | >= +0.20 | **fail** |
| EW post-tax Sharpe minus 60/40 SPY/BND (reference) | -0.11 | >= +0.20 | fail |
| EW max drawdown | -28.6% | better than -40% | pass |
| EW one-way turnover | 20.9%/yr | < 15%/yr | **fail** |
| EW post-tax Sharpe | 0.37 | >= 0.30 | pass |
| Tax model changes the answer (Sharpe haircut) | -33% | > 20% | pass (model matters) |

What the table says:

* **Taxes are the friction that matters; trading costs are not.** Costs are
  single-digit basis points for every baseline. Tax takes 2.5% of NAV a year
  from equal weight: roughly 0.8 points from taxed distributions (the three
  income funds and REMX/GDX pay non-qualified income) and 1.7 points from gains
  realised by monthly rebalancing under FIFO lot relief. The plan's intuition
  that "costs will dominate" above 15% turnover is correct only if "costs"
  means taxes.
* **The turnover criterion is violated by construction.** Nine funds with 15%
  to 35% volatility and modest correlations drift about 1.7% (one-way) from
  target every month; monthly rebalancing therefore turns over about 21% a year
  before any signal is applied. Quarterly rebalancing cuts this to 12% and
  *raises* the post-tax Sharpe to 0.41 (sensitivity table in `REPORT.md`), but
  60/40 benefits similarly, so the margin over 60/40 does not change.
* **Equal weight loses to a passive SPY/BND portfolio after tax** despite a
  higher pre-tax return, because it carries about 30% more volatility and twice
  the tax drag. The "diversified VanEck basket" is a higher-vol, higher-tax
  portfolio of thematic beta, not a better one.
* **One fund dominated the sample.** SMH compounded at 27% a year. Any portfolio
  rule capped at 20% in a single fund, and floored at 5% in the other eight,
  could not have kept up with it, and equal weight only holds 11%. This is a
  2017-2024 semiconductor-cycle artefact, not a property of the rules, but it
  is the backdrop against which every result below should be read.

## 4. Phase 2 (informational): the six locked strategies

Primary set = {None, Momentum, Drift} x {MinVar, MaxSharpe} with the plan's
regime overlay. Pre-registered test: paired one-sided t-test of post-tax
calendar-year returns against 60/40 VanEck, Bonferroni alpha 0.0083.

| Strategy (with regime overlay) | CAGR post-tax | Sharpe post-tax | Max drawdown | Turnover | Mean annual excess vs 60/40 | p (annual) | p (monthly) |
|---|---|---|---|---|---|---|---|
| None x MinVar | 3.7% | 0.21 | -19.2% | 144%/yr | -1.8% | 0.69 | 0.77 |
| None x MaxSharpe | 2.8% | 0.10 | -23.1% | 267%/yr | -3.3% | 0.89 | 0.81 |
| Momentum x MinVar | 2.9% | 0.11 | -23.2% | 268%/yr | -3.3% | 0.89 | 0.80 |
| Momentum x MaxSharpe | 2.7% | 0.10 | -23.5% | 278%/yr | -3.4% | 0.90 | 0.81 |
| Drift x MinVar | 4.3% | 0.24 | -21.3% | 199%/yr | -1.4% | 0.71 | 0.69 |
| Drift x MaxSharpe | 4.2% | 0.23 | -22.2% | 204%/yr | -1.6% | 0.73 | 0.70 |

Gate 2: **no winner**. Every strategy is below the 60/40 benchmark and far below
equal weight after tax; the pre-tax picture is the same (p >= 0.75 for all six).

### The same rules without the regime overlay (diagnostics, not tests)

| Strategy (no overlay) | CAGR post-tax | Sharpe post-tax | Max drawdown | Turnover | Mean annual excess vs 60/40 | p (annual) | p (monthly) |
|---|---|---|---|---|---|---|---|
| None x MinVar | 5.2% | 0.32 | -25.7% | 16%/yr | 0.0% | 0.50 | 0.67 |
| None x MaxSharpe | 4.6% | 0.23 | -25.2% | 214%/yr | -1.2% | 0.77 | 0.69 |
| Momentum x MinVar | 5.2% | 0.27 | -27.5% | 222%/yr | -0.6% | 0.67 | 0.55 |
| Momentum x MaxSharpe | 5.2% | 0.26 | -27.7% | 233%/yr | -0.6% | 0.69 | 0.56 |
| Drift x MinVar | 6.8% | 0.37 | -27.3% | 107%/yr | +1.4% | 0.19 | 0.16 |
| Drift x MaxSharpe | 6.8% | 0.37 | -27.2% | 114%/yr | +1.4% | 0.16 | 0.15 |

Removing the overlay improves every strategy by 0.1 to 0.2 Sharpe. The two
Drift rules are the only ones with a positive excess return over 60/40, and
they merely tie equal weight after tax (Sharpe 0.37 vs 0.37) while trading five
times as much. Nothing here is close to significance.

## 5. Regimes (Phase 2.4)

Share of out-of-sample days: Bull 34%, Neutral 26%, Correction 22%, Crisis 18%,
Stagflation 0%. Under the locked definition (S&P GSCI up more than 20% year on
year *and* 10-year yield above 4%) Stagflation last occurred in 2005-2008 and
only re-appears in 2026; the 2022 inflation shock never satisfied both
conditions on the same day. The Stagflation tilt was therefore never exercised
and cannot be evaluated.

| Post-tax Sharpe by regime | Bull | Correction | Crisis | Neutral |
|---|---|---|---|---|
| Equal weight | 2.28 | 0.73 | -1.67 | 1.41 |
| 60/40 VanEck | 2.19 | 0.39 | -1.74 | 1.24 |
| None x MinVar + regime | 2.18 | -0.20 | -1.59 | 1.08 |
| Drift x MinVar + regime | 2.58 | -0.25 | -2.16 | 0.84 |
| Momentum x MaxSharpe + regime | 2.25 | -0.23 | -2.33 | 0.53 |

| Max drawdown within regime days | Bull | Correction | Crisis | Neutral |
|---|---|---|---|---|
| Equal weight | -6.2% | -10.6% | -45.8% | -15.5% |
| None x MinVar + regime | -3.6% | -5.9% | -31.3% | -8.3% |
| Drift x MinVar + regime | -5.1% | -9.7% | -38.3% | -12.6% |

The overlay does what it says in Crisis: every strategy's Crisis drawdown is
shallower than equal weight's (by 4 to 15 points), so all six pass the Gate 3
tolerance test. It pays for that everywhere else:

* **Correction (22% of days)** flips from the second-best regime for equal
  weight to a losing one for every overlay strategy, because the rule parks 25%
  in cash during what were, in this decade, mostly mild pullbacks inside a bull
  market.
* **Crisis entry and exit are late by design.** Regimes are read at month-end
  and acted on the next trading day. In 2020 the overlay was in "hedges only"
  from 2 March until 3 August and again from 1 September until 1 December,
  eight of the ten monthly rebalances from March to December: it sold after the
  February-March fall and bought back after much of the recovery, then
  whipsawed once more in the autumn. In 2022 it entered and left the hedge
  basket three times, holding it in seven of twelve months. With a reactive
  VIX/drawdown trigger and monthly execution, the hedge basket (OUNZ, GDX,
  VGIT at 20% each, 40% cash) is held mainly during recoveries.
* **Momentum is the weakest signal.** It trails equal weight in every regime,
  with or without the overlay: 60-day returns on nine heterogeneous funds mostly
  rank noise, and the resulting 2x-per-year turnover is taxed as short-term
  gains. Drift (share of up-days) is a smoother estimator and is the only signal
  with any positive residual.

## 6. Why the plan's statistical gate could not have passed

The pre-registered test uses seven annual observations. For the best
diagnostic rule (Drift x MaxSharpe, +1.4% a year over 60/40) the standard
deviation of the annual excess return is about 3.5%; detecting that edge
one-sided at alpha 0.0083 with 80% power would need more than sixty years of
data. Even the supplementary monthly test (90 observations) gives p of
0.15. The annual t-test can only ever confirm an edge so large that it would not
need a test. If the project continues in any form, the significance design
should move to monthly or daily paired tests with a block bootstrap and to
pre-registered effect sizes, not to more strategies.

## 7. Sensitivities (none flips Gate 1)

| Variant | EW Sharpe post-tax | 60/40 VanEck Sharpe post-tax | Margin |
|---|---|---|---|
| Primary (T-bill cash, FIFO, monthly) | 0.37 | 0.30 | 0.07 |
| Plan-literal flat 4% cash | 0.25 | 0.17 | 0.08 |
| HIFO lot relief | 0.43 | 0.36 | 0.07 |
| No tax on distributions | 0.44 | 0.36 | 0.08 |
| Quarterly rebalancing (EW only) | 0.41 | - | - |

Tax-aware execution (HIFO plus quarterly rebalancing) is worth about 0.1
Sharpe to any of these portfolios, which is more than any allocation rule
delivered, but it lifts the benchmark as much as the candidate.

## 8. Decision and recommendations

Under the plan's rule, Gate 1 failed, so the project stops here. The 2025+
hold-out has deliberately not been touched (`python -m vaneck validate` exists
but was not run). If you choose to continue, the evidence points at three
structural levers rather than at signal research:

1. **Tax-aware execution first.** HIFO lots, quarterly or threshold rebalancing
   and loss harvesting are worth roughly 0.1 Sharpe to every portfolio in this
   study, which is larger than any signal effect found.
2. **Decide what the position limits are for.** Every rule was capped at 20% in
   the one fund that drove the decade's return. That cap is a sensible risk
   control, but with it in place the study is effectively asking whether
   re-weighting eight ex-growth themes can beat a static mix, and the answer was
   no.
3. **Broader universe or different question.** The plan's own fallback (a
   600+ ETF universe) addresses the real limitation: nine thematic funds offer
   five themes, and five themes offer little for a monthly allocation rule to
   exploit. Alternatively, the methodology licensing pivot the plan mentions
   does not depend on these results at all.

## 9. Caveats

* Yahoo Finance data, with its known occasional adjustment errors; the data
  quality checks found none in the study window, but no second source was used.
* Spreads are tiered assumptions calibrated to average dollar volume, not live
  broker quotes; `data/spreads_override.csv` accepts the latter. Market impact
  is not modelled; at $10M the thinnest fund (HAP, about $2M a day) would be
  harder to trade than the model assumes, which would only worsen the rules.
* A single 37% rate on all gains and distributions, FIFO lots, annual
  settlement; state-specific rules and the $3,000 ordinary-income offset are
  ignored.
* The CRB index is proxied by the S&P GSCI, which is more energy-heavy.
* The composition rule "signal pins its favoured funds, optimizer allocates the
  rest" is this implementation's resolution of a step the plan left undefined;
  it was fixed before running and is documented in `docs/SPEC.md`.
* All results are one draw of history dominated by a semiconductor supercycle
  and a zero-rate decade; the regime overlay in particular would look different
  in a sample with slow, grinding bear markets.

## 10. Pre-registered extensions

Three follow-ups with thresholds fixed in `docs/SPEC.md` sections 14 to 16 are
reported in `EXTENSIONS_FINDINGS.md`: tax-aware execution adds about 0.08
post-tax Sharpe to every portfolio without changing the Gate 1 verdict; the
annual Bonferroni test could only have detected a 10% to 14% annual edge; and
capacity under a 50 bps tracking limit is about $3M, bound by HAP's volume.

## 11. Reproduce

```bash
pip install -r requirements.txt
python -m pytest -q
python -m vaneck all --force
```
