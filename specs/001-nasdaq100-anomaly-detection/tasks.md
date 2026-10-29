---

description: "Task list for Nasdaq-100 Universe and Daily Anomaly Detection"
---

# Tasks: Nasdaq-100 Universe and Daily Anomaly Detection

**Input**: Design documents from `/specs/001-nasdaq100-anomaly-detection/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli-contract.md,
contracts/price-data-source-interface.md, quickstart.md

**Tests**: Required. Constitution Principle VIII requires test-first development of all
deterministic logic (a failing test MUST exist before the implementation), and spec SC-002 requires
a hand-checked example for every calculation. Every test task below comes before the task that
implements it and MUST be seen failing first. No test makes a network call; the price-data source
is always a test double or a fake HTTP session.

**Organization**: Tasks are grouped by user story so each story can be implemented and tested on
its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: Which user story the task belongs to (US1, US2, US3)

## Path Conventions

Single project at the repository root: `src/market_anomaly_agent/`, `tests/unit/`,
`tests/integration/` (plan.md, Project Structure).

## Shared decisions used by several tasks

These are fixed here so no task has to decide them during implementation:

- **Rule constants** live in `src/market_anomaly_agent/rules.py` (a small addition to the plan's
  structure, so the rule version and its parameters sit in one place): `RULE_VERSION = "1"`,
  `Z_THRESHOLD = 2.0`, `DAILY_CAP = 10`, `MAX_WINDOW_DAYS = 120`, `MIN_WINDOW_DAYS = 60`,
  `VOLUME_WINDOW_DAYS = 20`, `MIN_VOLUME_DAYS = 10`, `MAX_MISSING_DATA_PERCENT = 20` (added by
  T050), `BENCHMARK_TICKER = "QQQ"`. Any change to a parameter other than `BENCHMARK_TICKER` MUST
  bump `RULE_VERSION` (spec Assumptions).
- **Failed runs store nothing**: `detection.py` defines `RunAbortedError(Exception)` with two
  subclasses, `BenchmarkUnavailableError` (FR-022) and `TooMuchMissingDataError` (FR-023).
  `run_detection` raises them before returning any result, so nothing reaches `save_run`; the CLI
  maps `RunAbortedError` to exit `4` (contracts/cli-contract.md). The missing-data limit is checked
  in integers, `missing_count * 100 > len(tickers) * MAX_MISSING_DATA_PERCENT`, so exactly 20%
  passes and there is no floating-point edge case. Only `missing_data` skips count, not
  `insufficient_history`. `PriceDataError` from the source is not caught by detection and exits `1`.
- **Fetch window** for evaluated day D: from the trading day 121 trading days before D through D
  inclusive (122 trading days). That gives up to 120 returns before D for the regression (FR-006),
  D's own return, and the 20 preceding trading days for the volume ratio (FR-011).
- **Returns**: a return for trading day t exists only when both t's close and the previous
  *trading day's* close are present; a return is never computed across a gap (FR-005). The
  regression uses only days before D where both the stock's and the benchmark's return exist; `n`
  is the count of those days, capped at the 120 most recent.
- **Numbers**: bars arrive from the vendor as decimal strings and are kept as `Decimal` from the
  adapter to the database (no float round-trip). Calculations run in NumPy `float64`; results are
  converted with `Decimal(repr(value))` for storage. Comparison of bars for FR-018 is on the
  `Decimal` values.
- **Cap-exceeded stocks** get an `evaluated_stock_records` row with all their numbers (FR-014)
  **and** a `skipped_stock_records` row with `reason_code = 'cap_exceeded'`, and no
  `anomaly_cases` row (data-model.md cross-entity invariants); `reason_detail` records the
  z-score and rank, e.g. `"abs z=2.3100, rank 11 of 14 qualifying"`. Stocks skipped for
  `insufficient_history` or `missing_data` have no evaluated row. In the CLI summary,
  `evaluated` counts `evaluated_stock_records` rows and `skipped` counts `skipped_stock_records`
  rows, so a cap-exceeded stock is counted in both.
- **Bar snapshots**: each run stores the full window it fetched under its own `evaluated_date`
  (data-model.md `daily_price_bars`) and computes only from that fresh fetch.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and tooling

- [X] T001 Create `pyproject.toml` at the repository root for package `market-anomaly-agent` (import name `market_anomaly_agent`, src layout, `requires-python = ">=3.12"`, build backend `uv_build`), with runtime dependencies `numpy`, `pandas_market_calendars`, `requests`, `psycopg[binary]` (v3); dev dependency group `pytest`, `ruff`, `mypy`, `types-requests`; console script `detect = "market_anomaly_agent.cli:main"`; `[tool.pytest.ini_options]` with `testpaths = ["tests"]`, marker `integration: needs a local PostgreSQL`, and `addopts = "-m 'not integration'"`; `[tool.ruff]` with `line-length = 100`; `[tool.mypy]` with `strict = true`, `files = ["src", "tests"]`, and a `[[tool.mypy.overrides]]` setting `ignore_missing_imports = true` for `pandas_market_calendars`. Then run `uv sync` with `UV_PROJECT_ENVIRONMENT=/home/dev/.venvs/market-anomaly-agent` (plan.md Constraints) to create `uv.lock`
- [X] T002 Create the package skeleton from plan.md Project Structure: empty `__init__.py` files in `src/market_anomaly_agent/`, `src/market_anomaly_agent/calc/`, `src/market_anomaly_agent/universe/`, `src/market_anomaly_agent/data/`, `src/market_anomaly_agent/storage/`, plus `src/market_anomaly_agent/py.typed`, `src/market_anomaly_agent/storage/migrations/` (empty directory with `.gitkeep`), and test directories `tests/unit/calc/`, `tests/unit/data/`, `tests/unit/universe/`, `tests/integration/` (with `__init__.py` files so test module names do not collide)
- [X] T003 [P] Create `compose.yaml` at the repository root with a single service `db` (image `docker.io/library/postgres:17`, environment `POSTGRES_USER=market_anomaly_agent`, `POSTGRES_DB=market_anomaly_agent`, `POSTGRES_PASSWORD` read from `.env`, port `5432:5432`, named volume `pgdata` mounted at `/var/lib/postgresql/data`, a `pg_isready` healthcheck), runnable with `podman compose up -d db`
- [X] T004 [P] Create `.env.example` at the repository root listing `POSTGRES_PASSWORD=`, `DATABASE_URL=postgresql://market_anomaly_agent:<password>@host.containers.internal:5432/market_anomaly_agent` and `PRICE_DATA_API_KEY=` with no real values (`.env` itself is already git-ignored)
- [X] T005 [P] Create `src/market_anomaly_agent/rules.py` with the constants listed under "Shared decisions" above, each with a one-line comment naming the requirement it implements (FR-006, FR-009, FR-010, FR-011, FR-014)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Calendar, price-data interface and adapter, universe file reader, schema and
migration runner. Every user story depends on these.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests for Phase 2 (write first, see them fail)

- [X] T006 [P] Write `tests/unit/test_calendar.py` for the NASDAQ calendar in `America/New_York` (FR-013): `is_trading_day` is false for a Saturday, for 2025-12-25 and for 2025-07-04, and true for 2025-11-28 (day after Thanksgiving, early close counts as a full trading day); `previous_trading_day(date(2025, 12, 26))` is 2025-12-24; `previous_trading_day(date(2026, 1, 2))` is 2025-12-31; `trading_days_before(date(2026, 3, 9), 5)` returns the 5 trading days before 2026-03-09 in ascending order and is unaffected by the 2026-03-08 DST change; `fetch_window_start(d)` returns the trading day 121 trading days before `d`
- [X] T007 [P] Write `tests/unit/data/test_twelvedata_source.py` against `TwelveDataPriceDataSource` using an injected fake session (an object with a `get(url, params, headers, timeout)` method returning canned JSON) and an injected fake `sleep` that records calls (no real HTTP, no real waiting). Cover: request goes to `https://api.twelvedata.com/time_series` with params `symbol`, `interval=1day`, `adjust=all`, `start_date`, `end_date` set to the day *after* the requested end, `order=asc`, `timezone=America/New_York`, and the API key in the `Authorization: apikey <key>` header, never in the URL or params; a canned `{"status": "ok", "values": [{"datetime": "2026-10-27", "open": "101.25", "high": "...", "low": "...", "close": "102.50", "volume": "1234567"}]}` becomes `DailyBar(trading_date=date(2026, 10, 27), open=Decimal("101.25"), close=Decimal("102.50"), volume=1234567)`; bars outside `[start, end]` are dropped; a body with `"status": "error"` and `code` 400 or 404 raises `PriceDataUnavailable` (superseded by T046: only 404 does, 400 raises `PriceDataError`); HTTP 429, HTTP 5xx, a `"code": 429` error body and a timeout are retried (at most 3 retries, waiting 60 s on 429 and 2/4/8 s otherwise) and raise `PriceDataError` once retries are exhausted; consecutive requests are spaced at least 7.5 s apart (8 per minute); the 801st request in one adapter instance raises `PriceDataError` without calling the session (800 per day); the API key never appears in any raised exception message or log record (use `caplog`)
- [X] T008 [P] Write `tests/unit/universe/test_constituents.py` for `read_universe_file(path)`: a fixture CSV with header `ticker,company_name,snapshot_date,source,share_class_note` parses into a tuple of `UniverseConstituent` (fields matching the header, `snapshot_date` as `date`, empty `share_class_note` as `None`) sorted by ticker; a missing column or an unparseable date raises `UniverseFileError`; `find_universe_file(directory)` returns the single `nasdaq100_*.csv` file and raises `UniverseFileError` when there are zero or more than one
- [X] T009 [P] Write `tests/integration/test_storage.py::test_migrations_apply_once` (mark `@pytest.mark.integration`, uses `DATABASE_URL`, creates and drops a throwaway schema per test): running the migration runner twice applies `0001_init.sql` exactly once, records it in `schema_migrations`, and creates the six tables from data-model.md with their primary keys

### Implementation for Phase 2

- [X] T010 [P] Implement `src/market_anomaly_agent/calendar.py` (pass T006) using `pandas_market_calendars.get_calendar("NASDAQ")` and `America/New_York`: `is_trading_day(d: date) -> bool`, `previous_trading_day(d: date) -> date`, `trading_days_before(d: date, n: int) -> list[date]` (ascending, excludes `d`), `trading_days_between(start: date, end: date) -> list[date]` (inclusive), `fetch_window_start(d: date) -> date` (121 trading days before `d`). All take and return plain `date`s; early-close sessions are ordinary trading days
- [X] T011 [P] Implement `src/market_anomaly_agent/data/source.py` exactly as contracts/price-data-source-interface.md: frozen dataclass `DailyBar(trading_date: date, open: Decimal, close: Decimal, volume: int)` (open and close split- and dividend-adjusted), `PriceDataSource` `Protocol` with `get_daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]`, exceptions `PriceDataUnavailable` (vendor has no data for the ticker at all) and `PriceDataError` (retries or request budget exhausted)
- [X] T012 Implement `src/market_anomaly_agent/data/twelvedata_source.py` (pass T007; depends on T011): class `TwelveDataPriceDataSource(api_key: str, session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic)` implementing `PriceDataSource` per contracts/price-data-source-interface.md and research.md §1/§6 — sequential requests, minimum 7.5 s between requests, hard stop at 800 requests per instance, retries and waits as specified in T007, `timeout=30` per request, results sorted by `trading_date` and filtered to `[start, end]`. Pass `end_date` as the day after `end` so the end day is included whether or not the API treats `end_date` as exclusive. Never fabricate a bar; never log or include the API key in messages (vendor error 400 handling corrected by T048)
- [X] T013 [P] Implement `src/market_anomaly_agent/universe/constituents.py` reader part (pass T008): frozen dataclass `UniverseConstituent(ticker: str, company_name: str, snapshot_date: date, source: str, share_class_note: str | None)`, `UniverseFileError`, `find_universe_file(directory: Path) -> Path`, `read_universe_file(path: Path) -> tuple[UniverseConstituent, ...]` using the standard-library `csv` module. Validation of the frozen-universe rules is added in US2 (T034)
- [X] T014 Write `src/market_anomaly_agent/storage/migrations/0001_init.sql` creating the six tables exactly as data-model.md, including these constraints verbatim from it (the `detection_runs` columns `status` and `status_detail` and the nullable `benchmark_return` below are superseded by T049):
  `universe_constituents`: `ticker TEXT PRIMARY KEY`, `company_name TEXT NOT NULL`, `snapshot_date DATE NOT NULL`, `source TEXT NOT NULL`, `share_class_note TEXT NULL`.
  `detection_runs`: `evaluated_date DATE PRIMARY KEY`, `detection_rule_version TEXT NOT NULL`, `computed_at TIMESTAMPTZ NOT NULL`, `benchmark_ticker TEXT NOT NULL`, `benchmark_return NUMERIC NULL` ("`NULL` only when `status <> 'completed'`" — add `CHECK ((status = 'completed') = (benchmark_return IS NOT NULL))`), `status TEXT NOT NULL` ("`'completed'` or `'skipped_benchmark_unavailable'`" — add a `CHECK` with exactly those two values), `status_detail TEXT NULL`.
  `daily_price_bars`: `evaluated_date DATE NOT NULL REFERENCES detection_runs(evaluated_date)`, `ticker TEXT NOT NULL` (no FK to `universe_constituents`), `trading_date DATE NOT NULL`, `open NUMERIC NOT NULL`, `close NUMERIC NOT NULL`, `volume NUMERIC NOT NULL`, `source TEXT NOT NULL`, `fetched_at TIMESTAMPTZ NOT NULL`, `PRIMARY KEY (evaluated_date, ticker, trading_date)`.
  `evaluated_stock_records`: `evaluated_date DATE NOT NULL REFERENCES detection_runs(evaluated_date)`, `ticker TEXT NOT NULL`, `"return" NUMERIC NOT NULL` (always double-quote this column name), `expected_return NUMERIC NOT NULL`, `abnormal_return NUMERIC NOT NULL`, `z_score NUMERIC NOT NULL`, `alpha NUMERIC NOT NULL`, `beta NUMERIC NOT NULL`, `estimation_window_days SMALLINT NOT NULL` ("60–120" — add `CHECK (estimation_window_days BETWEEN 60 AND 120)`), `PRIMARY KEY (evaluated_date, ticker)`.
  `anomaly_cases`: `evaluated_date DATE NOT NULL`, `ticker TEXT NOT NULL`, `rank SMALLINT NOT NULL` ("1–10" — `CHECK (rank BETWEEN 1 AND 10)`), `volume_ratio NUMERIC NULL`, `volume_ratio_unavailable_reason TEXT NULL` ("`NULL` exactly when `volume_ratio_unavailable_reason` is set" — `CHECK ((volume_ratio IS NULL) = (volume_ratio_unavailable_reason IS NOT NULL))`), `PRIMARY KEY (evaluated_date, ticker)`, `FOREIGN KEY (evaluated_date, ticker) REFERENCES evaluated_stock_records(evaluated_date, ticker)`, `UNIQUE (evaluated_date, rank)`.
  `skipped_stock_records`: `evaluated_date DATE NOT NULL REFERENCES detection_runs(evaluated_date)`, `ticker TEXT NOT NULL`, `reason_code TEXT NOT NULL` ("one of `insufficient_history`, `missing_data`, `cap_exceeded`" — `CHECK` with exactly those values), `reason_detail TEXT NULL`, `PRIMARY KEY (evaluated_date, ticker)`
- [X] T015 Implement `src/market_anomaly_agent/storage/db.py` (`connect() -> psycopg.Connection` reading `DATABASE_URL` from the environment; raise a clear error naming the variable when unset, without printing its value) and `src/market_anomaly_agent/storage/migrate.py` (pass T009): creates `schema_migrations(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL)` if missing, applies `migrations/*.sql` not yet recorded in filename order, each file plus its `schema_migrations` row in one transaction; runnable as `uv run python -m market_anomaly_agent.storage.migrate` (quickstart.md)

**Checkpoint**: Calendar, adapter, universe reader and schema are in place; `uv run pytest` and `uv run pytest -m integration` pass.

---

## Phase 2b: Foundational corrections (Blocking Prerequisites)

**Purpose**: Bring the finished Phase 2 code in line with the updated contract and data model:
vendor error `400` fails loudly (contracts/price-data-source-interface.md), a run with an
unusable benchmark or too much missing data stores nothing (spec FR-022, FR-023), so the stored
day-level skip status goes away. New IDs are appended (T046–T050) instead of renumbering the
existing tasks. The database holds no real data yet, so `0001_init.sql` is edited in place rather
than adding a second migration.

**⚠️ CRITICAL**: Complete before Phase 3; T027, T028 and T030 rely on the corrected schema and
adapter.

### Tests for Phase 2b (write first, see them fail)

- [X] T046 [P] In `tests/unit/data/test_twelvedata_source.py`, replace the parametrized `test_error_body_for_unknown_symbol_raises_unavailable` with two tests: a body `{"status": "error", "code": 404, "message": "..."}` raises `PriceDataUnavailable`; a body with `"code": 400` raises `PriceDataError` (not `PriceDataUnavailable`) whose message contains `400` and the ticker, after exactly one session call (no retry)
- [X] T047 [P] In `tests/integration/test_storage.py`, add `test_detection_runs_has_no_skip_status` (marked `integration`, uses the `db_conn` fixture): after `apply_migrations`, the columns of `detection_runs` in `information_schema.columns` are exactly `evaluated_date`, `detection_rule_version`, `computed_at`, `benchmark_ticker`, `benchmark_return`, in that order, and `benchmark_return` has `is_nullable = 'NO'`; inserting a `detection_runs` row with `benchmark_return = NULL` raises `psycopg.errors.NotNullViolation`

### Implementation for Phase 2b

- [X] T048 In `src/market_anomaly_agent/data/twelvedata_source.py`, set `UNAVAILABLE_CODES = {404}` so vendor error `400` falls through to `PriceDataError` with the scrubbed vendor message (pass T046; all other adapter tests keep passing). Update the `PriceDataUnavailable` docstring in `src/market_anomaly_agent/data/source.py` to "The vendor reports no matching data for the ticker." to match contracts/price-data-source-interface.md
- [X] T049 Edit `src/market_anomaly_agent/storage/migrations/0001_init.sql` so `detection_runs` matches data-model.md: remove the `status` and `status_detail` columns, the `CHECK (status IN (...))` and the `CHECK ((status = 'completed') = (benchmark_return IS NOT NULL))`, and change `benchmark_return NUMERIC NULL` to `benchmark_return NUMERIC NOT NULL` ("the benchmark's own return for the day (FR-015)") (pass T047; `test_migrations_apply_once` keeps passing). If the migration was already applied to the local development database, the repository owner resets it before Phase 3 by running `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` in that database and then `uv run python -m market_anomaly_agent.storage.migrate`; it holds no real data
- [X] T050 [P] Add `MAX_MISSING_DATA_PERCENT: Final = 20` to `src/market_anomaly_agent/rules.py`, after `MIN_VOLUME_DAYS`, with the comment `# more missing_data skips than this share of the universe -> fail the run (FR-023)`; `RULE_VERSION` stays `"1"` because no run has been stored yet

**Checkpoint**: `uv run pytest` and `uv run pytest -m integration` pass with the corrected adapter and schema.

---

## Phase 3: User Story 1 - Daily anomaly flagging before market open (Priority: P1) 🎯 MVP

**Goal**: For one explicit evaluated trading day, compute every universe stock's return, market-model
abnormal return and z-score against the benchmark, flag up to 10 stocks with |z| ≥ 2 (the rest
keep their evaluated record and are additionally recorded as `cap_exceeded`), compute a volume ratio for each flagged case, and store everything once —
reproducing, never duplicating, on a re-run with unchanged data and rejecting a re-run with changed
data.

**Independent Test**: With a fake `PriceDataSource` returning a fixed, hand-built price history,
run detection for one evaluated day and compare flagged cases and every stored number with
hand-calculated values (spec US1 Independent Test); then run it again and confirm no duplicates,
and alter one stored bar and confirm exit code 3.

### Tests for User Story 1 (write first, see them fail) ⚠️

Each calculation test MUST include at least one example whose expected values were worked out by
hand and are written out step by step in the test's docstring (SC-002).

- [X] T016 [P] [US1] Write `tests/unit/calc/test_returns.py` for `simple_returns(closes: Mapping[date, Decimal], trading_days: Sequence[date]) -> dict[date, float]` (FR-005): hand-checked example closes 100 → 102 → 99.96 give returns 0.02 and −0.02; a day whose previous trading day has no close gets no return (no return across a gap); the first trading day in the sequence gets no return
- [X] T017 [P] [US1] Write `tests/unit/calc/test_market_model.py` for `fit_market_model(stock: Sequence[float], benchmark: Sequence[float]) -> MarketModel(alpha, beta, residual_std, n)`, `expected_return(model, benchmark_return)`, `abnormal_return(actual, expected)` and `z_score(abnormal, residual_std)` (FR-006–FR-008): a 5-point hand-checked example (e.g. benchmark 0.01, 0.02, −0.01, 0.00, 0.03 and stock 0.012, 0.025, −0.008, 0.001, 0.031) with alpha, beta, residuals and `residual_std = sqrt(sum(residuals²) / (n − 2))` written out in the docstring and asserted to 1e-12; a perfect-fit example where `residual_std` is 0 makes `z_score` raise `ValueError`; mismatched lengths or `n < 3` raise `ValueError`
- [X] T018 [P] [US1] Write `tests/unit/calc/test_volume.py` for `volume_ratio(evaluated_volume: int, window_volumes: Sequence[int | None]) -> VolumeRatioResult(ratio: float | None, unavailable_reason: str | None)` (FR-011, Clarification Q3): 20 present volumes, hand-checked; 12 present and 8 `None` averages over the 12; exactly 10 present still computes; 9 present returns `ratio=None` with a reason naming "9 of 20"; an average of 0 returns `ratio=None` with a reason (never divides by zero)
- [X] T019 [P] [US1] Write `tests/unit/calc/test_ranking.py` for `apply_daily_cap(scores: Mapping[str, float], threshold: float, cap: int) -> CapResult(kept: list[tuple[str, int]], cap_exceeded: list[tuple[str, int]])` (FR-009, FR-010): 14 qualifying scores keep the 10 largest |z| with ranks 1–10 and return the other 4 with ranks 11–14 (spec US1 scenario 2); |z| exactly 2.0 qualifies; |z| 1.999 does not; negative z-scores rank by absolute value; an exact |z| tie at ranks 10/11 keeps the alphabetically earlier ticker; same input always gives the same output
- [X] T020 [P] [US1] Write `tests/unit/test_detection.py` (US1 part) for `run_detection(evaluated_date, tickers, source, now)` returning a `DetectionResult` (no database): a fixed fake `PriceDataSource` with 122 trading days for 4 tickers plus `QQQ`, built so one ticker has a large positive abnormal move on the evaluated day; assert the flagged ticker, its rank and volume ratio, the stored-form numbers for every ticker (return, expected return, abnormal return, z-score, alpha, beta, `estimation_window_days = 120`), the benchmark return, `RULE_VERSION`, and that non-flagged tickers are evaluated records, not skipped (spec US1 scenarios 1 and 4); a variant with 14 qualifying tickers produces 14 evaluated records with all numbers set, 10 anomaly cases, and 4 `cap_exceeded` skips (one for each of the 4 tickers not kept, each also present among the evaluated records and absent from the anomaly cases) with the `reason_detail` format from "Shared decisions"; running it twice on the same input produces equal results (FR-020); the fetch window passed to the source is `fetch_window_start(d)` … `d`
- [X] T021 [P] [US1] Add to `tests/integration/test_storage.py` (marked `integration`): `save_run` writes one `detection_runs` row and all bar-snapshot, evaluated, anomaly and skipped rows in a single transaction; a failure part-way leaves no rows; `load_bar_snapshot(evaluated_date)` returns exactly the saved bars as `Decimal`s; `load_run(evaluated_date)` returns `None` for an unknown day; a second `save_run` for the same `evaluated_date` raises and leaves the first untouched; a result with 14 qualifying stocks stores 14 `evaluated_stock_records`, 10 `anomaly_cases` and 4 `cap_exceeded` `skipped_stock_records` rows whose tickers each also have an evaluated row; `save_run` raises `ValueError` before writing anything when a result breaks a data-model.md cross-entity invariant (a `cap_exceeded` skip without an evaluated row, an `insufficient_history`/`missing_data` skip with one, a ticker both in anomaly cases and skipped, or more than 10 anomaly cases)
- [X] T022 [P] [US1] Write `tests/unit/test_cli.py` for `main(argv) -> int` with the detection service, repository and source replaced by fakes (no DB, no network) per contracts/cli-contract.md: missing or malformed `--date` → 2; a non-trading date such as a Saturday → 2; fresh run → 0 and the last stdout line is exactly `evaluated_date=2026-10-27 status=completed flagged=<n> evaluated=<m> skipped=<k> rule_version=1`, where for a result with 14 qualifying, 80 other evaluated and 6 not-evaluated tickers the line reads `flagged=10 evaluated=94 skipped=10` (cap-exceeded stocks counted in both `evaluated` and `skipped`); same-day re-run with matching snapshot → 0, same summary, no `save_run` call; same-day re-run with a differing snapshot → 3, no `save_run` call; detection raising `BenchmarkUnavailableError` or `TooMuchMissingDataError` → 4, no `save_run` call, nothing on stdout but stderr naming the reason; the source raising `PriceDataError` → 1, no `save_run` call; unexpected exception → 1; the value of `PRICE_DATA_API_KEY` or `DATABASE_URL` never appears in stdout, stderr or logs; a missing `PRICE_DATA_API_KEY` → 1 with a message naming the variable

### Implementation for User Story 1

- [ ] T023 [P] [US1] Implement `simple_returns` in `src/market_anomaly_agent/calc/returns.py` (pass T016)
- [ ] T024 [P] [US1] Implement `MarketModel`, `fit_market_model`, `expected_return`, `abnormal_return`, `z_score` in `src/market_anomaly_agent/calc/market_model.py` with plain NumPy (research.md §4: OLS via closed-form covariance/variance, residual std with the n − 2 denominator, no SciPy/statsmodels) (pass T017)
- [ ] T025 [P] [US1] Implement `VolumeRatioResult` and `volume_ratio` in `src/market_anomaly_agent/calc/volume.py` using `MIN_VOLUME_DAYS` from `rules.py` (pass T018)
- [ ] T026 [P] [US1] Implement `CapResult` and `apply_daily_cap` in `src/market_anomaly_agent/calc/ranking.py`, sorting by `(-abs(z), ticker)` (pass T019)
- [ ] T027 [US1] Implement `src/market_anomaly_agent/detection.py` (pass T020; depends on T010, T011, T023–T026): dataclasses `EvaluatedRecord`, `AnomalyCase`, `SkippedRecord`, `DetectionResult` (evaluated date, rule version, computed-at, benchmark ticker and return, bars fetched per ticker, evaluated/anomaly/skipped records, with numbers converted to `Decimal` per "Shared decisions"); `run_detection(evaluated_date: date, tickers: Sequence[str], source: PriceDataSource, now: datetime) -> DetectionResult` that fetches the window for every ticker and `BENCHMARK_TICKER`, computes returns, fits the market model on up to the 120 most recent paired returns before D, computes abnormal return and z-score for D, creates an `EvaluatedRecord` for every evaluated ticker (including those the cap drops), applies the cap, adds a `cap_exceeded` `SkippedRecord` for each qualifying ticker not kept, and computes the volume ratio over the 20 trading days before D for each kept case. Also define `RunAbortedError`, `BenchmarkUnavailableError` and `TooMuchMissingDataError` per "Shared decisions" (raised from US3's T040). In this story assume complete data; skip handling and the run-level checks are added in US3 (T040)
- [ ] T028 [US1] Implement `src/market_anomaly_agent/storage/repository.py` (pass T021): `save_run(conn, result: DetectionResult) -> None` (first check the data-model.md cross-entity invariants listed in T021 and raise `ValueError` before any write if one is broken; then, in one transaction, insert `detection_runs` first, then `daily_price_bars` with `source = 'twelvedata'` and `fetched_at`, then evaluated, anomaly and skipped rows; no `UPDATE`/`DELETE` statements anywhere in the module), `load_run(conn, evaluated_date) -> StoredRun | None` (run row plus counts of evaluated, flagged and skipped rows), `load_bar_snapshot(conn, evaluated_date) -> dict[str, list[DailyBar]]`
- [ ] T029 [US1] Add `snapshots_match(stored: Mapping[str, Sequence[DailyBar]], fetched: Mapping[str, Sequence[DailyBar]]) -> bool` to `src/market_anomaly_agent/detection.py` with unit tests first in `tests/unit/test_detection.py`: equal when the same tickers, the same `trading_date`s and equal `open`, `close`, `volume` `Decimal`s; different on any changed value, missing bar or extra bar (FR-018, data-model.md `daily_price_bars` validation rule)
- [ ] T030 [US1] Implement `src/market_anomaly_agent/cli.py` `main(argv: Sequence[str] | None = None) -> int` (pass T022) per contracts/cli-contract.md: parse `--date` with `argparse` (exit 2 on usage error or non-trading day via `calendar.is_trading_day`); read `PRICE_DATA_API_KEY`; load the universe tickers with `find_universe_file`/`read_universe_file` from the `universe/` package directory; run `run_detection`; if `load_run` finds an existing row for the date, compare `load_bar_snapshot` with the fresh fetch via `snapshots_match` — mismatch → print a reject message to stderr and exit 3 without writing; match → print the stored summary and exit 0 without writing; otherwise `save_run` and exit 0. `RunAbortedError` from `run_detection` → log the reason to stderr and exit 4 without writing (contracts/cli-contract.md). Configure `logging` to stderr; the last stdout line is always the `key=value` summary; any other exception → log it (no secrets) and exit 1
- [ ] T031 [US1] Add `tests/integration/test_detect_end_to_end.py` (marked `integration`): with a real local PostgreSQL, a fixture universe file and a fake `PriceDataSource` injected into `cli.main`, run quickstart.md scenarios 1–3 in-process — first run exits 0 and stores the expected rows; second run exits 0 and `SELECT count(*) FROM detection_runs WHERE evaluated_date = ...` is still 1 with no new rows in any table; after an `UPDATE` of one `daily_price_bars` row of that snapshot made directly in the test, the third run exits 3 and no table changes. Add the injection seam to `cli.main` (keyword argument `source_factory`) if T030 did not already provide one

**Checkpoint**: US1 works on its own: `uv run detect --date YYYY-MM-DD` evaluates one day, stores it, reproduces it and rejects changed data.

---

## Phase 4: User Story 2 - Frozen, documented universe (Priority: P2)

**Goal**: The real Nasdaq-100 constituent list is captured once from Invesco's QQQ holdings with its
snapshot date and source, contains exactly one Alphabet class (GOOGL), and can never change once
loaded.

**Independent Test**: Inspect the committed `nasdaq100_<snapshot-date>.csv`; confirm 100 rows, one
snapshot date, a cited source, GOOGL present with its share-class note and GOOG absent; run the
loader twice and confirm the second run changes nothing; run it with an edited file and confirm it
refuses.

### Tests for User Story 2 (write first, see them fail) ⚠️

- [ ] T032 [P] [US2] Add to `tests/unit/universe/test_constituents.py` tests for `validate_universe(constituents) -> None` (FR-001, FR-002): passes for a valid fixture; raises `UniverseFileError` when rows have more than one `snapshot_date`, when any `source` is empty, when tickers repeat, when `GOOG` is present, when `GOOGL` is missing its `share_class_note`, or when the row count is not 100 (the count check takes an `expected_count` argument defaulting to 100 so small fixtures can pass `expected_count=len(fixture)`)
- [ ] T033 [P] [US2] Add to `tests/integration/test_storage.py` (marked `integration`) tests for `load_universe_once(conn, constituents)` (FR-003, SC-006): into an empty table it inserts every row; called again with identical rows it changes nothing; called with an added, removed or edited row it raises `UniverseFrozenError` and leaves the table unchanged

### Implementation for User Story 2

- [ ] T034 [US2] Implement `validate_universe` in `src/market_anomaly_agent/universe/constituents.py` (pass T032) and call it from `read_universe_file`'s callers in `src/market_anomaly_agent/cli.py` before detection runs
- [ ] T035 [US2] Implement `UniverseFrozenError` and `load_universe_once` in `src/market_anomaly_agent/storage/repository.py` (pass T033; insert only; compare against existing rows; never `UPDATE`/`DELETE`), and call it from `src/market_anomaly_agent/cli.py` before `run_detection`; a `UniverseFrozenError` exits 1 with a message saying the committed universe file no longer matches the frozen table
- [ ] T036 [US2] **Manual (repository owner)**: download the full holdings file for the Invesco QQQ Trust from its official product page on invesco.com on the snapshot date (research.md §2); keep only equity rows (drop cash, futures and other non-equity lines); drop `GOOG`; set `share_class_note` on the `GOOGL` row to `GOOGL kept, GOOG excluded`; fill `snapshot_date` with the download date and `source` with the exact download URL; save as `src/market_anomaly_agent/universe/nasdaq100_<snapshot-date>.csv` with header `ticker,company_name,snapshot_date,source,share_class_note`. If the result is not exactly 100 rows, stop and investigate before committing. Record the URL and date in the commit message
- [ ] T037 [US2] Add `tests/unit/universe/test_committed_universe.py` that reads the committed file from T036 through `find_universe_file` and passes `validate_universe` with `expected_count=100`, so any later edit to the committed universe fails the unit suite

**Checkpoint**: US1 and US2 both work; detection runs against the real frozen universe.

---

## Phase 5: User Story 3 - Safe handling of gaps and noisy history (Priority: P3)

**Goal**: Stocks with too little history or missing data are skipped with distinct reasons, stocks
with 60–119 days are evaluated on their shorter window, without halting the run or producing
wrong numbers. A benchmark with no usable data (FR-022) or more than 20% of the universe missing
data (FR-023) fails the run with exit 4 and stores nothing, so the day can be run again later.

**Independent Test**: With a fake source where one ticker has 45 days of history, one has 80, one
has no bar on the evaluated day, and the rest are complete: the first and third are skipped with
`insufficient_history` and `missing_data`, the second is evaluated with `estimation_window_days =
80`, everything else is evaluated normally (spec US3 Independent Test).

### Tests for User Story 3 (write first, see them fail) ⚠️

- [ ] T038 [P] [US3] Add to `tests/unit/test_detection.py` (US3 part). Each fixture universe in these tests MUST keep its `missing_data` skips at or below 20% of its tickers (FR-023), e.g. at least 10 tickers when one is missing data, or at least 20 when one fixture combines four `missing_data` cases, so the tests still pass once T040 adds the run-level `TooMuchMissingDataError` check: 45 days of history → `SkippedRecord(reason_code="insufficient_history")` whose `reason_detail` states the 45 paired returns found and the 60 required, and no partial-window evaluation (US3 scenario 1); 80 days → evaluated with `estimation_window_days == 80` and the regression over exactly those 80 paired returns (US3 scenario 2); no bar on the evaluated day, or no bar on the previous trading day → `missing_data` with a reason naming the missing date (US3 scenario 3); a ticker for which the source raises `PriceDataUnavailable` → `missing_data`; a zero residual std → `missing_data` with reason "zero residual variance"; every universe ticker ends up with exactly one of the four outcomes in data-model.md's cross-entity invariants (evaluated only; evaluated + anomaly case; evaluated + `cap_exceeded` skip; `insufficient_history`/`missing_data` skip with no evaluated record); a flagged ticker with only 9 volume days in its 20-day window stays flagged with `volume_ratio=None` and the reason set
- [ ] T039 [P] [US3] Add to `tests/unit/test_detection.py` (after T038, same file): when the benchmark has no bar on the evaluated day, or none on the previous trading day, or the source raises `PriceDataUnavailable` for it, `run_detection` raises `BenchmarkUnavailableError` whose message names the cause, and no result is returned (spec FR-022, Edge Cases); with a 100-ticker universe where 21 tickers end up as `missing_data` (no bar on the evaluated day or `PriceDataUnavailable`), it raises `TooMuchMissingDataError` whose message states `21 of 100`; with exactly 20 `missing_data` tickers it returns a normal result with those 20 skips (spec FR-023); 25 tickers with `insufficient_history` plus 0 `missing_data` do not trigger it (only `missing_data` counts); `PriceDataError` raised by the source for any ticker propagates unchanged

### Implementation for User Story 3

- [ ] T040 [US3] Extend `run_detection` in `src/market_anomaly_agent/detection.py` (pass T038, T039): fetch and check the benchmark first and raise `BenchmarkUnavailableError` when it has no bar on D or on the previous trading day, or the source raises `PriceDataUnavailable` for it (FR-022); per ticker, catch `PriceDataUnavailable` and record `missing_data`; record `missing_data` when the evaluated day's or previous trading day's bar is absent; count paired returns before D and record `insufficient_history` below `MIN_WINDOW_DAYS`, otherwise use the most recent `min(n, MAX_WINDOW_DAYS)`; catch the `ValueError` from `z_score` and record `missing_data`; after all tickers are processed and before building the result, raise `TooMuchMissingDataError` when `missing_count * 100 > len(tickers) * MAX_MISSING_DATA_PERCENT` (FR-023); never catch `PriceDataError`; log each skip at INFO with ticker and reason, and each abort at ERROR with its reason (FR-012, FR-017)
- [ ] T041 [US3] Extend `tests/integration/test_detect_end_to_end.py` with quickstart.md scenarios 4 and 5: a fixture universe containing a ticker with fewer than 60 trading days of history produces a stored `skipped_stock_records` row with `reason_code = 'insufficient_history'`, and every other ticker is in `evaluated_stock_records` (and in `anomaly_cases` where flagged); a fake source with no benchmark bar on the evaluated day makes `cli.main` exit 4 and leaves every table without a row for that day; a 100-ticker fixture universe with 21 tickers missing the evaluated day's bar exits 4 and stores nothing, while one with exactly 20 missing exits 0 and stores 20 `missing_data` skips; after each exit-4 case, running the same day again with complete data exits 0 and stores the day normally

**Checkpoint**: All three user stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Checks that cover the whole feature

- [ ] T042 [P] Run `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy` and fix every finding in `src/` and `tests/` (plan.md Testing; constitution Principle VIII type hints)
- [ ] T043 [P] Add a test in `tests/unit/test_no_network.py` that sets an autouse fixture in `tests/conftest.py` replacing `socket.socket.connect` with a function that raises, so any unit test that tries to open a network connection fails (constitution Principle VIII: the test suite MUST NOT make network calls); integration tests are allowed to reach only the local database, so the fixture skips tests marked `integration`
- [ ] T044 [P] Add `README.md` at the repository root with a short project description, the disclaimer "Experimental research project, not investment advice." (constitution Principle VI), and pointers to `specs/001-nasdaq100-anomaly-detection/quickstart.md` for setup and validation
- [ ] T045 Run quickstart.md end to end against a real Twelve Data key and local PostgreSQL (scenarios 1–4, including the by-hand recomputation for one flagged ticker in scenario 1) and record the evaluated date, exit codes, run time (about 13 minutes expected) and the hand-check result in `specs/001-nasdaq100-anomaly-detection/checklists/quickstart-run.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup; blocks all user stories.
- **Foundational corrections (Phase 2b)**: depends on Phase 2; blocks all user stories (T027, T028 and T030 use the corrected schema and adapter).
- **US1 (Phase 3)**: depends on Foundational (Phases 2 and 2b) only.
- **US2 (Phase 4)**: depends on Foundational; T034/T035 edit `cli.py` from T030, so do them after T030 if US1 and US2 run in parallel. T036 (manual capture) can be done at any time.
- **US3 (Phase 5)**: depends on Foundational and on `detection.py` from T027, which it extends.
- **Polish (Phase 6)**: after the stories you want to ship. T045 needs T036's real universe file.

### User Story Dependencies

- **US1 (P1)**: independent; tested with fixture universes and a fake price source.
- **US2 (P2)**: independently testable (its tests touch only `constituents.py` and the repository); wiring into the CLI follows US1.
- **US3 (P3)**: builds on US1's `run_detection`; its tests are independent of US2.

### Within Each Phase

- Test tasks first; each MUST fail before its implementation task starts (constitution Principle VIII).
- `calc/` functions before `detection.py`; `detection.py` and `repository.py` before `cli.py`.

### Parallel Opportunities

- Phase 1: T003, T004, T005 together after T001/T002.
- Phase 2: tests T006–T009 together; then T010, T011, T013 together; T012 after T011; T014 then T015.
- Phase 2b: tests T046 and T047 together, with T050; then T048 and T049 together (different files).
- US1: tests T016–T022 together; then T023–T026 together; then T027, T028 (different files) together; then T029, T030, T031 in order.
- US2: T032 and T033 together; T036 any time.
- US3: T038 and T039 touch the same test file, so write them one after the other.

---

## Parallel Example: User Story 1

```bash
# All US1 calculation tests at once (different files):
Task: "T016 Write tests/unit/calc/test_returns.py"
Task: "T017 Write tests/unit/calc/test_market_model.py"
Task: "T018 Write tests/unit/calc/test_volume.py"
Task: "T019 Write tests/unit/calc/test_ranking.py"

# Then all US1 calculation modules at once:
Task: "T023 Implement calc/returns.py"
Task: "T024 Implement calc/market_model.py"
Task: "T025 Implement calc/volume.py"
Task: "T026 Implement calc/ranking.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup, then Phase 2 Foundational and Phase 2b corrections.
2. Phase 3 (US1) against a fixture universe file.
3. **Stop and validate**: `uv run pytest`, `uv run pytest -m integration`, then quickstart.md scenarios 1–3 with a fixture or the real universe.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 → one evaluated day stored, reproduced and rejected on changed data (MVP).
3. US2 → real frozen universe committed and enforced.
4. US3 → gaps and short histories handled; benchmark outages and vendor-wide gaps fail the run without storing anything.
5. Polish → lint, type checks, network guard, README, real quickstart run.

---

## Notes

- Claude Code does not commit; the repository owner reviews and commits each change (constitution Development Workflow). Commit messages follow Conventional Commits.
- The public website, its nasdaq.com links and the Twelve Data permission request (plan.md Constitution Check VI, research.md §1) belong to a later feature; nothing in this feature publishes data.
- `daily_price_bars` is a per-run snapshot keyed by `(evaluated_date, ticker, trading_date)` (data-model.md), because dividend adjustment restates earlier days; a re-run for a day evaluated weeks ago will usually exit 3 after any intervening ex-dividend date, which is the behavior FR-018 asks for.
