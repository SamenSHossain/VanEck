# Operations runbook

How the package is run as a system rather than as a one-off study. The design
follows three principles: every decision is recorded before it is acted on, no
decision is taken on unchecked data, and the paper book is reconciled against
the research engine so the two cannot silently diverge.

## The monthly cycle

| Step | Command | Gate |
|---|---|---|
| 1. Refresh data after the last close of the month | `python -m vaneck fetch --force` | - |
| 2. Health gate | `python -m vaneck check` | exit code 2 blocks the cycle |
| 3. Dry-run decision on the signal date | `python -m vaneck rebalance --strategy EqualWeight` | records a `dry_run` decision |
| 4. Human review of the decision record | open `paper/decisions/<signal>_<strategy>_dry_run.json` | targets, orders, risk checks, regime |
| 5. Execute after the first trading day's close | `python -m vaneck rebalance --strategy EqualWeight --submit` | refuses duplicates for the same signal date |
| 6. Monitor | `python -m vaneck monitor --strategy EqualWeight --mark` | exit code 1 on any alert |

Steps 1 to 3 and 6 are automated by `.github/workflows/monthly.yml`, which
uploads the health report, the dry-run decision and the monitor report as
artifacts. Nothing is executed automatically; step 5 is a human action.

## Decision records

Every call to `rebalance` writes `paper/decisions/<signal_date>_<strategy>_<status>.json`
and appends a line to `paper/decisions/log.md`. A record carries:

- the signal date, execution date and regime on the signal date;
- the intended target weights and, once filled, the executed weights;
- the orders after risk adjustments, with participation versus average dollar volume;
- the full health report and risk report that justified the status;
- the run manifest: git commit, dirty flag, configuration hash, data hash, package versions.

Statuses: `dry_run`, `submitted`, `blocked` (health or risk), `duplicate`
(already submitted for that signal date; override with `--force`), and
`awaiting_exec_bar` (submission requested before the execution day's close is
in the data).

## Risk controls (`vaneck/risk.py`)

| Control | Default | Action on breach |
|---|---|---|
| long only | - | block |
| per-position cap | strategy cap (15% equal weight, 20% rules) | block |
| gross exposure | 100% | block |
| one-way turnover per rebalance | 75% of equity | block |
| participation per order | 10% of trailing 60-day dollar volume | scale the order down, warn |
| minimum order | $500 | drop the order |
| cash after trades and estimated costs | >= 0 | block |
| signal staleness | 5 calendar days | block |
| drawdown halt | -25% from the book's peak | remove buys, keep sells, warn |
| cost reserve | 10 bps of equity held back from targets | - |

The participation limit is the one that binds in practice: at $10M, equal
weight in HAP is about 65% of a day's volume, so the book carries a persistent
underweight that the monitor reports as an implementation gap.

## Health checks (`vaneck/health.py`)

Block: a ticker missing on the latest bar, NaNs in the last five rows, an
adjustment-factor break in the last sixty rows, macro values outside sane
ranges, a last bar more than ten days old, or insufficient signal history.
Warn: a daily move above 25%, zero volume on the last bar, a last bar more
than five days old, a cache older than three days.

## Execution policy

`rebalance` and `replay` accept `--policy P0|P1|P2|P3` (SPEC section 14). The
orders then come from the same planner the research simulator uses: TAXOPT lot
relief (P1), a 20% relative drift band (P2), and loss harvesting into the
pre-registered substitutes (P3). The paper book keeps tax lots, wash-sale
state, realised gains by year and the accrued liability. Without `--policy`
the legacy full rebalance applies.

## Monitoring (`vaneck/monitor.py`)

- **Engine agreement.** The research simulator is fed the weights the paper
  book actually executed. The two curves should agree within 1% per year;
  a larger gap is an engine or data problem and raises an alert.
- **Implementation gap.** The simulator is fed the intended targets, once
  without and once with the live participation cap. The uncapped difference is
  the cost of the risk controls; the capped difference should be whole-share
  rounding and dividends held as cash (about -0.1% in the 2024 rehearsal).
- **Book hygiene.** Current drawdown versus the halt level, blocked decisions,
  rebalances where orders were scaled, and days since the last decision.

Reports land in `reports/monitor_<strategy>.md` and `.json`.

## Paper book (`vaneck/broker.py`)

A JSON book of whole shares and cash. Dividends are credited as cash on the
ex-date, cash earns the T-bill yield daily, splits after the book's data
vintage are applied to share counts, and fills happen at the execution day's
close with the cost model's slippage. `python -m vaneck replay` runs the whole
cycle over a past window as an operational rehearsal.

## Connecting a real broker

Implement the `Broker` protocol (`positions`, `cash`, `equity`, `submit`)
against the broker's API and pass it to `live.rebalance`. Reconcile positions
against the broker before every decision, set the lot-relief method (HIFO or
specific identification) at the broker, and keep the paper book running in
parallel as the control. Nothing in this repository talks to a real broker.

## What is deliberately not here

- No intraday data or execution; the design is end-of-day and monthly.
- No automatic submission: a human approves every `--submit`.
- No strategy has earned deployment: the study stopped at Gate 1, so the
  operational pipeline is exercised with equal weight as a benchmark control.

## Second source and cross-validation

`python -m vaneck fetch --secondary` refreshes FRED (VIX close, 10-year CMT,
3-month bill, S&P 500) into `data/secondary/`. `python -m vaneck check --cross`
adds three layers to the health gate:

- **FRED agreement** for every macro input: block if the last common day
  disagrees beyond tolerance, warn if more than 1% of days do.
- **Peer divergence**: each fund's daily returns against its cached substitute
  (SMH/SOXX, GDX/RING, ...). A one-day gap above 10% warns, above 25% blocks;
  correlation is checked only for pairs that move at least 0.3% a day.
- **Corporate-actions audit**: every change in Yahoo's adjustment factor must
  coincide with a recorded distribution of matching size. Unexplained factor
  changes block; a distribution above 10% of price is flagged for review.

Stooq is wired as a price provider but currently answers with a JavaScript
challenge, so it reports itself unavailable. A keyed vendor plugs in behind
`vaneck.sources.PriceProvider`.

## Golden results

`tests/golden/` freezes the key numbers of the main study and the extensions
together with the configuration hash and the raw-data hash they came from.
`python -m vaneck golden` (and the test suite) fail if either hash changed or
if the numbers moved while the hashes did not. After a deliberate change, run
`python -m vaneck golden --update`, read the diff, and commit it: results can
only change together with a visible change in inputs or code.

## History, status, alerts, dashboard

Every `check`, `rebalance`, `replay`, `monitor`, `all` and `extensions` run is
recorded in `state/vaneck.sqlite`. `python -m vaneck history` prints the
timeline, `python -m vaneck status` the latest state (exit 1 when anything is
blocked or alerting), and `python -m vaneck dashboard` renders
`reports/dashboard.html`. A blocked health check, a blocked or unexecutable
decision and any monitor alert are delivered to the webhook in
`VANECK_WEBHOOK_URL` (Slack-compatible JSON) and always appended to
`state/alerts.log`.

## Type checking, packaging, container

`make typecheck` runs mypy (CI does too); `pip install -e .` provides the
`vaneck` console command; `make docker` builds an image that runs the test
suite at build time and defaults to the health check.
