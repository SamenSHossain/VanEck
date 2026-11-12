# Pre-registered extensions: findings

Three analyses motivated by the main study's findings, with every threshold
fixed in `docs/SPEC.md` sections 14 to 16 before the first run. Generated tables
and figures: `EXTENSIONS.md`. Window: 2017-06-01 to 2024-12-31, the main study's
out-of-sample period.

## 1. Tax-aware execution: worth 0.04 to 0.10 Sharpe, changes no verdict

| Post-tax Sharpe | P0 calendar, FIFO | P1 TAXOPT lots | P2 + 20% drift band | P3 + loss harvesting |
|---|---|---|---|---|
| Equal weight | 0.37 | 0.41 | 0.44 | 0.44 |
| 60/40 VanEck | 0.30 | 0.34 | 0.36 | 0.40 |
| 60/40 SPY/BND | 0.48 | 0.50 | 0.51 | 0.53 |
| Drift x MinVar (no overlay) | 0.37 | 0.39 | 0.39 | 0.41 |
| **Gate 1 margin (EW minus 60/40 VanEck)** | **0.07** | **0.07** | **0.08** | **0.04** |

| Equal weight under each policy | Tax drag (% NAV/yr) | One-way turnover | Costs (bps/yr) | Tracking error vs P0, pre-tax |
|---|---|---|---|---|
| P0 | 2.54 | 21% | 3.9 | - |
| P1 | 1.85 | 21% | 3.9 | 0.0% |
| P2 | 1.53 | 5.5% | 1.8 | 0.7% |
| P3 | 0.99 | 31% | 4.9 | 1.4% |

What this says:

* **Lot selection alone is worth 0.04 Sharpe** and costs nothing: the same
  trades, relieved from the lots that realise the least gain.
* **Drift bands are the best step.** They cut turnover by three quarters, halve
  trading costs, add another 0.03 Sharpe, and track the calendar portfolio
  within 0.7% a year. This is the policy to run.
* **Harvesting is not free.** It realises 40 loss sales for equal weight over
  the window and lowers tax drag to under 1% of NAV, but the substitutes track
  imperfectly (1.4% tracking error, 0.3 points of pre-tax return given back)
  and turnover rises again. For equal weight it adds nothing after P2; for the
  two 60/40 portfolios it adds 0.02 to 0.04. Dropping the greyest pair
  (OUNZ/IAU) lowers equal weight's result only slightly, 0.44 to 0.43, so the
  pair is not what carries the benefit.
* **Taxing short-term gains at 46% changes nothing material** (P3 still 0.44).
* **The Gate 1 verdict stands.** The margin moves between 0.04 and 0.08 against
  a 0.20 threshold, because every policy helps the benchmark at least as much
  as the candidate. Tax-aware execution is an operational improvement, not evidence
  for the universe.

Operationally, the same planner now drives the paper broker (`--policy P2`),
with lots, wash-sale tracking and realised-gain reporting in the book.

**Corrected on 2026-11-10.** The first version of this section was computed
with a planner that dropped the 31-day no-rebuy window once a fund had been
sold out completely (SPEC section 14, correction note). With the rule applied
as written, 8 of the 64 frozen ladder values moved, all in the P3 column:
60/40 VanEck 0.39 to 0.37, the P3 margin 0.06 to 0.07, equal weight's loss
sales 43 to 40. Separately, equal drift scores are now ranked by a stated rule
(SPEC section 20). That moved the Drift x MinVar rows of sections 1 and 3 and
the Drift figures of section 2 in the third decimal: 67 years became 64, 43
bps became 42. No outcome changed.

**Changed on 2026-11-11.** The harvest test now treats a fund bought exactly
30 days earlier as recently bought, which agrees with the ledger's wash-sale
window (SPEC section 20). This restates the P3 column once more: 60/40 VanEck
0.37 to 0.40, the P3 margin 0.07 to 0.04. P0 to P2 are unchanged.

## 2. Statistical design: the original test could not have passed

| Strategy family | Minimum detectable annual excess return, 80% power | At alpha 0.05 |
|---|---|---|
| Primary six, annual test (n = 7), Bonferroni alpha 0.0083 | 10% to 14% per year | 7% to 9% |
| Diagnostics, annual test | 5% to 9% | 3.5% to 6% |
| Drift rules, monthly Newey-West test (n = 90), Bonferroni | about 4% | about 3% |

The plan's decisive test needed an edge larger than the equity risk premium to
reject. The best rule in the study (Drift x MaxSharpe without overlay, +1.5% a
year over 60/40) would need 64 years of data under the annual test.

| Drift x MaxSharpe (no overlay) | Value |
|---|---|
| Monthly excess over 60/40, Newey-West one-sided p | 0.13 |
| Probabilistic Sharpe of the edge series vs zero | 0.85 |
| Deflated Sharpe (12 trials) | 0.74 |
| Romano-Wolf adjusted p (12-strategy family) | 0.35 |
| White reality-check p, primary family / all 12 | 0.81 / 0.35 |

Reading: the Drift rules are not refuted and not demonstrated. A deflated
Sharpe of 0.74 says there is a 26% chance the observed Sharpe is what the best
of twelve zero-edge trials would show; the stepdown p of 0.35 says the edge over
60/40 is comfortably consistent with selection among twelve candidates. The
primary six remain negative under every test. For future work the proposal in
the SPEC stands: pre-register the minimum detectable effect, test monthly with
HAC standard errors, and control the family with Romano-Wolf rather than
Bonferroni on seven annual points.

## 3. Capacity: small, and bound by one fund

| Portfolio | Friction drag at $10M | Tracking error at $10M | Capacity under the pre-registered limits (drag <= 25 bps, tracking <= 50 bps) | Binding constraint |
|---|---|---|---|---|
| Equal weight | 10 bps/yr | 63 bps/yr | $3M | tracking (participation cap) |
| 60/40 VanEck | 9 bps/yr | 64 bps/yr | $3M | tracking |
| Drift x MinVar | 42 bps/yr | 106 bps/yr | below $1M | drag (turnover) and tracking |
| Momentum x MaxSharpe | 75 bps/yr | 217 bps/yr | below $1M | drag and tracking |

* **The baselines are liquidity-bound, not cost-bound.** Even at $1 billion the
  drag is under 11 bps because the participation cap simply refuses the trades;
  what grows is the tracking error, from 9 bps at $1M to 780 bps at $1B, as HAP
  (about $2M a day) and then PFXF and OUNZ cannot be filled. At the plan's $10M
  the cap already removes more than a tenth of trades.
* **The rules are cost-bound at any size.** One to two times annual turnover
  through 5 to 10 bps spreads costs 35 to 75 bps a year before impact, above
  the 25 bps limit even at $1M. Capacity for these rules is a turnover problem,
  not a size problem.
* **Research and execution now agree.** With the same participation cap applied
  in the research engine, the 2024 paper book differs from research by -0.13%
  cumulative (whole-share rounding and dividends held as cash), against +1.25%
  when research ignored the cap. The monitor reports both lines.

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
* Golden regression test: the refactored engine reproduces the main study to
  six decimals under the legacy policy.

## Reproduce

```bash
python -m vaneck extensions            # ~1 minute; writes reports/EXTENSIONS.md and tables/figures
python -m vaneck replay --strategy EqualWeight --start 2024-01-01 --end 2024-12-31 --policy P2
```
