# Why allocation rules fail on VanEck ETFs: findings

> Revision 2026-11-08: the wash-sale accounting was corrected (shares sold, or
> already sold, no longer count as their own replacement). Gate 1 numbers are
> unchanged; Phase 2 post-tax Sharpe ratios moved by at most 0.01.
>
> Revision 2026-11-13: the figures in this document were checked again against
> the code, the data and the regenerated tables. The check reran the study's own
> code and was not an outside review. Corrections to the engine (`docs/SPEC.md` section 20) moved
> some Phase 2 figures in the second or third decimal; no gate, test outcome or
> verdict changed. Several statements were corrected or qualified. Every figure
> below is current.

Study window: 9 VanEck funds, out-of-sample 2017-06-01 to 2024-12-31 (91
monthly rebalances, 7 full calendar years), $10M taxable account, costs tiered
by liquidity, 37% tax with FIFO lots and wash-sale rules, cash at the 13-week
T-bill rate. The rules, thresholds and tests are set out in `docs/SPEC.md`. By
its own account they were fixed before the results they govern, and its opening
paragraphs say which rules and text were added or changed afterwards. That file
was committed together with the first results, in commit 7e4ff71, so the
repository history cannot confirm that the rules came first. The operational,
extension and retirement rules of sections 13 to 17 were added after the main
results, and the wash-sale correction of 2026-11-08 noted above is recorded
here rather than in the SPEC.
Generated tables and figures: `REPORT.md`.

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
* **Recommendation under the plan's own decision rule: stop, or pivot.**
  Tax-aware execution, tested afterwards (section 10), lifts every portfolio but
  leaves the Gate 1 answer unchanged. The levers left untested are structural
  (position limits, the universe), not better signals.

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

| Portfolio | CAGR pre-tax | CAGR post-tax | Sharpe pre-tax | Sharpe post-tax | Max drawdown | Turnover (one-way) | Costs | Taxes paid |
|---|---|---|---|---|---|---|---|---|
| Equal weight (1/9, monthly) | 9.9% | 6.9% | 0.55 | 0.37 | -28.6% | 20.9%/yr | 3.9 bps/yr | 2.5% of NAV/yr |
| 60/40 VanEck (plan definition) | 8.4% | 5.6% | 0.49 | 0.30 | -25.3% | 22.2%/yr | 2.9 bps/yr | 2.5% of NAV/yr |
| 60/40 SPY/BND | 9.2% | 7.4% | 0.62 | 0.48 | -22.1% | 10.7%/yr | 0.6 bps/yr | 1.3% of NAV/yr |
| Buy & hold SMH | 26.9% | 26.8% | 0.82 | 0.81 | -45.7% | 0 | 0.1 bps/yr | 0 (see note) |

Taxes are paid at the first rebalance of the following year, so the last column
covers tax years 2017 to 2023. The 2024 liability is already deducted from the
post-tax NAV; counting it, taxes are 3.0% of NAV a year for equal weight, 2.8%
for 60/40 VanEck, 1.7% for 60/40 SPY/BND and 0.3% for buy-and-hold SMH, which
never rebalances and so never pays.

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
  single-digit basis points for every baseline. Counting the 2024 liability,
  tax takes about 3.0% of NAV a year from equal weight: roughly 1.0 point from
  distributions, all taxed at 37% and mostly paid by the three income funds,
  and 2.0 points from gains realised by monthly rebalancing under FIFO lot
  relief. The plan's intuition
  that "costs will dominate" above 15% turnover is correct only if "costs"
  means taxes.
* **The turnover criterion is violated by construction.** Nine funds with 6%
  to 37% annual volatility and modest correlations drift about 1.7% (one-way)
  from target every month; monthly rebalancing therefore turns over about 21% a
  year before any signal is applied. Quarterly rebalancing cuts this to 12% and
  *raises* equal weight's post-tax Sharpe to 0.41 (sensitivity table in
  `REPORT.md`), but it lifts 60/40 VanEck about as much (+0.045 against +0.043,
  computed for this check and not in that table), so the margin barely moves.
* **Equal weight loses to a passive SPY/BND portfolio after tax** despite a
  higher pre-tax return, because it carries about 30% more volatility and nearly
  twice the tax drag. The "diversified VanEck basket" is a higher-vol, higher-tax
  portfolio of thematic beta, not a better one.
* **One fund dominated the sample.** SMH compounded at 27% a year. Any portfolio
  rule capped at 20% in a single fund, and floored at 5% in the other eight,
  could not have kept up with it, and equal weight only holds 11%. This is most
  likely a 2017-2024 semiconductor-cycle effect rather than a property of the
  rules (nothing here tests that), but it is the backdrop against which every
  result below should be read.

## 4. Phase 2 (informational): the six locked strategies

Primary set = {None, Momentum, Drift} x {MinVar, MaxSharpe} with the plan's
regime overlay. Pre-registered test: paired one-sided t-test of post-tax
calendar-year returns against 60/40 VanEck, Bonferroni alpha 0.0083.

| Strategy (with regime overlay) | CAGR post-tax | Sharpe post-tax | Max drawdown | Turnover | Mean annual excess vs 60/40 | p (annual) | p (monthly) |
|---|---|---|---|---|---|---|---|
| None x MinVar | 3.7% | 0.21 | -19.2% | 144%/yr | -1.8% | 0.69 | 0.77 |
| None x MaxSharpe | 2.8% | 0.10 | -23.1% | 267%/yr | -3.3% | 0.89 | 0.81 |
| Momentum x MinVar | 2.9% | 0.11 | -23.2% | 268%/yr | -3.3% | 0.89 | 0.79 |
| Momentum x MaxSharpe | 2.7% | 0.10 | -23.5% | 278%/yr | -3.4% | 0.90 | 0.81 |
| Drift x MinVar | 4.3% | 0.24 | -21.2% | 199%/yr | -1.5% | 0.71 | 0.69 |
| Drift x MaxSharpe | 4.2% | 0.22 | -22.3% | 204%/yr | -1.6% | 0.73 | 0.70 |

Gate 2: **no winner**. Every strategy is below the 60/40 benchmark and far below
equal weight after tax; the pre-tax picture is the same (p >= 0.75 for all six).

### The same rules without the regime overlay (diagnostics, not tests)

| Strategy (no overlay) | CAGR post-tax | Sharpe post-tax | Max drawdown | Turnover | Mean annual excess vs 60/40 | p (annual) | p (monthly) |
|---|---|---|---|---|---|---|---|
| None x MinVar | 5.2% | 0.32 | -25.7% | 16%/yr | 0.0% | 0.50 | 0.67 |
| None x MaxSharpe | 4.6% | 0.23 | -25.2% | 214%/yr | -1.2% | 0.77 | 0.69 |
| Momentum x MinVar | 5.2% | 0.27 | -27.4% | 222%/yr | -0.6% | 0.67 | 0.55 |
| Momentum x MaxSharpe | 5.2% | 0.26 | -27.7% | 233%/yr | -0.7% | 0.69 | 0.56 |
| Drift x MinVar | 6.8% | 0.37 | -27.3% | 106%/yr | +1.4% | 0.19 | 0.16 |
| Drift x MaxSharpe | 6.8% | 0.37 | -27.2% | 113%/yr | +1.4% | 0.16 | 0.15 |

Removing the overlay improves every strategy by 0.11 to 0.16 Sharpe. The two
Drift rules are the only ones with a positive excess return over 60/40, and
they merely tie equal weight after tax (Sharpe 0.37 vs 0.37) while trading five
times as much. Nothing here is close to significance.

Restated on 2026-11-10: equal drift scores are now ranked by a stated rule
instead of by the sort a machine happens to use (SPEC section 20). The favoured
funds changed in 2 of 91 rebalances, which moved the four Drift rows in the
third decimal of Sharpe. Restated again on 2026-11-13: wash-sale reversals that
arrive after a tax year has been settled are now charged, as SPEC section 6
states; that moved post-tax figures of the rules by at most 0.0013 Sharpe. No
outcome changed either time.

## 5. Regimes (Phase 2.4)

Share of out-of-sample days: Bull 34%, Neutral 26%, Correction 22%, Crisis 18%,
Stagflation 0%. Under the locked definition (S&P GSCI up more than 20% year on
year *and* 10-year yield above 4%) Stagflation last occurred in 2005-2008 and
does not re-appear through 2024; the 2022 inflation shock never satisfied both
conditions on the same day. The Stagflation tilt was therefore never exercised
and cannot be evaluated.

How to read the two tables below. Each day is assigned the regime read at that
day's own close, so they describe conditions, not regimes anyone could have
traded on: a large fall pushes VIX up and the S&P 500 below its peak on the same
day, and that day counts as Crisis. With the regime known at the previous close
instead, equal weight earns +19% a year in Crisis (Sharpe 0.75) and +2% in
Bull. The strategies themselves only ever use the regime known at the signal
date. Comparisons between portfolios over the same days are unaffected. The
drawdowns chain each regime's days together across separate episodes; they
are not drawdowns any portfolio suffered (equal weight's worst actual
drawdown is -28.6%, its worst single Crisis episode -27.0%).

| Post-tax Sharpe by regime | Bull | Correction | Crisis | Neutral |
|---|---|---|---|---|
| Equal weight | 2.28 | 0.72 | -1.67 | 1.41 |
| 60/40 VanEck | 2.19 | 0.39 | -1.74 | 1.24 |
| None x MinVar + regime | 2.18 | -0.20 | -1.59 | 1.08 |
| Drift x MinVar + regime | 2.58 | -0.25 | -2.15 | 0.83 |
| Momentum x MaxSharpe + regime | 2.25 | -0.23 | -2.33 | 0.53 |

| Max drawdown within regime days | Bull | Correction | Crisis | Neutral |
|---|---|---|---|---|
| Equal weight | -6.2% | -10.6% | -45.8% | -15.5% |
| None x MinVar + regime | -3.6% | -5.9% | -31.3% | -8.3% |
| Drift x MinVar + regime | -5.1% | -9.7% | -38.2% | -12.6% |

In Crisis every overlay strategy's drawdown is shallower than equal weight's
(by 4 to 15 points), so all six would pass the Gate 3 tolerance test. Part of
that comes from the optimizers rather than the overlay: against the same rules
without it, the overlay saves 2 to 7 points of Crisis drawdown, and it lowers
every rule's Sharpe in Crisis, Correction and Neutral periods. In Bull periods
it leaves Sharpe about unchanged, from 0.005 lower to 0.07 higher, but every
rule earns 0.8 to 1.3 points a year less: on about one Bull day in five the
overlay still had 25% in cash after a Correction reading at the previous
month-end. Two regimes need a closer look:

* **Correction (22% of days)** is a profitable regime for equal weight (Sharpe
  0.72, third of four), but every overlay strategy earns less than cash there,
  and five of the six lose money. The 25% cash rule is not the cause: on the 226
  Correction days when it was in force, every rule lost money with or without
  it (2.6% to 6.4% a year with it, 5.3% to 9.3% without), so it reduced the
  losses. The difference comes from 80 Correction days on which the overlay
  still held the Crisis hedge basket, chosen at a month-end Crisis reading,
  while the funds rallied: 20 days in 2019, 12 in 2020, 36 in 2021 and 12 in
  2022. Over those 80 days equal weight gained 29%, the rules without the
  overlay 18% to 30% and the overlay versions 2% to 5%. On the other 347
  Correction days every overlay strategy lost less than equal weight and less
  than the same rule without the overlay.
* **Crisis entry and exit are late by design.** Regimes are read at month-end
  and acted on the next trading day. In 2020 the overlay was in "hedges only"
  from 2 March until 3 August and again from 1 September until 1 December,
  eight of the ten monthly rebalances from March to December. It moved into the
  hedge basket on 2 March, after the first leg of the fall (S&P 500 down 9% from
  its 19 February peak) but before most of it (down 34% at the 23 March low),
  and bought back on 3 August, after about 90% of the fall had been recovered,
  then whipsawed once more in the autumn. In 2022 it entered and left the hedge
  basket three times, holding it in seven of twelve months. With a reactive
  VIX/drawdown trigger and monthly execution, the hedge basket (OUNZ, GDX,
  VGIT at 20% each, 40% cash) is held mainly during recoveries.
* **Momentum is the weakest signal.** It trails equal weight in every regime,
  with or without the overlay: 60-day returns on nine heterogeneous funds appear
  to rank mostly noise, and the resulting turnover of more than twice a year
  realises gains every year instead of deferring them (the study taxes short-
  and long-term gains alike at 37%). Drift (share of up-days) is a smoother
  estimator and is the only signal
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

HIFO lot relief alone adds about 0.06 post-tax Sharpe to both portfolios, and
quarterly rebalancing 0.04 to equal weight: about as much as the best rule's
edge over 60/40 VanEck (Drift x MaxSharpe without the overlay, +0.08) and more
than any rule added over equal weight. It lifts the benchmark as much as the
candidate, so the margin stays at 0.07.

## 8. Decision and recommendations

Under the plan's rule, Gate 1 failed, so the project stops here. The 2025+
hold-out has deliberately not been touched. It is sealed: `python -m vaneck
validate` only reports the seal, and opening it is a single logged decision
under SPEC section 19. If you choose to continue, the evidence points at three
structural levers rather than at signal research:

1. **Tax-aware execution first.** Tax-aware lot selection, drift-band
   rebalancing and loss harvesting add 0.02 to 0.10 post-tax Sharpe to the four
   portfolios tested (about 0.07 for equal weight), which is more than any
   allocation rule added over equal weight.
2. **Decide what the position limits are for.** Every rule was capped at 20% in
   the one fund that drove the window's return. That cap is a sensible risk
   control, but with it in place the study is effectively asking whether
   re-weighting nine funds, none set above 20%, can beat a static mix, and the
   answer was no. Only three rules ever set SMH at the cap: the two Momentum
   rules, each at 33 of the 91 rebalances with the overlay and 52 without, and
   None x MaxSharpe, at 19 and 27.
3. **Broader universe or different question.** The plan's own fallback (a
   600+ ETF universe) addresses the real limitation: nine funds offer
   five themes, and five themes offer little for a monthly allocation rule to
   exploit. Alternatively, the methodology licensing pivot the plan mentions
   does not depend on these results at all.

## 9. Caveats

* Yahoo Finance data, which can contain adjustment errors; the data quality
  checks found none in the study window. FRED cross-checks four of the five
  macro inputs, all but the S&P GSCI, over the last 750 trading days, and each
  fund's daily returns are compared with a peer ETF's over the last 250; on the
  cache, which ends in November 2026, the FRED check reaches back only to late
  2023 and the peer check starts after the study window. A corporate-actions
  audit checks Yahoo's adjustment factors against Yahoo's own distribution
  records over the whole history of each of the nine funds and found no
  mismatch, but it compares Yahoo with itself; no independent second price feed
  covers the nine funds in the study window.
* Spreads are tiered assumptions calibrated to average dollar volume, not live
  broker quotes; `data/spreads_override.csv` accepts the latter. Market impact
  is not modelled in the main study (extension 3 models it); at $10M the
  thinnest fund (HAP, about $0.6M a day on average over the window) would be
  harder to trade than the model assumes, which would only worsen the rules.
* A single 37% rate on all gains and distributions, FIFO lots, annual
  settlement; state-specific rules and the $3,000 ordinary-income offset are
  ignored.
* The CRB index is proxied by the S&P GSCI, which is production-weighted and so
  more energy-heavy.
* The composition rule "signal pins its favoured funds, optimizer allocates the
  rest" is this implementation's resolution of a step the plan left undefined.
  It appears in section 8 of `docs/SPEC.md` as first committed in 7e4ff71, the
  same commit as the first results, and its tie rule was added on 2026-11-10,
  after the results were known.
* All results are one draw of history, dominated by a semiconductor supercycle
  and by T-bill rates that went from near zero (2020-2021) to 5% (2023-2024),
  averaging 2.2%; the regime overlay in particular would look different in a
  sample with more slow, grinding bear markets like 2022's, in which it switched
  in and out of the hedge basket three times.

## 10. Pre-registered extensions

Three follow-ups with thresholds fixed in `docs/SPEC.md` sections 14 to 16 are
reported in `EXTENSIONS_FINDINGS.md`: tax-aware execution adds 0.02 to 0.10
post-tax Sharpe without changing the Gate 1 verdict (margin 0.04 to 0.08 against
0.20); the annual Bonferroni test could only have detected a 10% to 14% annual
edge; and under the pre-registered limits capacity is $3M for equal weight and
60/40 VanEck (the largest size tested that passes; $10M fails). That limit is
set by building a new position from cash under a 10% volume cap: once the book
is built, tracking error at $10M is under 10 bps a year.

## 11. Reproduce

```bash
pip install -r requirements.txt
python -m pytest -q
python -m vaneck all --force       # writes REPORT.md and the main study's tables and figures
python -m vaneck extensions        # writes EXTENSIONS.md and the extension tables and figures
```

This file and `EXTENSIONS_FINDINGS.md` are written by hand. The first command
supplies most figures in sections 1 to 7. The second supplies most of section
10, the years of data needed in section 6 and the tax-aware gains in section 8.
Some figures were computed separately for the 2026-11-13 check and appear in
neither output, among them the taxes that count the 2024 liability, the
quarterly lift for 60/40 VanEck, equal weight's regime returns with the regime
read at the previous close, HAP's average daily dollar volume, the average
T-bill rate and the tracking error once the book is built.
