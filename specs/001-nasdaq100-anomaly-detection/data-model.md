# Data Model: Nasdaq-100 Universe and Daily Anomaly Detection

**Feature**: [spec.md](./spec.md) | **Research**: [research.md](./research.md)

Five entities back this feature, matching the Key Entities section of the spec. All five map to
PostgreSQL tables (plain SQL, no ORM — see research.md §5). Monetary/statistical values use
`NUMERIC` (not floating point) for anything stored, to keep results exactly reproducible.

## Entity summary

| Entity (spec) | Table | Primary key |
|---|---|---|
| Universe Constituent | `universe_constituents` | `ticker` |
| Daily Price Bar | `daily_price_bars` | `(ticker, trading_date)` |
| Detection Run | `detection_runs` | `evaluated_date` |
| Evaluated Stock Record | `evaluated_stock_records` | `(evaluated_date, ticker)` |
| Anomaly Case | `anomaly_cases` | `(evaluated_date, ticker)` |
| Skipped Stock Record | `skipped_stock_records` | `(evaluated_date, ticker)` |

## `universe_constituents`

The frozen Nasdaq-100 universe (FR-001–FR-003). Loaded once from the committed snapshot file and
never written to again by the application; the table exists so detection can join against it, not
so the application can mutate it.

| Column | Type | Notes |
|---|---|---|
| `ticker` | `TEXT PRIMARY KEY` | e.g. `AAPL` |
| `company_name` | `TEXT NOT NULL` | |
| `snapshot_date` | `DATE NOT NULL` | same value for every row — the single universe-capture date |
| `source` | `TEXT NOT NULL` | citation for where the snapshot came from (FR-001) |
| `share_class_note` | `TEXT NULL` | set when a company had more than one class and one was excluded, e.g. `"GOOGL kept, GOOG excluded"` (FR-002) |

**Validation rules**: `ticker` unique (enforced by primary key); exactly one row per company
(FR-002 — enforced at load time by the universe-loading script, not by a DB constraint, since
"same company" is not a column by itself). No `UPDATE`/`DELETE` paths are exposed once loaded.

## `daily_price_bars`

Every adjusted daily bar a run has ever fetched and used, for universe stocks and the benchmark
alike (FR-004, and the technical-context requirement to store the bars each run used so re-runs
are reproducible). Append-only.

| Column | Type | Notes |
|---|---|---|
| `ticker` | `TEXT NOT NULL` | a universe ticker or the configured benchmark ticker; no FK to `universe_constituents` so the benchmark row needs no universe entry |
| `trading_date` | `DATE NOT NULL` | |
| `open` | `NUMERIC NOT NULL` | adjusted for splits and dividends (FR-004) |
| `close` | `NUMERIC NOT NULL` | adjusted for splits and dividends; this is the value FR-005's return is computed from |
| `volume` | `NUMERIC NOT NULL` | |
| `source` | `TEXT NOT NULL` | price-data vendor identifier, for provenance |
| `fetched_at` | `TIMESTAMPTZ NOT NULL` | when this bar was retrieved |

**Primary key**: `(ticker, trading_date)`. **Validation rule**: once a `(ticker, trading_date)`
row exists, it is never updated — this is what makes the FR-018 reject-on-mismatch check possible
(a fresh fetch is compared value-for-value against any existing row for the same key before a run
is allowed to proceed).

## `detection_runs`

One row per evaluated trading day (FR-021: exactly one explicit day per run).

| Column | Type | Notes |
|---|---|---|
| `evaluated_date` | `DATE PRIMARY KEY` | the one day this run evaluated |
| `detection_rule_version` | `TEXT NOT NULL` | version of the flagging/cap/window rules (FR-014) |
| `computed_at` | `TIMESTAMPTZ NOT NULL` | when the computation was performed (FR-014) |
| `benchmark_ticker` | `TEXT NOT NULL` | which instrument served as the benchmark |
| `benchmark_return` | `NUMERIC NULL` | the benchmark's own return for the day (FR-015); `NULL` only when `status <> 'completed'` |
| `status` | `TEXT NOT NULL` | `'completed'` or `'skipped_benchmark_unavailable'` — the day-level outcome (see Edge Cases: benchmark data missing) |
| `status_detail` | `TEXT NULL` | reason text when `status <> 'completed'` |

**State transitions**: a `detection_runs` row is written exactly once per `evaluated_date` and
never updated afterward (FR-018). There is no "superseding" or "revision" state — the clarified
rule is reject-and-store-nothing on a data mismatch, so every row that exists reflects a run that
either completed or cleanly recorded a day-level skip; a rejected re-run leaves no trace in this
table (it fails before any write).

## `evaluated_stock_records`

One row per stock that had enough history and data to be evaluated on a given day — flagged or
not (FR-014, Clarifications Q1 of the spec's acceptance scenarios).

| Column | Type | Notes |
|---|---|---|
| `evaluated_date` | `DATE NOT NULL REFERENCES detection_runs(evaluated_date)` | |
| `ticker` | `TEXT NOT NULL` | |
| `return` | `NUMERIC NOT NULL` | FR-005 |
| `expected_return` | `NUMERIC NOT NULL` | from the market-model estimate (FR-007) |
| `abnormal_return` | `NUMERIC NOT NULL` | `return - expected_return` |
| `z_score` | `NUMERIC NOT NULL` | FR-008 |
| `alpha` | `NUMERIC NOT NULL` | regression intercept, kept for audit/debugging |
| `beta` | `NUMERIC NOT NULL` | regression slope, kept for audit/debugging |
| `estimation_window_days` | `SMALLINT NOT NULL` | `n` actually used (60–120, FR-006), needed to interpret the n − 2 degrees of freedom later |

**Primary key**: `(evaluated_date, ticker)`.

## `anomaly_cases`

The subset of a day's `evaluated_stock_records` whose absolute z-score met the flagging threshold
and was kept under the daily cap of 10 (FR-009, FR-010).

| Column | Type | Notes |
|---|---|---|
| `evaluated_date` | `DATE NOT NULL` | |
| `ticker` | `TEXT NOT NULL` | |
| `rank` | `SMALLINT NOT NULL` | 1–10, by descending absolute z-score, ties broken alphabetically by ticker (FR-010) |
| `volume_ratio` | `NUMERIC NULL` | FR-011; `NULL` exactly when `volume_ratio_unavailable_reason` is set |
| `volume_ratio_unavailable_reason` | `TEXT NULL` | set when fewer than 10 of the preceding 20 trading days had volume data (FR-011) |

**Primary key**: `(evaluated_date, ticker)`. **Foreign key**: `(evaluated_date, ticker)` →
`evaluated_stock_records(evaluated_date, ticker)` — every anomaly case is, by definition, one of
that day's evaluated stock records (spec's Key Entities: "the subset of Evaluated Stock Records").
**Validation rule**: at most 10 rows per `evaluated_date` (FR-010; enforced by the application
before insert, not by a DB constraint, since "top 10 by score" is not expressible as a simple
check constraint).

## `skipped_stock_records`

A stock that was not evaluated at all, or that qualified but lost out to the cap, for a given day
(FR-012, FR-010, FR-017). Per the Key Entities clarification: a stock that was evaluated but
simply did not qualify is **not** stored here — it is an `evaluated_stock_records` row.

| Column | Type | Notes |
|---|---|---|
| `evaluated_date` | `DATE NOT NULL` | |
| `ticker` | `TEXT NOT NULL` | |
| `reason_code` | `TEXT NOT NULL` | one of `insufficient_history`, `missing_data`, `cap_exceeded` |
| `reason_detail` | `TEXT NULL` | free-text elaboration, e.g. the exact history length found |

**Primary key**: `(evaluated_date, ticker)`.

## Cross-entity invariants (from the spec's Functional Requirements)

- A `(evaluated_date, ticker)` pair appears in **exactly one** of `evaluated_stock_records` or
  `skipped_stock_records`, never both, and never neither (every universe ticker lands in one of
  the two for every completed run) — this is the FR-012/FR-014 partition.
- Every row in `anomaly_cases` has a matching row in `evaluated_stock_records` with the same key.
- `detection_runs` rows are immutable once written; FR-018's reject path never produces a second
  row for the same `evaluated_date`, and never rewrites an existing one.
- `daily_price_bars` rows are immutable once written; this is what lets the application detect,
  before writing anything else, whether a re-run's freshly fetched bars match what a prior run
  already stored for the same `(ticker, trading_date)` keys.
