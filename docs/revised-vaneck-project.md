# REVISED PROJECT: VanEck ETF Allocation Strategy

## Council-Informed Implementation Plan

## EXECUTIVE SUMMARY

**Original Problem:** Can simple allocation rules beat equal weight and 60/40 baselines out of sample, after trading costs?

**Revised Framing:** Two sequential gates, not one conflated question:

- **Gate 1 (Feasibility):** Are these 9 VanEck funds allocatable? Do they have decorrelated drift, exploitable correlations, and tradeable liquidity?
- **Gate 2 (Edge):** If Gate 1 passes, do allocation rules add value over static factor exposure?

**Process Reset:** Build infrastructure first, then test. Fail fast on feasibility before investing in optimization.

---

## PHASE 1: FEASIBILITY STUDY (Weeks 1–3)

### 1.1 – Define Universe & Validate Correlations

**Objective:** Confirm the 9-fund universe is allocatable (not just correlated noise).

**Tasks:**

- [ ] Pull 5-year daily prices for: SMH, MOAT, GDX, ANGL, REMX, HAP, FLTR, PFXF, OUNZ
- [ ] Source: yfinance (free, validated); fallback to Morningstar API if needed
- [ ] Adjust for splits, distributions (reinvested daily)
- [ ] Calculate 60-day rolling correlations between all pairs
- [ ] Identify correlation clusters (e.g., SMH/SMHX/RACK/NODE all tilt semis)

**Success Criteria:**

- Average pairwise correlation < 0.75 (if > 0.75, funds are too similar; stop here)
- At least 5 decorrelated "themes" (e.g., semis, gold, commodities, bonds, crypto)
- No single fund has > 0.95 correlation with another (redundancy check)

**Output:** Correlation matrix, correlation timeline, redundancy report

### 1.2 – Establish Walk-Forward Scaffold (Foundation)

**Objective:** Build bulletproof backtesting harness with zero lookahead bias.

**Spec (must be defined before coding):**

| Parameter | Definition |
|---|---|
| Train Window | 3 years (lookback for signal + parameter fitting) |
| Test Window | 1 year (hold-out, no optimization) |
| Roll Forward | Monthly (move train/test window forward 1 month) |
| Rebalance Frequency | Monthly (first trading day of month) |
| Data Cutoff | Use prices as of market close on rebalance date; no intraday lookahead |
| Portfolio Size | Equal-weighted across N funds initially |
| Position Caps | 15% max per fund (prevents concentration) |
| Cash Handling | Excess rebalanced to money market (assume 4% annual yield) |

**Tasks:**

- [ ] Implement date-based train/test splitter (no data leakage)
- [ ] Write rebalance logic: on each monthly rebalance date, use only data up to that date
- [ ] Unit test: run equal-weight baseline across full period, check no future data is used
- [ ] Validate: compare output to manual spot-checks (e.g., Jan 2021 rebalance uses only data through Dec 2020)

**Success Criteria:**

- Zero lookahead bias detectable (unit tests pass)
- Rebalance dates align with market calendar
- Cash reinvestment is handled consistently

**Output:** Walk-forward testing framework (Python class or equivalent)

### 1.3 – Model Transaction Costs Realistically

**Objective:** Quantify actual costs, not assumptions.

**Tasks:**

**1.3.1 – Bid-Ask Spreads**

- [ ] Pull latest bid-ask for each ETF (multiple sources: Schwab, IB, TD Ameritrade)
- [ ] Average spreads for daily volume (e.g., if SMH avg daily vol is $500M, use retail spread ~$0.02)
- [ ] Create cost lookup table: cost[fund][volume_level]

**1.3.2 – Tax Treatment**

- [ ] Assume 37% long-term capital gains (federal + state + NIIT)
- [ ] Track realized gains per rebalance
- [ ] Run two backtests: pre-tax and post-tax
- [ ] Model wash-sale constraints if holding both a fund and its close correlate

**1.3.3 – Other Costs**

- [ ] ETF expense ratios (already baked into prices, no adjustment needed)
- [ ] Regulatory fees: ~$0.001 per trade
- [ ] Slippage: model as +0.5 bps on top of spread for realistic execution
- [ ] Dividend reinvestment friction: reinvest on distribution date, not rebalance date

**Cost Model:**

```
total_cost = (bid_ask_spread + slippage) × notional_traded + (capital_gains × 0.37) + regulatory_fee
```

**Success Criteria:**

- Cost model reflects retail execution at $10M–$100M AUM
- Pre-tax and post-tax backtests differ by > 20%
- If post-tax Sharpe < 0.3, stop here (edge likely doesn't exist after tax)

**Output:** Transaction cost lookup table, cost sensitivity analysis

### 1.4 – Baseline Testing: Equal Weight vs. 60/40

**Objective:** Establish whether complexity is even justified before testing allocation rules.

**Tasks:**

- [ ] Implement equal-weight portfolio (rebalance monthly, hold 1/9 of each fund)
- [ ] Implement 60/40 benchmark (60% equal-weight equity funds, 40% bond proxy; use SPY/BND if needed)
- [ ] Implement buy-and-hold SMH (passive sector bet)
- [ ] Run all three across full walk-forward period (train/test split)
- [ ] Calculate: Sharpe ratio, max drawdown, turnover, annualized return (pre- and post-tax)

**Success Criteria for Gate 1:**

- Equal-weight Sharpe (post-tax) > 60/40 Sharpe by at least 0.2
  - If NO: stop. Equal-weight can't beat passive. Allocation rules won't either.
  - If YES: proceed to Phase 2
- Max drawdown < -40% (if worse, funds are too volatile; stop)
- Turnover < 15% annualized (if higher, costs will dominate)

**Output:** Baseline performance report, go/no-go decision for Phase 2

---

## PHASE 2: ALLOCATION RULES TESTING (Weeks 4–8)

### 2.1 – Regime Characterization (Ex-Ante)

**Objective:** Define regimes before testing, avoid data mining.

**Pre-specified Regimes (locked down, no backfit):**

| Regime | Indicator | Threshold | Action |
|---|---|---|---|
| Bull | VIX < 15 AND S&P 500 YTD > 0% | — | Hold allocation rule |
| Correction | VIX 15–25 AND S&P 500 YTD ±10% | — | Reduce position size by 25% |
| Crisis | VIX > 25 OR S&P 500 drawdown > -20% | — | Hold hedges only (OUNZ, GDX, VGIT) |
| Stagflation | CRB Commodity Index > +20% YoY AND 10Y Yield > 4% | — | Overweight commodities (ANGL, GDX, FLTR) |

**Tasks:**

- [ ] Pull S&P 500, VIX, CRB Index daily
- [ ] Classify each trading day into one of 4 regimes
- [ ] Lock regime definitions (no adjustment based on backtest results)

**Output:** Regime calendar for full backtest period

### 2.2 – Signal Definition & Hyperparameter Specification

**Objective:** Specify signals without data mining. Fix hyperparameters upfront.

**Signals to Test (locked; no iterative tuning):**

| Signal | Formula | Lookback | Rebalance Trigger |
|---|---|---|---|
| Momentum | 60-day price return | 60 days | Score top 3 funds, hold equal-weight |
| Drift | % of days fund up in past 60 days | 60 days | Allocate based on drift % |
| None (Equal-Weight) | 1/9 allocation | — | Fixed 1/9 |

**Optimizers (locked; no ad-hoc tuning):**

| Optimizer | Formula | Constraints |
|---|---|---|
| Min-Variance | σ² minimized | 5%–20% per fund, long-only |
| Max-Sharpe | μ/σ maximized | 5%–20% per fund, long-only; use 60-day rolling return as μ |

**Hyperparameters (locked in advance):**

- Per-fund min: 5% (prevents excessive concentration)
- Per-fund max: 20% (prevents single-factor dominance)
- Drift threshold: allocate 15%–20% to top 3 drifting funds, 5% floor to others

**Success Criteria:**

- All hyperparameters and signals defined before running backtest
- No iterative tuning based on results

### 2.3 – Backtest Implementation & Correction for Multiple Testing

**Objective:** Run 6 strategy combinations (3 signals × 2 optimizers) with statistical rigor.

**Tasks:**

- [ ] Implement all 6 strategies in walk-forward harness
- [ ] For each strategy, calculate: Sharpe (pre- and post-tax), max drawdown, turnover
- [ ] Apply Bonferroni correction: alpha level = 0.05 / 6 = 0.0083
- [ ] Report: which strategies beat 60/40 with p < 0.0083 (significant after correction)

**Comparison Metrics:**

- Null Hypothesis: Strategy return = 60/40 return
- Alternative: Strategy return > 60/40 return (one-tailed)
- Test: T-test on annual returns across 5 out-of-sample years
- Significance threshold (Bonferroni-corrected): p < 0.0083

**Output:** Backtest results table, statistical significance report

### 2.4 – Degradation & Regime Testing

**Objective:** Ensure rules don't break in stressed regimes.

**Tasks:**

- [ ] Segment results by regime (Bull, Correction, Crisis, Stagflation)
- [ ] For each strategy, report Sharpe and max drawdown by regime
- [ ] Test: In Crisis regime, do allocation rules reduce drawdown vs. equal-weight?
  - If NO: rule isn't robust; low confidence
  - If YES: rule has conditional value
- [ ] Test: In Stagflation, does allocation tilt commodities beneficially?

**Success Criteria:**

- Winning strategies don't have outsized drawdowns in Crisis (within 5% of equal-weight)
- Allocation rules show regime-dependent benefit (not just luck in one regime)

**Output:** Regime performance breakdown, robustness report

---

## PHASE 3: FINDINGS & NEXT STEPS (Week 9)

### 3.1 – Decision Gate

**If Phase 2 shows NO significant winners:**

- Conclusion: Simple allocation rules do not beat 60/40 post-tax.
- Implication: Either VanEck universe is too correlated, or trading costs are too high.
- Recommendation: Stop or pivot to factor arbitrage licensing angle (sell methodology to VanEck, not asset allocation service).

**If Phase 2 shows 1–2 significant winners:**

- Conclusion: Rules exist; edge is modest and regime-dependent.
- Recommendation: Test on out-of-sample data (2024–2025 unseen data) to validate.
- Next step: If validated, consider live paper trading or small AUM deployment.

### 3.2 – Output & Deliverables

- [ ] Correlation heatmap + redundancy report
- [ ] Equal-weight baseline performance (pre/post-tax)
- [ ] Full backtest results: Sharpe, drawdown, turnover, significance tests
- [ ] Regime-specific performance table
- [ ] Write-up: "Why allocation rules succeed or fail on VanEck ETFs"

---

## PROJECT SCOPE & CONSTRAINTS

| Item | Specification |
|---|---|
| Universe | 9 pre-2025 VanEck funds (SMH, MOAT, GDX, ANGL, REMX, HAP, FLTR, PFXF, OUNZ) |
| Backtest Period | 5 years minimum (2019–2024); walk-forward with 3-year train, 1-year test |
| Excluded Funds | VBILL ($100k min), VBNB (single crypto), 2026 launches (insufficient history) |
| Position Caps | 5% min, 20% max per fund |
| Success Metric | Post-tax Sharpe > 60/40 Sharpe (Bonferroni-corrected p < 0.0083) |
| Failure Condition | Stop if equal-weight can't beat 60/40 post-tax (Gate 1 fails) |
| Timeline | Phase 1: 3 weeks |

## BUILD ORDER (CORRECTED)

1. Data pipeline (Week 1) – Pull + validate prices, adjust for distributions
2. Walk-forward scaffold (Week 1–2) – Build framework, unit test for lookahead bias
3. Transaction cost model (Week 2) – Bid-ask, tax, slippage lookup tables
4. Baselines (Week 2–3) – Equal-weight, 60/40, buy-and-hold; validate Gate 1
5. Regimes (Week 3–4) – Pre-specify and classify
6. Signal definitions (Week 4) – Lock all hyperparameters
7. Backtest all 6 strategies (Week 4–5) – Walk-forward with cost model
8. Statistical correction (Week 5) – Bonferroni p-values
9. Regime degradation (Week 5–6) – Stress test by market regime
10. Write-up (Week 9) – Final report + decision gate

## KEY DIFFERENCES FROM ORIGINAL

| Original | Revised |
|---|---|
| Test optimization first | Build scaffold first (no lookahead bugs) |
| 18 strategy combinations | 6 locked combinations (Bonferroni-corrected) |
| Success = "beat 60/40" | Success = Bonferroni-significant edge post-tax + regime robust |
| Tax treatment hand-waved | Tax + bid-ask modeled upfront; costs drive go/no-go at Gate 1 |
| Correlation "known limit" | Correlation analysis is Gate 0; if funds too similar, stop immediately |
| Abandon if not beating 60/40 | Abandon after testing equal-weight; don't waste time on optimization if baseline fails |
| No regime specification | Regimes locked before backtesting (no data mining) |

## DECISION GATES (Go/No-Go)

**Gate 1: Feasibility (End of Week 3)**

- Pass: Equal-weight Sharpe post-tax > 60/40 Sharpe + low enough turnover + correlation < 0.75 average
- Fail: Stop. Allocatable edge doesn't exist on this universe.

**Gate 2: Statistical Significance (End of Week 5)**

- Pass: At least 1 strategy beats 60/40 with Bonferroni p < 0.0083
- Fail: Stop. Allocation rules don't add significant value.

**Gate 3: Regime Robustness (End of Week 6)**

- Pass: Winning strategy(ies) maintain advantage across Bull/Correction/Crisis/Stagflation
- Fail: Edge is regime-luck. Low confidence for deployment.

## SUCCESS DEFINITION (Clear Criteria)

**✅ Success:**

- Gate 1 passes (equal-weight beats 60/40 post-tax)
- At least 1 strategy beats 60/40 with Bonferroni-corrected significance
- Winning strategy(ies) robust across regimes (Sharpe doesn't collapse in Crisis)
- Post-tax Sharpe > 1.0

**❌ Failure:**

- Gate 1 fails (equal-weight can't beat 60/40 post-tax)
- No strategies pass Bonferroni test
- Winning strategies degrade badly in Crisis or Stagflation
- Post-tax Sharpe < 0.5

## NEXT STEPS AFTER THIS PROJECT

**If successful:**

- Validate on 2024–2025 out-of-sample data (6 months live)
- Paper trade with realistic position sizes
- Consider 1–5% AUM deployment if paper trading holds
- Monitor regime shifts in real time

**If unsuccessful:**

- Investigate why (most likely: VanEck funds too correlated, or costs too high)
- Pivot: Sell methodology to VanEck (factor arbitrage licensing)
- Or: Start over with broader universe (600+ ETFs across all providers)

---

Project Owner: [You]
Start Date: [Today]
Target Completion: 9 weeks
Council-Informed Version: 1.0
