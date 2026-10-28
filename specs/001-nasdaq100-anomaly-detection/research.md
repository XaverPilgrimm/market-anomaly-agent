# Research: Nasdaq-100 Universe and Daily Anomaly Detection

**Date**: 2026-10-28
**Feature**: [spec.md](./spec.md)

This document resolves the open research tasks named in the `/speckit-plan` technical context:
a free price-data source, an authoritative universe source, the market-calendar library, the
regression/statistics approach, and the storage/migration approach. Each entry gives a decision,
the rationale, and the alternatives considered.

## 1. Free daily price-data source

**Decision**: Financial Modeling Prep (FMP), free "Basic" plan, accessed through the
`historical-price-eod/full` (or equivalent dividend-adjusted) REST endpoint, behind a small
`PriceDataSource` interface (see [contracts/price-data-source-interface.md](./contracts/price-data-source-interface.md)).

**Rationale**:

| Criterion | FMP free Basic plan |
|---|---|
| Daily coverage for ~100 tickers + 1 benchmark | 250 requests/day, no credit card required, perpetual (not a trial) — fetching the 101 tickers needed for one evaluated day costs at most 101 requests/day, each request returning the full needed date range in one call |
| One-time backfill of ≥ 200 trading days | Each ticker's historical endpoint accepts a `from`/`to` date range and returns the whole range in a single request, so a ~200-trading-day backfill for all 101 symbols still costs ~101 requests total, not 101 × 200 |
| Split- and dividend-adjusted daily bars | The historical EOD response includes `close` (split-adjusted) and `adjClose` (split- **and** dividend-adjusted), plus `open` and `volume`, which is exactly what FR-004/FR-005 need |
| Official API, not scraping | Documented REST API with an API key; not a scraping technique |
| Terms permit automated access and storing fetched bars | FMP's Terms of Service require cached/received data to be deleted only *on termination* of the account, implying storage is permitted while the account stays active (ordinary internal use); display/redistribution of raw data to third parties needs a separate commercial agreement, which this project does not do — the public site (a later feature) would publish derived numbers (z-scores, classifications), never raw vendor bars, consistent with the constitution's Data and Compliance principle |

**Alternatives considered and rejected**:

- **Tiingo** — best-fitting rate limits (50/hour, 1,000/day) and clean adjusted-close EOD data, but
  its free Starter/Trial terms explicitly state that users "do not allow you to write, save,
  archive, back up, or otherwise retain Tiingo Data in any persistent or durable storage." This
  directly conflicts with FR-012/FR-014/FR-018, which require the bars a run used to be stored so
  re-runs are reproducible. Disqualified on terms, not capability.
- **Alpha Vantage** — the free tier is capped at 25 requests/day and 5/minute, and as of 2026 its
  `TIME_SERIES_DAILY_ADJUSTED` endpoint (the only one that returns dividend-adjusted closes) has
  been moved behind a paid plan; the free tier's unadjusted-only data would not satisfy FR-004.
  Disqualified on capability.
- **Polygon.io (now "Massive")** — free tier is 5 calls/minute with only 2 years of EOD history
  (enough for the backfill), but the brand is mid-rebrand (`polygon.io/terms` now redirects to
  `massive.com`) and its storage terms for the free tier were not clearly documented at the time
  of this research. Set aside due to documentation/stability risk, not a hard capability gap.
- **Twelve Data** — 800 requests/day/8 per minute would be plenty, but its free-tier license is
  explicitly scoped to "evaluation and development only," which reads as excluding the kind of
  standing, if small, daily production job this feature describes. Disqualified on terms.
- **Finnhub** — generous call volume (60/minute), but historical daily candles were moved to paid
  tiers and return HTTP 403 on a free key. Disqualified on capability.

**Vendor-agnostic mitigation**: FMP sits behind a small `PriceDataSource` interface
(see contracts), so if its terms, rate limits, or free-tier availability change later, only the
adapter needs to be replaced — nothing in `calc/`, `detection.py`, or storage changes.

## 2. Authoritative Nasdaq-100 universe source

**Decision**: Invesco's own published full holdings file for the Invesco QQQ Trust ETF (ticker
QQQ), downloaded once by hand from Invesco's official QQQ product page (invesco.com) on the
snapshot date, and committed to the repository as `nasdaq100_<snapshot-date>.csv` with columns
`ticker, company_name, snapshot_date, source`.

**Rationale**: QQQ's prospectus requires it to fully replicate the Nasdaq-100 Index, so its daily
disclosed holdings are the index's constituents by construction. Invesco is the fund issuer, so
its own published holdings file is the primary source — closer to the index than any third-party
aggregator (Barchart, stockanalysis.com, etc.) that merely mirrors it. QQQ's current holdings
count (104) is above 100 because it includes multiple share classes of a few constituents (e.g.,
Alphabet's GOOGL and GOOG), which is exactly the case FR-002 already dedupes. Fetching this file is
a one-time manual action, not a daily automated call, so it has no impact on the price-data rate
budget above; the exact download URL and the chosen snapshot date are recorded as a task in
`tasks.md` and stamped into the committed file's `source`/`snapshot_date` columns at the time the
file is actually captured.

**Alternatives considered and rejected**:

- **Nasdaq's own index page** (nasdaq.com) — authoritative in principle, but its constituent list
  is only browsable as a web page, not published as a clean, citable file; reproducing it would
  require scraping an unstructured page rather than reading a fund issuer's disclosed holdings.
- **Third-party aggregators** (Barchart, stockanalysis.com, ETF Database, MarketXLS) — convenient
  CSV exports, but they are downstream copies of the same underlying holdings disclosure, not the
  primary source, and several gate full exports behind a paid tier.

## 3. US market calendar library

**Decision**: `pandas_market_calendars`, using its `"NASDAQ"` calendar and `America/New_York`.

**Rationale**: `pandas_market_calendars` mirrors the calendars from `exchange_calendars` (the
actively maintained successor to the old `trading_calendars` package) and adds a pandas-native
`schedule`/`valid_days` API, which is a natural fit for resolving "the previous trading day" and
"is this a trading day" without hand-rolling holiday logic. Both libraries are actively maintained
in 2026 and correctly treat early-close sessions (e.g., the day after Thanksgiving) as regular
trading days with a shortened session — exactly the behavior FR-013 requires, since this feature
only needs the trading-day boundary, not the exact closing time.

**Alternatives considered and rejected**: `exchange_calendars` directly — equally correct, but a
lower-level, less pandas-idiomatic API for the limited set of operations this feature needs
(trading-day membership, previous/next trading day). Using `pandas_market_calendars` keeps the
dependency list minimal (Principle II) since it is already pandas-based and the project has no
other reason to depend on `exchange_calendars` directly.

## 4. Market-model regression and z-score computation

**Decision**: Plain NumPy, no `scipy`/`statsmodels`. Alpha and beta are estimated with ordinary
least squares via `numpy.linalg.lstsq` (or the equivalent closed-form covariance/variance
formulas); the residual standard deviation is computed manually as
`sqrt(sum(residuals**2) / (n - 2))`, matching FR-008's n − 2 degrees-of-freedom requirement.

**Rationale**: A single-variable OLS regression, its residuals, and a residual standard deviation
are a handful of array operations — adding `scipy` or `statsmodels` for this would be exactly the
kind of avoidable dependency and indirection Principle II ("Simplest Solution First") warns
against, and would make the hand-checked example tests (SC-002) harder to verify by hand, since
the closed-form formulas are simpler to reproduce with a calculator than a packaged regression
routine's internals.

**Alternatives considered and rejected**: `statsmodels.OLS` — more features (p-values, full
regression diagnostics) than this feature needs; `scipy.stats.linregress` — convenient for
simple-linear-regression alpha/beta, but still an extra dependency for one function, and it does
not directly expose the residual standard deviation with the exact n − 2 denominator FR-008 names.

## 5. Database migrations and persistence

**Decision**: Plain, numbered `.sql` migration files under `storage/migrations/`, applied by a
small custom Python runner that tracks applied migrations in a `schema_migrations` table; data
access through `psycopg` (v3) with hand-written SQL, no ORM.

**Rationale**: The constitution's Technology Constraints already call for "PostgreSQL with plain
SQL migration files; no ORM unless clearly needed," and this feature's schema (five small tables,
no deep object graphs) does not clear that bar. A ~40-line runner that reads `migrations/*.sql` in
order and records what has been applied is simpler to review and test than adopting a full
migration framework (Alembic, etc.) for a one-person project at this stage (Principle II).

## 6. HTTP client for the price-data adapter

**Decision**: `requests`, used synchronously and sequentially (no concurrency) inside the FMP
adapter, since a single run makes at most ~101 HTTP calls once per explicit evaluated day — well
within a few minutes even run one at a time, and concurrency would add complexity (connection
pooling, partial-failure handling) this feature does not need. A simple client-side delay/backoff
keeps the run under FMP's 250-requests/day ceiling and reacts to HTTP 429 by waiting and retrying
rather than failing the whole run on a single rate-limit hit.

**Alternatives considered and rejected**: `httpx` — equally capable, but its main advantage
(async) is not needed here; `requests` is the more minimal, more widely understood dependency for
a purely synchronous batch job.
