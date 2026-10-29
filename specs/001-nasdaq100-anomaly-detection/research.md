# Research: Nasdaq-100 Universe and Daily Anomaly Detection

**Date**: 2026-10-28
**Feature**: [spec.md](./spec.md)

This document resolves the open research tasks named in the `/speckit-plan` technical context:
a free price-data source, an authoritative universe source, the market-calendar library, the
regression/statistics approach, and the storage/migration approach. Each entry gives a decision,
the rationale, and the alternatives considered.

## 1. Free daily price-data source

**Revised**: 2026-10-29. The first version of this section chose Financial Modeling Prep (FMP).
A re-read of FMP's terms showed that it prohibits displaying its data, or data derived from it,
on websites without a separate agreement, prohibits naming FMP as the source without consent,
and restricts copying or downloading content without written approval. The public site (a later
feature) will show derived metrics, so the criteria below gain a public-display criterion and all
candidates are re-checked against their current terms.

**Decision**: **No free source meets all criteria.** For internal computation in this feature,
use **Twelve Data's free Basic plan** (`/time_series`, `interval=1day`, `adjust=all`) behind the
`PriceDataSource` interface (see [contracts/price-data-source-interface.md](./contracts/price-data-source-interface.md)).
Showing any metric derived from it on the public site is **blocked until Twelve Data grants written
permission** (see "What needs written permission" below). This feature does not publish anything,
so the block does not stop this feature; it gates the later website feature.

### Criteria

| # | Criterion |
|---|---|
| C1 | Free, perpetual plan (no paid API — spec success criterion) |
| C2 | Daily coverage for ~100 tickers + 1 benchmark, and a one-time backfill of ≥ 200 trading days, within the free rate limits |
| C3 | Split- **and** dividend-adjusted daily bars with open and volume (FR-004/FR-005) |
| C4 | Official API, not scraping |
| C5 | Terms permit automated access and **persistent storage** of fetched bars, so re-runs are reproducible (FR-012/FR-014/FR-018) |
| C6 | **New**: terms permit showing derived metrics (returns, abnormal returns, z-scores, paper P&L) on a public, non-commercial website, ideally with attribution to the source |

### Summary

| Candidate (free plan) | C2 capacity | C3 adjusted | C5 storage | C6 public derived display | Attribution |
|---|---|---|---|---|---|
| FMP Basic | Yes (250/day) | Yes | **No** without written approval | **No** | **Prohibited** without consent |
| Tiingo Starter | Yes (50/h, 1,000/day) | Yes | **No** (transient only) | **No** | Not granted |
| Twelve Data Basic | Yes (8/min, 800/day) | Yes (`adjust=all`) | Internal use only; plan is a free trial | **Unclear** — "Derived Data" clause, but plan labelled "Internal non-display usage" | Only under separate agreement |
| Alpha Vantage free | **No** (25/day, 100 bars) | **No** (adjusted is premium) | Not restricted | Not restricted for non-commercial use, not granted either | Silent |
| Massive (ex-Polygon) free | Yes (5/min, 2 years) | Split only | Personal use | **No** — derived works named explicitly | **No** without consent |
| Finnhub free | **No** (no daily candles) | — | Delete when subscription ends | **No** — "derived results" named explicitly | No trademark rights |
| EODHD free | **No** (20 calls/day) | Yes | Yes, private use | **No** — "repackaged form" | Silent |

All quotes below were checked against the live pages on 2026-10-29, except where noted.

### Financial Modeling Prep (FMP)

Terms: <https://site.financialmodelingprep.com/terms-of-service> ("Last updated: August 1, 2023").

- **Storage / copying** (§2.2.1 Personal Use): "The Customer may not copy or download any content
  from the Services except with the prior written approval of FMP." Storage is also implied by
  §6.3 Data Deletion: "Upon termination of this Agreement, Customer must delete all Data it has
  received from FMP under all applicable Order Forms, including data cached". §2.8 additionally
  requires: "Customer will notify FMP of the IP and domain aliases of any location where data is
  stored or processed."
- **Public display of derived data** (§2.2.2 Data Display): "Without a specific agreement with FMP,
  customers are prohibited from showcasing FMP Services or Data on platforms including but not
  limited to websites, blogs, software products, or applications designed for utilization by
  multiple individuals, irrespective of whether such usage is complimentary or paid". §2.6.1(i):
  Customer shall not "resell, sublicense, distribute or otherwise provide access to The Services,
  or data or information contained in or derived from The Services, to any third party". §2.2.1:
  the Customer may not "use the Services to host, share, display, or provide content for others."
- **Attribution** (§10.4): "Customer may not identify FMP as the source of the Data to any third
  party without FMP’s prior written consent". §2.6.1(iii) also bars using FMP's trademarks or
  trade names "for any purpose without the express written consent of FMP."

**Verdict**: fails C5 and C6 and forbids attribution. The first version of this section read §6.3
as implying storage is allowed; §2.2.1 requires written approval to download content at all.
Rejected.

### Tiingo (Starter)

Terms: <https://app.tiingo.com/tos/>. Pricing: <https://www.tiingo.com/about/pricing>.
The terms page returned HTTP 403 to automated fetches on 2026-10-29; the storage passage below is
the one recorded in the first version of this research and still shown in the search-engine
extract of that page. Re-check it in a browser before relying on it.

- **Storage**: "If you use a Starter Plan or any free or paid trial plan, you may not write, save,
  archive, back up, or otherwise retain Tiingo Data in any persistent or durable storage. You may
  process Tiingo Data only transiently in volatile memory or in a temporary, non-persistent cache."
- **Public display** (pricing page; the Starter licence row reads "Internal Use Only"): "Internal
  use means you may only use the data for your own personal use and you may not display or share
  the data with another person or organization".
- **Attribution**: no clause granting it was found; display to others requires a redistribution
  licence ("For redistribution use, our pricing is explicit and predictable on our Product pages.").

**Verdict**: fails C5 and C6. Rejected.

### Twelve Data (Basic)

Terms: <https://twelvedata.com/terms> ("Last updated: January 1, 2026").
Pricing: <https://twelvedata.com/pricing>.

- **Storage** (§2.2(a)): Customer may "Access, receive, process, and store Data solely for Internal
  Use (or as otherwise permitted by your Subscription Tier or add-ons)". §2.3(g): Customer shall
  not "Store or cache Data beyond permitted timeframes specified in the Documentation". §16.1:
  "Customer may retain Data only: (a) For duration permitted by subscription". §16.2: "Upon
  termination or expiration: (a) All Data must be deleted within 30 days".
- **Plan scope**: the pricing page labels Basic "Internal non-display usage" and its FAQ says "Free
  trial is available under the Basic plan." The terms define "Free Trial" as "evaluative use of the
  Platform for testing purposes prior to purchasing a subscription", and §5.6 says "(a) Free Trial
  limited to evaluation purposes only (b) No commercial use permitted during Free Trial". §2.3(l):
  Customer shall not "Use Free Tier data for commercial purposes".
- **Public display of derived data**: §1 defines "'Derived Data' means data created by Customer
  from the Data, provided such data cannot be reverse-engineered to arrive at the underlying Data."
  §2.2(c) grants the right to "Create Derived Data that cannot be reverse-engineered to recreate
  the original Data", and §6.2 says "Customer retains rights to Derived Data created in compliance
  with this Agreement." Against that, §2.2(e) allows Customer to "Redistribute or provide external
  display of Data only if and as expressly authorized by a Redistribution Rights Add-On or separate
  written agreement with Twelve Data (including compliance with attribution requirements)", and
  §17.1(g) prohibits: "Create derivative works for redistribution".
- **Attribution**: §2.4: "Certain Subscription Tiers or Data Add-ons may permit limited
  redistribution or external display of Data, subject to additional terms, attribution
  requirements, exchange rules, and separate agreements." §6.1(b) keeps "Trademarks, logos, and
  branding" with Twelve Data.

**Verdict**: meets C1–C4. C5 is met for internal, evaluative use, which fits building and testing
this feature but not an open-ended daily production job. C6 is unclear: the Derived Data clause is
the only one among all candidates that gives the customer rights to derived numbers, but §17.1(g)
and the "non-display" plan label cut the other way. A full daily return series plus one known
close lets a reader rebuild adjusted closes, so published *returns* are the metric most likely to
count as reverse-engineerable; z-scores, abnormal returns without the fitted alpha/beta, and paper
P&L are less so. Best available option for internal computation; public display needs permission.

### Alpha Vantage (free)

Terms: <https://www.alphavantage.co/terms_of_service/> (PDF, no date shown).
Documentation: <https://www.alphavantage.co/documentation/>. Limits: <https://www.alphavantage.co/support/>.

- **Storage**: the terms contain no storage, caching or deletion clause.
- **Public display** (§2.a): the licence is "for personal, non-commercial use, unless you and Alpha
  Vantage have agreed otherwise in writing". Use is commercial if "(i) You intend to use the Alpha
  Vantage Platform for any purpose that goes beyond investment analysis, research, testing,
  monitoring, and any other activities that are private and individual in nature" or "(iii) You
  plan to use or provide information accessed through the Alpha Vantage Platform as part of any
  type of commercial activity that allows individuals or entities other than User to access
  information directly or indirectly". A non-commercial public site is not caught by (iii), but
  a public site is arguably not "private and individual in nature" under (i).
- **Attribution**: the terms are silent.
- **Capability**: free keys get "25 API requests per day"; `TIME_SERIES_DAILY` is "raw
  (as-traded)", and for free keys "`compact` returns only the latest 100 data points" ("The "full"
  outputsize is available to premium keys"); `TIME_SERIES_DAILY_ADJUSTED` is "a premium API
  function".

**Verdict**: the most permissive terms, but fails C2 (101 symbols need 5 days of quota; 100 bars is
below the 120-day window and the ≥ 200-day backfill) and C3 (no adjusted data). Rejected on
capability.

### Massive (formerly Polygon.io, free)

Terms: <https://massive.com/legal/individuals-terms-of-service> ("Last Updated: July 18, 2025") and
<https://massive.com/legal/market-data-terms-of-service> ("Last Updated: August 28, 2025").

- **Storage / licence**: "a nonexclusive, nontransferable, non-sublicensable, revocable, limited
  license to use Market Data exclusively for your personal, non-business, and non-commercial
  purposes. [...] you may not use the Market Data to build an application intended for use by end
  users other than you."
- **Public display of derived data**: "The Market Data may not be copied, reproduced, republished,
  uploaded, posted, publicly displayed, encoded, translated, transmitted, or distributed in any way
  (including "mirroring") to any other computer, server, website, or other medium for publication or
  distribution or for any business or commercial enterprise, without Massive's express prior
  written consent." You may not "Redistribute, display, disseminate, [...] publish, [...] or
  otherwise transfer the Market Data—or any data, charts, analytics, research, or other works based
  on, referring to, or derived from the Market Data ("Derived Works") — to any third party".
- **Attribution** (Individuals Terms §5.2): "any of our names, trademarks, service marks, logos,
  trade dress, or other branding included as part of the Services are owned by us, unless otherwise
  noted, and may not be copied, imitated, or used (in whole or in part) without our prior written
  consent."

**Verdict**: fails C6 explicitly; its aggregates are split-adjusted only, so C3 also fails. Rejected.

### Finnhub (free)

Terms: <https://finnhub.io/terms-of-service> (no date shown).

- **Storage**: "All data must be deleted should your subscription to that data ends."
- **Public display of derived data**: "You hereby agree to not redistribute or share access to data
  or derived results from the data obtained from Finnhub with anyone or any 3rd party without
  written approval from Finnhub."
- **Attribution**: "Neither these Terms nor your use of the Website grant you any right to use any
  trademark or service-mark accessed on or through the Website."

**Verdict**: fails C6 explicitly; historical daily candles also return HTTP 403 on a free key
(fails C2). Rejected.

### EODHD (free)

Terms: <https://eodhd.com/financial-apis/terms-conditions> (no date shown).

- **Storage**: "Non-Professional Users are permitted to store, manipulate, and analyze the data for
  private, non-commercial purposes."
- **Public display of derived data**: Non-Professional Users are "prohibited from: Selling,
  reselling, retransmitting, redistributing, displaying, or granting access to the Information or
  Services, whether in its original or repackaged form."
- **Attribution**: the terms are silent.

**Verdict**: fails C6 ("repackaged form") and, at 20 calls/day on the free plan, C2. Rejected.

### Not evaluated

Yahoo Finance (via `yfinance`) and Stooq have no official API for this use; both are scraping and
fail C4, so their terms were not analysed further.

### What needs written permission

Ask **Twelve Data** (sales@twelvedata.com) to confirm in writing, before the website feature starts:

1. That a standing, once-daily, non-commercial job on the Basic plan is permitted, not only
   "evaluation purposes" (§5.6).
2. That fetched daily bars may be kept in persistent storage for as long as the account is active,
   so past runs stay reproducible (§2.2(a), §2.3(g), §16.1).
3. That publishing the following on a public, non-commercial, static website counts as Derived
   Data under §2.2(c)/§6.2 and not as external display of Data under §2.2(e) or derivative works
   for redistribution under §17.1(g): daily abnormal returns, z-scores, flag/skip classifications,
   volume ratios, paper P&L, and — the one most likely to need an explicit yes — each flagged
   stock's raw daily return on the evaluated day.
4. That the site may name Twelve Data as the price source, and the exact attribution wording they
   want (§2.4, §6.1(b)).

If Twelve Data declines, the fallback order is: (a) ask FMP for the same four items, which needs a
waiver of §2.2.1 (downloading), §2.2.2 and §2.6.1(i) (display of derived data) and §10.4
(attribution); (b) buy a plan that includes display rights, which conflicts with the "no paid
APIs" success criterion and needs a spec change first.

### Consequences for this feature

- The production adapter becomes `TwelveDataPriceDataSource` and must request `adjust=all`
  (the API default is `splits`) and pass `start_date`/`end_date` so one call per symbol (1 API
  credit) covers the backfill range. At 8 requests/minute, 101 symbols take about 13 minutes.
- plan.md, quickstart.md and both contracts now name Twelve Data instead of FMP.
- Constitution Principle VI ("Each provider's terms MUST be respected") now has a concrete gate:
  the website feature must not publish any price-derived number until the permission above is on
  file.

**Vendor-agnostic mitigation**: the vendor sits behind the small `PriceDataSource` interface (see
contracts), so if permission is refused or the terms change, only the adapter needs to be replaced
— nothing in `calc/`, `detection.py`, or storage changes.

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

**Decision**: `requests`, used synchronously and sequentially (no concurrency) inside the Twelve
Data adapter, since a single run makes at most ~101 HTTP calls once per explicit evaluated day —
about 13 minutes at the free plan's 8 requests/minute, and concurrency would add complexity
(connection pooling, partial-failure handling) this feature does not need and the rate limit would
not allow. A simple client-side delay/backoff keeps the run under the 8-per-minute and
800-per-day ceilings and reacts to HTTP 429 by waiting and retrying rather than failing the whole
run on a single rate-limit hit.

**Alternatives considered and rejected**: `httpx` — equally capable, but its main advantage
(async) is not needed here; `requests` is the more minimal, more widely understood dependency for
a purely synchronous batch job.
