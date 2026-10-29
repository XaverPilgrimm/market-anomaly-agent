# Implementation Plan: Nasdaq-100 Universe and Daily Anomaly Detection

**Branch**: `001-nasdaq100-anomaly-detection` | **Date**: 2026-10-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-nasdaq100-anomaly-detection/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Before each US market open, evaluate the previous trading day for every Nasdaq-100 stock: compute
a market-model abnormal return and z-score relative to a Nasdaq-100 benchmark, flag up to 10
stocks whose |z-score| ≥ 2, compute a volume ratio for each flagged case, and persist everything
(flagged, evaluated-but-not-flagged, and skipped) with full provenance so re-runs are reproducible
and never silently duplicate or overwrite a prior result. Delivered as pure, hand-testable
calculation functions plus a thin CLI (`uv run detect --date YYYY-MM-DD`) and a small PostgreSQL
schema, with the frozen Nasdaq-100 universe captured once from Invesco's QQQ holdings and daily
adjusted bars fetched from Twelve Data's free Basic plan (see research.md).

## Technical Context

**Language/Version**: Python 3.12+, managed with `uv`

**Primary Dependencies**: `numpy` (returns, OLS regression, z-score — no `scipy`/`statsmodels`,
see research.md §4); `pandas_market_calendars` (NASDAQ trading-day calendar, `America/New_York`,
see research.md §3); `requests` (price-data HTTP adapter, see research.md §6); `psycopg` (v3,
PostgreSQL driver, no ORM)

**Storage**: PostgreSQL, plain numbered `.sql` migration files applied by a small custom runner
(no Alembic/ORM — see research.md §5); schema detailed in [data-model.md](./data-model.md)

**Testing**: `pytest` — unit tests for every calculation and the detection orchestration run with
no database and no network (price data always a test double); integration tests against a local
PostgreSQL are marked `@pytest.mark.integration` and excluded by default; `ruff` and `mypy
--strict` for lint/type checking

**Target Platform**: Linux; developed and run locally inside the existing "agent-box" container
during this feature, reaching PostgreSQL (started via `podman compose`) at
`host.containers.internal`; packaging the application itself into the eventual daily-job
container image and deploying it to Azure Container Apps are out of scope for this feature (per
the spec) and are left to a future deployment feature

**Project Type**: Single project — a small library of pure calculation functions plus a thin CLI
entry point (no web/mobile component)

**Performance Goals**: No hard SLA; a full run (≈101 symbols, one evaluated day) should complete
well inside the pre-market window — practically, about 13 minutes, dominated by ~101 sequential
HTTP calls to the price-data vendor at the free plan's limit of 8 requests per minute

**Constraints**: No paid API or data subscription (FR-019/SC-003); must stay within the chosen
price-data vendor's free-tier rate limits (research.md §1); detection must be exactly reproducible
given unchanged input (FR-020) and must fail without any write when input data has changed since
a prior run for the same day (FR-018); `UV_PROJECT_ENVIRONMENT=/home/dev/.venvs/market-anomaly-agent`
keeps the uv virtual environment outside the project directory so the host and the agent-box
container never share a `.venv`

**Scale/Scope**: ~101 symbols (100 frozen universe constituents + 1 benchmark ETF); 60–120
trailing trading days of history per symbol per evaluated day; exactly one evaluated day per CLI
invocation (FR-021)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / constraint | Assessment |
|---|---|
| I. Deterministic Numbers, LLM for Judgment Only | PASS — every number (return, alpha, beta, abnormal return, z-score, volume ratio, ranking) is a pure, tested function; this feature contains no LLM call at all (the LLM-judgment step is explicitly a separate, later feature) |
| II. Simplest Solution First | PASS — no agent loop; minimal dependency list (numpy, pandas_market_calendars, requests, psycopg); no ORM; no framework beyond a thin CLI |
| III. Research Integrity | PASS — universe frozen and committed with snapshot date/source (FR-001–003); every stored record carries a timestamp and a rule version (FR-014); decisions (flagged/evaluated/skipped) are stored before any downstream outcome exists, matching "stored with a timestamp before its outcome is known"; skipped/capped stocks are recorded, never dropped (FR-017); re-runs cannot silently rewrite history (FR-018) |
| IV. Security | PASS — DB credentials and the price-data API key are read from the environment only, never logged (cli-contract.md); this feature has no agent/tool-calling surface yet, so "agent tools read-only" does not apply here but is not violated either |
| V. Cost Control | PASS (adapted) — no paid API is used, so no spending cap is needed for this feature; the free-tier *rate limit* is still enforced in code (adapter-level backoff, research.md §6), which is the spirit of this principle even without a dollar cap |
| VI. Data and Compliance | PASS for this feature, which publishes nothing — the candidate vendors' terms on storage, public display of derived data and attribution are quoted and compared in research.md §1. Open item for the public site (a later feature): the site shows no prices and no per-stock returns. Each case instead links to the stock's public historical quote page, `https://www.nasdaq.com/market-activity/stocks/{ticker}/historical`, with `{ticker}` in lowercase (e.g. `.../stocks/aapl/historical`). The site shows only z-scores, classifications (flagged / evaluated / skipped), the agent's explanations and aggregated group results. Even these are derived from vendor data, so they stay unpublished until the written permission listed in research.md §1 is on file |
| VII. Observability | PASS (scoped) — every run is recorded as a `detection_runs` row with its rule version and computation timestamp (FR-014); this feature has no agent, so the full "tool calls, token usage" trace in Principle VII targets the future agent feature, not this one |
| VIII. Testing and Quality | PASS — test-first pure functions with hand-checked examples (SC-002); price-data vendor always mocked in tests; `mypy --strict` and `ruff`; small, reviewable scope (one feature, three user stories) |
| Technology Constraints — Python 3.12+/uv | PASS |
| Technology Constraints — LangGraph/LangChain/Azure OpenAI | N/A for this feature — no LLM/agent component is in scope here; applies to the future "agent" feature |
| Technology Constraints — Brave Search / X API | N/A for this feature — evidence gathering is a separate, future feature |
| Technology Constraints — PostgreSQL, plain SQL migrations, no ORM | PASS |
| Technology Constraints — one container image for the daily job / Azure Container Apps | Deferred — this feature defines the CLI the eventual image will run (`uv run detect --date ...`); building and deploying that image is explicitly out of scope per the spec |
| Technology Constraints — runs locally with podman compose | PASS at this feature's scope — `compose.yaml` runs PostgreSQL; the application itself runs in the existing agent-box dev container, not yet as its own composed service (full packaging is the deployment feature's job) |
| Technology Constraints — public site static | N/A for this feature |

**Result**: No violations requiring justification. The Complexity Tracking table below is empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-nasdaq100-anomaly-detection/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   ├── cli-contract.md
│   └── price-data-source-interface.md
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
pyproject.toml            # uv project; ruff + mypy --strict config; pytest markers
compose.yaml               # PostgreSQL only
.env.example

src/market_anomaly_agent/
├── __init__.py
├── cli.py                           # `detect` entry point (contracts/cli-contract.md)
├── calc/
│   ├── __init__.py
│   ├── returns.py                   # simple close-to-close adjusted return (FR-005)
│   ├── market_model.py              # OLS alpha/beta, abnormal return, residual std, z-score (FR-006–008)
│   ├── volume.py                    # volume ratio with the 20-day/min-10-day rule (FR-011)
│   └── ranking.py                   # cap-of-10 + alphabetical tie-break (FR-010)
├── calendar.py                       # US market trading-day resolution, America/New_York (FR-013)
├── universe/
│   ├── __init__.py
│   ├── constituents.py               # loads the frozen snapshot file (FR-001–003)
│   └── nasdaq100_<snapshot-date>.csv  # the committed frozen universe (ticker, name, date, source)
├── data/
│   ├── __init__.py
│   ├── source.py                     # PriceDataSource Protocol (contracts/price-data-source-interface.md)
│   └── twelvedata_source.py           # Twelve Data adapter implementing PriceDataSource
├── storage/
│   ├── __init__.py
│   ├── db.py                         # psycopg connection helper
│   ├── migrate.py                     # tiny migration runner
│   ├── migrations/
│   │   └── 0001_init.sql              # tables from data-model.md
│   └── repository.py                   # persistence + the FR-018 compare-before-write check
└── detection.py                        # orchestrates one run: fetch → compute → compare/reject → persist

tests/
├── unit/
│   ├── calc/
│   │   ├── test_returns.py
│   │   ├── test_market_model.py
│   │   ├── test_volume.py
│   │   └── test_ranking.py
│   ├── test_calendar.py
│   └── test_detection.py              # orchestration logic against a fake PriceDataSource
└── integration/
    └── test_storage.py                 # @pytest.mark.integration, needs local PostgreSQL
```

**Structure Decision**: Single project (Option 1). A small internal library of pure calculation
and orchestration modules (`src/market_anomaly_agent/`) with one thin CLI entry point; no web or
mobile component. The price-data vendor and the US market calendar are each isolated behind their
own module/interface so they can be swapped without touching `calc/` or `storage/`.

## Complexity Tracking

> No entries — the Constitution Check above found no violations to justify.
