# Data Model: Nasdaq-100 Universe and Daily Anomaly Detection

**Feature**: [spec.md](./spec.md) | **Research**: [research.md](./research.md)

Five entities back this feature, matching the Key Entities section of the spec. All five map to
PostgreSQL tables (plain SQL, no ORM — see research.md §5). Monetary/statistical values use
`NUMERIC` (not floating point) for anything stored, to keep results exactly reproducible.

## Entity summary

| Entity (spec) | Table | Primary key |
|---|---|---|
| Universe Constituent | `universe_constituents` | `ticker` |
| Daily Price Bar | `daily_price_bars` | `(evaluated_date, ticker, trading_date)` |
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

A snapshot of the exact adjusted bars each run fetched and used, for universe stocks and the
benchmark alike (FR-004, and the technical-context requirement to store the bars each run used so
re-runs are reproducible). Append-only.

Bars are stored **per run**, not once per `(ticker, trading_date)`. Split- and dividend-adjusted
prices are restated for every earlier day whenever a stock goes ex-dividend or splits, so the same
`(ticker, trading_date)` legitimately has different adjusted values in runs made on different days.
Each run therefore computes only from its own fresh fetch and stores that whole window under its
`evaluated_date`; it never reads or compares bars stored by a run for a different evaluated day.

| Column | Type | Notes |
|---|---|---|
| `evaluated_date` | `DATE NOT NULL REFERENCES detection_runs(evaluated_date)` | the run whose input snapshot this bar belongs to |
| `ticker` | `TEXT NOT NULL` | a universe ticker or the configured benchmark ticker; no FK to `universe_constituents` so the benchmark row needs no universe entry |
| `trading_date` | `DATE NOT NULL` | |
| `open` | `NUMERIC NOT NULL` | adjusted for splits and dividends (FR-004) |
| `close` | `NUMERIC NOT NULL` | adjusted for splits and dividends; this is the value FR-005's return is computed from |
| `volume` | `NUMERIC NOT NULL` | |
| `source` | `TEXT NOT NULL` | price-data vendor identifier, for provenance |
| `fetched_at` | `TIMESTAMPTZ NOT NULL` | the detection run's `computed_at`; every bar of a run is fetched during that run, so the run's timestamp stands in for the retrieval time |

**Primary key**: `(evaluated_date, ticker, trading_date)`. **Validation rule**: rows are never
updated or deleted — this is what makes the FR-018 reject-on-mismatch check possible: when a run
for an `evaluated_date` that already has a `detection_runs` row is started, its fresh fetch is
compared value-for-value (`open`, `close`, `volume`, and the set of `(ticker, trading_date)` keys)
against that `evaluated_date`'s stored snapshot before anything is written.

## `detection_runs`

One row per evaluated trading day (FR-021: exactly one explicit day per run).

| Column | Type | Notes |
|---|---|---|
| `evaluated_date` | `DATE PRIMARY KEY` | the one day this run evaluated |
| `detection_rule_version` | `TEXT NOT NULL` | version of the flagging/cap/window rules (FR-014) |
| `computed_at` | `TIMESTAMPTZ NOT NULL` | when the computation was performed (FR-014) |
| `benchmark_ticker` | `TEXT NOT NULL` | which instrument served as the benchmark |
| `benchmark_return` | `NUMERIC NOT NULL` | the benchmark's own return for the day (FR-015) |

**State transitions**: a `detection_runs` row is written exactly once per `evaluated_date` and
never updated afterward (FR-018). There is no "superseding", "revision" or day-level "skipped"
state: every row that exists reflects a completed run. A run that fails — because its re-run data
does not match the stored snapshot (FR-018), the benchmark has no usable data (FR-022), or more
than 20% of the universe is missing data (FR-023) — fails before any write and leaves no trace in
any table, so the day can be run again later.

## `evaluated_stock_records`

One row per stock that had enough history and data to be evaluated on a given day — flagged,
not qualifying, or qualifying but dropped by the daily cap (FR-014: "for every evaluated stock
whether flagged or not"). A cap-exceeded stock was fully evaluated, so it gets this row with all
its numbers **and** a `cap_exceeded` row in `skipped_stock_records`.

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
(FR-012, FR-010, FR-017). The two kinds differ:

- `insufficient_history` / `missing_data`: the stock was not evaluated, so it has **no**
  `evaluated_stock_records` row for that day.
- `cap_exceeded`: the stock was evaluated and qualified (|z| ≥ 2) but ranked below 10th, so it
  **also** has an `evaluated_stock_records` row with all its numbers (FR-014), and no
  `anomaly_cases` row.

Per the Key Entities clarification: a stock that was evaluated but simply did not qualify is
**not** stored here — it is only an `evaluated_stock_records` row.

| Column | Type | Notes |
|---|---|---|
| `evaluated_date` | `DATE NOT NULL` | |
| `ticker` | `TEXT NOT NULL` | |
| `reason_code` | `TEXT NOT NULL` | one of `insufficient_history`, `missing_data`, `cap_exceeded` |
| `reason_detail` | `TEXT NULL` | free-text elaboration, e.g. the exact history length found, or for `cap_exceeded` the rank, e.g. `"rank 11 of 14 qualifying"` |

**Primary key**: `(evaluated_date, ticker)`. **Validation rule**: a `cap_exceeded` row requires a
matching `evaluated_stock_records` row with `abs(z_score) >= 2`; an `insufficient_history` or
`missing_data` row requires that no such row exists (enforced by the application before insert,
since a foreign key cannot depend on `reason_code`).

## Cross-entity invariants (from the spec's Functional Requirements)

- For every completed run, every universe ticker has exactly one of these outcomes:
  - evaluated, not qualifying: an `evaluated_stock_records` row only;
  - flagged: an `evaluated_stock_records` row and an `anomaly_cases` row;
  - cap-exceeded: an `evaluated_stock_records` row and a `skipped_stock_records` row with
    `reason_code = 'cap_exceeded'`;
  - not evaluated: a `skipped_stock_records` row with `reason_code` `insufficient_history` or
    `missing_data`, and no `evaluated_stock_records` row.
- Every row in `anomaly_cases` has a matching row in `evaluated_stock_records` with the same key,
  and no matching `skipped_stock_records` row.
- Every `evaluated_stock_records` row with `abs(z_score) >= 2` has exactly one of an
  `anomaly_cases` row or a `cap_exceeded` skip; together these number at most 10 anomaly cases
  plus all the rest as `cap_exceeded` (FR-010, SC-004).
- `detection_runs` rows are immutable once written; FR-018's reject path never produces a second
  row for the same `evaluated_date`, and never rewrites an existing one.
- `daily_price_bars` rows are immutable once written; this is what lets the application detect,
  before writing anything else, whether a re-run's freshly fetched bars match the snapshot the
  prior run for the same `evaluated_date` stored. Runs for different evaluated days never compare
  against each other's snapshots.
