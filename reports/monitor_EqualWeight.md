# Monitor: EqualWeight (as of 2024-12-31)

Status: **ALERT**

Alerts:
- implementation gap vs intended targets +1.25% exceeds 1.01%: risk controls or fills are moving the book away from the research portfolio

## realized

| metric | value |
|---|---|
| total_return | 0.0958 |
| cagr | 0.0950 |
| ann_vol | 0.1161 |
| sharpe | 0.4190 |
| max_drawdown | -0.0692 |
| current_drawdown | -0.0644 |
| turnover_one_way_per_year | 0.2297 |
| costs_dollars | 8355.0592 |
| costs_bps_per_year | 7.6750 |
| dividends_received | 319954.6520 |

## reconciliation

| metric | value |
|---|---|
| tolerance | 0.0101 |
| paper_total_return | 0.0958 |
| research_executed_total_return | 0.0960 |
| cumulative_difference_vs_executed | 0.0000 |
| tracking_error_ann_vs_executed | 0.0001 |
| research_executed_costs_dollars | 8334.4204 |
| research_intended_total_return | 0.0835 |
| cumulative_difference_vs_intended | 0.0125 |
| tracking_error_ann_vs_intended | 0.0102 |
| research_intended_costs_dollars | 8971.5141 |
| research_intended_capped_total_return | 0.0973 |
| cumulative_difference_vs_intended_capped | -0.0013 |
| tracking_error_ann_vs_intended_capped | 0.0016 |
| research_intended_capped_costs_dollars | 8599.3626 |
| paper_costs_dollars | 8355.0592 |

## retirement rules (SPEC s.17)

Overall: **OK**

| rule | status | value | threshold | detail |
|---|---|---|---|---|
| R1_monthly_returns | ok | 0 | 3 of 6 below p5 | 0 of the last 6 months below the backtest 5th percentile (-4.97%) |
| R2_drawdown | ok | -0.0692 | -0.356 | worst drawdown -6.9% vs retire level -35.6% (1.25 x backtest -28.5%) |
| R3_rolling_sharpe | insufficient | 1 | 3 | needs 14 months of marks |
| R4_engine_agreement | ok | 0 | 2 consecutive | no persistent engine alert |
| R5_implementability | insufficient | 2 | 3 | needs three monitor runs |

## decisions

| status | count |
|---|---|
| submitted | 12 |

Rebalances where orders were scaled by the participation limit: 2023-12-29, 2024-01-31, 2024-02-29, 2024-03-28, 2024-04-30, 2024-05-31, 2024-06-28, 2024-07-31, 2024-08-30, 2024-09-30, 2024-10-31, 2024-11-29
