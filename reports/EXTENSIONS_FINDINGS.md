# Pre-registered extensions: findings

Three analyses motivated by the main study's findings, with every numerical
threshold fixed in `docs/SPEC.md` sections 14 to 16 before the first run (those
sections were committed together with the first results, so the history alone
cannot show the order). Two boundary rules were stated afterwards and are marked
there: the lot-tie tolerance and the day-30 harvest boundary. Generated tables
and figures: `EXTENSIONS.md`. Window: 2017-06-01 to 2024-12-31, the main study's
out-of-sample period.

## 1. Tax-aware execution: worth 0.02 to 0.10 Sharpe, changes no verdict

| Post-tax Sharpe | P0 calendar, FIFO | P1 TAXOPT lots | P2 + 20% drift band | P3 + loss harvesting |
|---|---|---|---|---|
| Equal weight | 0.37 | 0.41 | 0.44 | 0.44 |
| 60/40 VanEck | 0.30 | 0.34 | 0.36 | 0.40 |
| 60/40 SPY/BND | 0.48 | 0.50 | 0.51 | 0.53 |
| Drift x MinVar (no overlay) | 0.37 | 0.39 | 0.39 | 0.41 |
| **Gate 1 margin (EW minus 60/40 VanEck)** | **0.07** | **0.07** | **0.08** | **0.04** |

| Equal weight under each policy | Tax paid in the window (% NAV/yr) | One-way turnover | Costs (bps/yr) | Tracking error vs P0, pre-tax |
|---|---|---|---|---|
| P0 | 2.54 | 21% | 3.9 | - |
| P1 | 1.85 | 21% | 3.9 | 0.0% |
| P2 | 1.53 | 5.5% | 1.8 | 0.7% |
| P3 | 0.99 | 31% | 4.9 | 1.4% |

The tax column counts what was paid inside the window (tax years 2017 to 2023).
The 2024 tax is accrued in the post-tax NAV and the Sharpe ratios already
include it; counting it, the four policies cost 3.00, 2.32, 2.13 and 1.46% of
NAV a year.

What this says:

* **Lot selection alone is worth 0.04 Sharpe** and costs nothing: the same
  trades, relieving loss lots first and then long-term before short-term gain
  lots, smallest gain first within each (SPEC section 14's TAXOPT order).
* **For equal weight, drift bands give the best result.** They cut turnover by
  three quarters, halve trading costs, add another 0.03 Sharpe, and track the
  calendar portfolio within 0.7% a year. This is the policy to run for equal
  weight; for the two 60/40 portfolios and Drift x MinVar, P3 scores higher.
* **Harvesting is not free.** It realises 40 loss sales for equal weight over
  the window and lowers the tax paid inside the window to under 1% of NAV a
  year (1.46% once the 2024 tax is counted), but the substitutes track
  imperfectly (1.4% tracking error; 0.4 points of pre-tax return a year given
  back against P0, 0.3 against P2) and turnover rises again. For equal weight
  it adds nothing after P2; for the
  two 60/40 portfolios it adds 0.02 to 0.04. Dropping the greyest pair
  (OUNZ/IAU) lowers equal weight's result only slightly, 0.44 to 0.43, so the
  pair is not what carries the benefit.
* **Taxing short-term gains at 46% changes nothing material** (P3 still 0.44).
* **The Gate 1 verdict stands.** The margin moves between 0.04 and 0.08 against
  a 0.20 threshold. P1 and P2 lift equal weight slightly more than the benchmark
  (margin 0.07 to 0.08), P3 lifts the benchmark more (margin 0.04), and no policy
  comes near 0.20. Tax-aware execution is an operational improvement, not
  evidence for the universe.

Operationally, the same planner now drives the paper broker (`--policy P2`),
with lots, wash-sale tracking and realised-gain reporting in the book.

**Corrected on 2026-11-10.** The first version of this section was computed
with a planner that dropped the 31-day no-rebuy window once a fund had been
sold out completely (SPEC section 14, correction note). With the rule applied
as written, 8 of the 64 frozen ladder values moved, all in the P3 column:
60/40 VanEck 0.39 to 0.37, the P3 margin 0.06 to 0.07, equal weight's loss
sales 43 to 40. Separately, equal drift scores are now ranked by a stated rule
(SPEC section 20). That moved the Drift x MinVar rows of sections 1 and 3 and
the Drift figures of section 2 by small amounts: 67 years became 64, 43 bps
became 42, and the best rule's edge moved from +1.44% to +1.45% a year. No
outcome changed.

**Changed on 2026-11-11.** The harvest test now treats a fund bought exactly
30 days earlier as recently bought, which agrees with the ledger's wash-sale
window (SPEC section 20). This restates the P3 column once more: 60/40 VanEck
0.37 to 0.40, the P3 margin 0.07 to 0.04. P0 to P2 are unchanged.

**Corrected on 2026-11-13.** Wash-sale reversals that arrive after a tax year
has been settled are now charged in the following year, as SPEC section 6
states; before, they were never charged. Post-tax figures moved by at most
0.0013 Sharpe: the P1 margin 0.0719 to 0.0718, the best rule's edge +1.45% to
+1.43% a year, the years needed 64 to 65, the reality-check p for all twelve
rules 0.35 to 0.36. No outcome changed.

## 2. Statistical design: the original test could not have passed

| Strategy family | Minimum detectable annual excess return, 80% power | At alpha 0.05 |
|---|---|---|
| Primary six, annual test (n = 7), Bonferroni alpha 0.0083 | 10% to 14% per year | 7% to 9% |
| Diagnostics, annual test | 5% to 9% | 3.5% to 6% |
| Drift rules without the overlay, monthly paired test (n = 90, plain standard deviation), Bonferroni | about 4% | about 3% |

The plan's decisive test needed an edge of 10% to 14% a year to reject, about as
large as the whole premium SPY earned over cash in this window (about 12% a
year). The best rule in the study (Drift x MaxSharpe without overlay, +1.4% a
year over 60/40) would need 65 years of data under the annual test.

| Drift x MaxSharpe (no overlay) | Value |
|---|---|
| Monthly excess over 60/40, Newey-West one-sided p | 0.13 |
| Probabilistic Sharpe of the edge series vs zero | 0.85 |
| Deflated Sharpe (12 trials) | 0.74 |
| Romano-Wolf adjusted p (12-strategy family) | 0.36 |
| Reality-check p, primary family / all 12 | 0.81 / 0.36 |

The reality-check p is the studentised version SPEC section 15 defines: the
best strategy's stepdown-adjusted p. White's original, unstudentised statistic
on the same draws gives larger values.

Reading: the Drift rules are not refuted and not demonstrated. A deflated
Sharpe of 0.74 says that, allowing for twelve trials, there is about a 26%
chance the rule's Sharpe ratio over cash is no better than the best of twelve
zero-skill trials would show; it does not test the edge over 60/40. The
stepdown p of 0.36 says the edge over 60/40 is comfortably consistent with
selection among twelve candidates. The primary six remain negative under every
test. For future work the SPEC (section 15) proposes that a monthly HAC test
with Romano-Wolf control replace the annual Bonferroni test on seven annual
points; FINDINGS.md section 6 adds pre-registering the effect size.

## 3. Capacity: small, and set by building the position

| Portfolio | Friction drag at $10M | Tracking error at $10M | Capacity under the pre-registered limits (drag <= 25 bps, tracking <= 50 bps) | Binding constraint |
|---|---|---|---|---|
| Equal weight | 10 bps/yr | 63 bps/yr | $3M | tracking (participation cap) |
| 60/40 VanEck | 9 bps/yr | 64 bps/yr | $3M | tracking |
| Drift x MinVar | 42 bps/yr | 106 bps/yr | below $1M | drag (turnover and impact); tracking also fails from $3M |
| Momentum x MaxSharpe | 75 bps/yr | 217 bps/yr | below $1M | drag and tracking |

* **The baselines are liquidity-bound, not cost-bound.** Even at $1 billion the
  drag is under 11 bps because the participation cap simply refuses the trades;
  what grows is the tracking error, from 9 bps at $1M to 780 bps at $1B for
  equal weight (4 to 640 bps for 60/40 VanEck), as HAP (about $0.5M a day over
  2017-2024) and then PFXF and OUNZ cannot be filled. At the plan's $10M the cap
  already trims more than a tenth of trades (10.6% for equal weight, 11.5% for
  60/40 VanEck).
* **Most of that tracking error comes from building the position.** The
  measure covers the whole window from an all-cash start. Excluding the first
  12 months, equal weight's tracking error at $10M is 7.4 bps a year: once
  built, the book follows its targets closely, and the capacity limit is really
  the time it takes to fund a new account under a 10% volume cap.
* **The rules are cost-bound at any size.** One to two times annual turnover
  costs 15 to 32 bps a year in spreads and slippage at $1M, and square-root
  impact in the thin funds lifts that to 36 to 74 bps, above the 25 bps limit
  even at $1M. Drift x MinVar would pass on spreads alone; Momentum x MaxSharpe
  would not. Capacity for these rules is a turnover problem, not a size problem.
* **Research and execution now agree.** With the same participation cap applied
  in the research engine, the 2024 paper book differs from research by -0.13%
  cumulative (not decomposed; likely whole-share rounding, dividends held as
  cash, the 10 bps cost reserve and the $500 minimum order), against +1.25% when
  research ignored the cap. The monitor reports both lines.

## What changed in the package

* `vaneck/execution.py`: one planner (lot selection, drift bands, cash
  deployment, harvesting with substitutes, no-rebuy window, participation cap)
  used by the research simulator and the paper broker.
* `vaneck/tax.py`: TAXOPT lot relief, explicit lot plans, short- and long-term
  tracking, last-buy and last-loss-sale dates, serialisation for the paper book.
* `vaneck/costs.py`: square-root impact on trailing volatility.
* `vaneck/stats.py`: minimum detectable effect and required n, Newey-West paired
  test, probabilistic and deflated Sharpe, Romano-Wolf stepdown.
* `vaneck/taxaware.py`, `vaneck/design.py`, `vaneck/liquidity.py`,
  `vaneck/extensions.py`: the three experiments and the report.
* Golden regression test: 248 key values (the Gate 1 baselines, the 12
  strategies, the policy ladder and the capacity curve) are checked against
  frozen copies at a relative tolerance of 1e-6. The refactored engine
  reproduced the original Gate 1 baselines and no-overlay strategies exactly;
  the regime-overlay strategies changed with the refactor and later fixes and
  were refrozen each time (SPEC section 20).

## Reproduce

```bash
python -m vaneck extensions            # ~1 minute; writes reports/EXTENSIONS.md and tables/figures
# the 2024 paper book of section 3 (legacy policy), written to a scratch folder so the committed book is untouched
python -m vaneck replay --strategy EqualWeight --start 2024-01-01 --end 2024-12-31 \
  --book /tmp/replay/book.json --decisions /tmp/replay/decisions --out /tmp/replay
# add --policy P2 to rehearse the drift-band policy instead (different figures)
```
