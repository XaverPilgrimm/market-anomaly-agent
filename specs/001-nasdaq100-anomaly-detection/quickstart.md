# Quickstart: Nasdaq-100 Universe and Daily Anomaly Detection

**Feature**: [spec.md](./spec.md) | **Data model**: [data-model.md](./data-model.md) | **CLI contract**: [contracts/cli-contract.md](./contracts/cli-contract.md)

This is a validation guide, not an implementation guide — it proves the feature works end-to-end
once built. Full code (models, services, migrations, test suites) belongs in `tasks.md` and the
implementation itself.

## Prerequisites

- `uv` installed.
- A PostgreSQL instance reachable at `DATABASE_URL` (locally: `podman compose up -d db`, see
  below).
- An API key for the price-data vendor (Twelve Data free Basic plan — research.md §1), exported as
  `PRICE_DATA_API_KEY`.
- The frozen universe file committed at `src/market_anomaly_agent/universe/nasdaq100_<snapshot-date>.csv`
  (see research.md §2 for how it was captured).

## One-time local setup

```bash
# uv virtual environment lives outside the project folder, so the host and the
# agent-box container never share a .venv:
export UV_PROJECT_ENVIRONMENT=/home/dev/.venvs/market-anomaly-agent
uv sync

# PostgreSQL only — the application itself runs inside the existing agent-box container:
podman compose up -d db

# DATABASE_URL points at the compose service via the container-to-host bridge:
export DATABASE_URL="postgresql://market_anomaly_agent:<password>@host.containers.internal:5432/market_anomaly_agent"
export PRICE_DATA_API_KEY="<your Twelve Data API key>"

uv run python -m market_anomaly_agent.storage.migrate   # applies storage/migrations/*.sql in order
```

## Validation scenario 1 — a known historical day, hand-checked

1. Pick a past trading day with publicly known, unusual single-stock moves (e.g., a day with a
   large post-earnings gap for one Nasdaq-100 name).
2. Run:
   ```bash
   uv run detect --date 2026-10-27
   ```
3. Expect exit code `0` and a summary line like
   `evaluated_date=2026-10-27 status=completed flagged=<n> evaluated=<m> skipped=<k> rule_version=1`.
4. By hand (spreadsheet or calculator), recompute the return, alpha/beta, abnormal return, and
   z-score for one flagged ticker from the stored `daily_price_bars` rows, using the formulas in
   FR-005–FR-008. The hand-computed values must match the stored `evaluated_stock_records` row
   for that ticker and day (this is the core proof behind SC-001/SC-002).

## Validation scenario 2 — idempotent re-run (FR-018, FR-020)

1. Run the same command again: `uv run detect --date 2026-10-27`.
2. Expect exit code `0` again, the same summary numbers, and no new row in `detection_runs` for
   that date (confirm with `SELECT count(*) FROM detection_runs WHERE evaluated_date = '2026-10-27'`
   — must still be `1`).

## Validation scenario 3 — reject on changed input data (FR-018, Clarifications Q1)

1. Manually alter one `daily_price_bars` row of that run's stored snapshot
   (`evaluated_date = '2026-10-27'`), simulating a vendor correction that happened after the
   original run.
2. Run `uv run detect --date 2026-10-27` again.
3. Expect exit code `3`, no change to the altered row, and no new/changed row in any other table
   for that date.

## Validation scenario 4 — insufficient history and missing data (User Story 3)

1. Pick an evaluated day for which the universe includes at least one ticker with fewer than 60
   trading days of history before that day (or construct one in a test fixture).
2. Run detection for that day.
3. Expect that ticker to appear in `skipped_stock_records` with `reason_code = 'insufficient_history'`,
   and every other eligible ticker to appear in either `evaluated_stock_records` or
   `anomaly_cases` as appropriate.

## Running the test suite

```bash
uv run pytest -m "not integration"   # unit tests: pure functions + calendar + orchestration, no DB/network
podman compose up -d db
uv run pytest -m integration         # integration tests: real local PostgreSQL, price data still mocked
uv run ruff check .
uv run mypy .
```

No test ever calls the real price-data API (constitution Principle VIII) — the `PriceDataSource`
is always a test double in `pytest`.
