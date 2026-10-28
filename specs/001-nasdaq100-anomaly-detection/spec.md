# Feature Specification: Nasdaq-100 Universe and Daily Anomaly Detection

**Feature Branch**: `001-nasdaq100-anomaly-detection`

**Created**: 2026-10-28

**Status**: Draft

**Input**: User description: "Feature: Nasdaq-100 universe and daily anomaly detection. Before each US market open, identify the Nasdaq-100 stocks whose move on the previous trading day was unusual relative to the overall market. These flagged cases are the input for all later steps (evidence gathering, agent judgment, signals). Requirements cover: a frozen Nasdaq-100 universe with snapshot date and source; daily adjusted open/close/volume for every universe stock and a Nasdaq-100 benchmark; a market-model-based abnormal return and z-score per stock over a trailing 120-trading-day window; flagging stocks with |z| >= 2, capped at 10 per day with the rest recorded as skipped; a volume ratio for every flagged case; skipping stocks with insufficient history or missing data, with the reason logged; New York time / US market calendar handling; and idempotent, versioned, timestamped storage of results. Out of scope: evidence gathering, the agent, signals, the website, deployment. Success: reproducible results, hand-checked test coverage for every calculation, no paid APIs needed."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Daily anomaly flagging before market open (Priority: P1)

Before the US market opens, the system looks at the previous trading day and flags the Nasdaq-100
stocks whose move was statistically unusual relative to the Nasdaq-100 benchmark, producing a
ready, numbered, timestamped, versioned list that later steps (evidence gathering, agent judgment,
signal generation — all outside this feature) can act on.

**Why this priority**: This is the core deliverable of the feature. Without a trustworthy daily
flagged-case list, none of the downstream research steps have anything to work with.

**Independent Test**: Supply a fixed, known price history for the universe and the benchmark
covering a specific past date range; run detection for one evaluated trading day; compare the
flagged cases and every computed number against hand-calculated expected values.

**Acceptance Scenarios**:

1. **Given** a stock whose actual return on the evaluated day is far enough from what the market
   model predicts that its z-score has absolute value at least 2, **When** detection runs for that
   day, **Then** the stock appears in the flagged-case output with a correctly computed abnormal
   return, z-score, and volume ratio.
2. **Given** 14 stocks qualify for flagging on one evaluated day, **When** detection runs,
   **Then** exactly the 10 cases with the largest absolute z-score are stored as flagged, and the
   other 4 are recorded as skipped with a reason indicating the daily cap was exceeded.
3. **Given** detection has already been run once for a specific evaluated day, **When** it is run
   again for that same day using the same input data, **Then** no duplicate flagged-case or
   skipped-stock records are created for that day.
4. **Given** a stock's z-score on the evaluated day does not meet the flagging threshold, **When**
   detection runs, **Then** its return, expected return, abnormal return, and z-score are still
   stored as an evaluated-stock record, and the benchmark's own return for that day is stored
   alongside it.

---

### User Story 2 - Frozen, documented universe (Priority: P2)

A single, fixed list of the Nasdaq-100 constituents is captured once, together with the date it
was captured and where it came from, so the research scope cannot silently drift over the life of
the project.

**Why this priority**: Every later step — daily data loading, detection, and eventually evidence
gathering and signals — depends on knowing exactly which stocks are in scope. The constitution
requires this universe to be frozen before detection results can be considered trustworthy.

**Independent Test**: Inspect the committed universe artifact; confirm it lists the Nasdaq-100
constituents as of the recorded snapshot date and cites the source; confirm that no later process
adds, removes, or otherwise changes entries in it.

**Acceptance Scenarios**:

1. **Given** the universe has been captured once, **When** the detection process runs on any later
   date, **Then** it uses the same constituent list, with no additions or removals.
2. **Given** a company joins or leaves the real-world Nasdaq-100 index after the snapshot date,
   **When** later detection runs occur, **Then** the universe used is still the originally frozen
   list, not the current real-world index membership.
3. **Given** a company has more than one share class included in the real-world Nasdaq-100 (such
   as Alphabet's GOOGL and GOOG), **When** the universe is captured, **Then** only one class is
   included (GOOGL for Alphabet) and the choice is documented alongside the universe list.

---

### User Story 3 - Safe handling of gaps and noisy history (Priority: P3)

Stocks with insufficient or missing history are safely skipped and clearly logged, rather than
silently producing wrong numbers or halting the run, so the daily output can be trusted.

**Why this priority**: Without this, one bad or sparse data point could corrupt a day's results or
stop the whole pipeline, undermining the project's research-integrity requirements.

**Independent Test**: Supply a universe that includes one recently listed stock (fewer than 60
trading days of history), one stock with a moderate history between 60 and 120 trading days, and
one stock with a missing price on the evaluated day; run detection; confirm the first and third
appear in a skip log with distinct, correct reasons, the second is evaluated using its shorter
history, and every other stock is evaluated normally.

**Acceptance Scenarios**:

1. **Given** a stock has only 45 trading days of history, **When** detection runs, **Then** that
   stock is skipped with a reason indicating insufficient history (fewer than the required 60
   trading days), and it is not evaluated with a partial window.
2. **Given** a stock has 80 trading days of history (at least 60 but fewer than 120), **When**
   detection runs, **Then** that stock is evaluated using all 80 available trading days as its
   estimation window, rather than being skipped or padded to 120 days.
3. **Given** a stock has no recorded trade for the evaluated day, **When** detection runs,
   **Then** that stock is skipped with a reason indicating missing data for that day.

---

### Edge Cases

- Weekend or US market holiday: the evaluated day and the "previous trading day" are both resolved
  against the US equity market calendar; a non-trading day is never treated as an evaluated day.
- Early-close trading session (such as the day after Thanksgiving): treated as a normal, full
  trading day, with no special-case handling.
- Daylight saving time transition: the trading-day boundary stays anchored to New York local market
  hours, so the transition does not shift which calendar day is evaluated.
- A split or dividend on or before the evaluated day: adjusted price data prevents the resulting
  price jump from being mistaken for an anomaly.
- The benchmark's own data is missing or unusable for the evaluated day: the entire day's detection
  is skipped and logged, since no stock's move can be judged without the benchmark.
- An exact tie in absolute z-score at the 10th/11th rank: ties are broken by ordering the tied
  stocks alphabetically by ticker, so the kept set of 10 is always reproducible.
- More than 10 stocks qualify on one day: only the top 10 by absolute z-score (after tie-breaking)
  are kept; every other qualifying stock is recorded as skipped with a cap-exceeded reason, never
  silently dropped.
- Detection is re-run for a day it has already evaluated: the stored result for that day is
  unchanged — no duplicate records are created.
- A company has more than one share class in the real-world Nasdaq-100: only one class (GOOGL,
  not GOOG, for Alphabet) is part of the universe, so the same company is never evaluated twice
  under two tickers.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST maintain a single list of Nasdaq-100 constituents captured at one
  snapshot date, recording both that date and the source used to compile the list.
- **FR-002**: When a company has more than one share class included in the real-world Nasdaq-100,
  the universe MUST retain exactly one class for that company and MUST document which class was
  chosen; for Alphabet, the universe MUST retain GOOGL and MUST exclude GOOG.
- **FR-003**: Once committed, the constituent list MUST remain frozen for the duration of the
  project — later runs MUST NOT add, remove, or otherwise change its entries, regardless of
  changes to the real-world Nasdaq-100 index.
- **FR-004**: For each evaluated trading day, the system MUST obtain split- and dividend-adjusted
  daily open, close, and volume for every stock in the universe and for the Nasdaq-100 benchmark.
- **FR-005**: The system MUST compute each stock's and the benchmark's return for a given trading
  day as the simple percentage change from the previous trading day's adjusted close to that day's
  adjusted close.
- **FR-006**: For each stock and evaluated trading day, the system MUST estimate that stock's
  alpha and beta against the benchmark by regressing its returns on the benchmark's returns over
  the most recent trading days available before the evaluated day, using up to 120 such days; at
  least 60 days of history MUST be available, or the stock MUST be skipped per FR-012.
- **FR-007**: The system MUST compute each stock's abnormal return for the evaluated day as its
  actual return minus the return expected from its market-model estimate (alpha, beta, and the
  benchmark's return on that day).
- **FR-008**: The system MUST compute a z-score for the evaluated day as the abnormal return
  divided by the residual standard deviation of the market-model regression from FR-006, computed
  with n − 2 degrees of freedom, where n is the number of trading days used in that regression.
- **FR-009**: The system MUST flag a stock as an anomaly case for the evaluated day when the
  absolute value of its z-score is at least 2.
- **FR-010**: When more than 10 stocks qualify as anomaly cases on a given evaluated day, the
  system MUST keep only the 10 cases with the largest absolute z-score; ties in absolute z-score
  at the cap boundary MUST be broken by ordering the tied stocks alphabetically by ticker. Every
  qualifying stock not kept MUST be recorded as skipped, with a reason indicating the daily cap
  was exceeded.
- **FR-011**: For every flagged anomaly case, the system MUST compute a volume ratio equal to the
  evaluated day's volume divided by the stock's average daily volume over the preceding 20 trading
  days.
- **FR-012**: The system MUST skip a stock for an evaluated day, and log the specific reason, when
  fewer than 60 trading days of price history are available before that day (a reason indicating
  insufficient history), or when its data for the evaluated day is missing or otherwise unusable
  (a distinct reason indicating missing data).
- **FR-013**: The system MUST determine trading days, the evaluated day's boundaries, and the
  "previous trading day" using the US equity market calendar (observing weekends and market
  holidays) expressed in New York local time, with daylight saving time transitions handled
  correctly and early-close trading sessions (such as the day after Thanksgiving) treated as
  normal, full trading days.
- **FR-014**: For each evaluated trading day, the system MUST persist, for every evaluated stock
  whether flagged or not, its return, its market-model-expected return, its abnormal return, and
  its z-score, together with the time the computation was performed and the version identifier of
  the detection rules used.
- **FR-015**: For each evaluated trading day, the system MUST persist the benchmark's own return
  for that day.
- **FR-016**: For every flagged anomaly case, the system MUST additionally persist its volume
  ratio.
- **FR-017**: For each evaluated trading day, the system MUST persist every skipped stock together
  with its specific skip reason.
- **FR-018**: Re-running detection for a trading day that has already been evaluated MUST NOT
  create duplicate stored records for that day.
- **FR-019**: Universe construction and daily detection MUST NOT require any paid API or paid data
  subscription.
- **FR-020**: Given the same input price data, repeated detection runs for the same evaluated day
  MUST produce identical evaluated-stock records, flagged cases, skipped records, and computed
  numbers.

### Key Entities

- **Universe Constituent**: A stock that is in scope for the project — ticker, company name, the
  snapshot date it was captured as of, and the source it was captured from. Frozen once committed.
  For a company with more than one listed share class, only one documented class is included
  (e.g., GOOGL rather than GOOG for Alphabet).
- **Daily Price Bar**: One trading day's adjusted open, close, and volume for a universe stock or
  for the benchmark.
- **Detection Run**: One execution of the daily detection process for a specific evaluated trading
  day — carries the detection rule version, the time the computation was performed, and the
  benchmark's return for that day.
- **Evaluated Stock Record**: The per-stock result of a detection run for every stock that had
  enough history and data to be evaluated — carries its return, market-model-expected return,
  abnormal return, and z-score, whether or not the stock ended up flagged.
- **Anomaly Case**: The subset of Evaluated Stock Records whose absolute z-score met the flagging
  threshold and was kept under the daily cap — additionally carries the volume ratio.
- **Skipped Stock Record**: A stock that, for a specific evaluated trading day, was either not
  evaluated (insufficient history, missing data) or qualified as an anomaly case but was not kept
  because of the daily cap. Carries the specific reason. Stocks that were evaluated but did not
  qualify are Evaluated Stock Records, not skipped records.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Re-running detection for the same historical trading day, with the same input price
  data, always produces the same flagged cases and the same computed numbers.
- **SC-002**: Every deterministic calculation used by detection — return, alpha/beta estimation,
  abnormal return, z-score, and volume ratio — has at least one hand-checked example that passes.
- **SC-003**: Universe construction and daily detection run end-to-end without any paid API
  subscription or paid data feed.
- **SC-004**: On every evaluated trading day, the number of flagged cases never exceeds 10, and
  every stock that qualified but was not kept is recorded with a skip reason rather than dropped.
- **SC-005**: Running detection twice for the same evaluated day never produces duplicate stored
  flagged-case or skipped-stock records for that day.
- **SC-006**: The frozen Nasdaq-100 universe list, together with its snapshot date and source,
  remains available and unchanged across every subsequent run for the life of the project.

## Assumptions

- The Nasdaq-100 "benchmark" is a broad, continuously traded instrument tracking the Nasdaq-100
  index, with its own daily open, close, and volume — not the bare index value, which has no
  trading volume of its own.
- The universe snapshot date is the date the Nasdaq-100 constituent list is actually compiled and
  committed while building this feature; the source is a single, publicly citable, authoritative
  listing of Nasdaq-100 constituents as of that date.
- "Previous trading day" and "evaluated trading day" follow the standard US equity market trading
  calendar, anchored to New York local time.
- A "detection rule version" is a simple identifier that changes whenever the flagging threshold,
  the daily cap, or an estimation-window parameter changes; it does not require a full historical
  changelog beyond what is already needed for rule versioning.
- This feature produces and stores data only; it exposes no user interface. Evidence gathering,
  the agent, signal generation, and the public website are separate, later features that consume
  this feature's output.
- **Known limitation**: the Nasdaq-100 benchmark itself contains the evaluated stock (for every
  universe constituent, since the universe is the index). As a result, abnormal returns for the
  largest constituents — which carry the most weight in the benchmark — are slightly understated,
  because part of a large stock's own move is already baked into the benchmark return it is being
  compared against.
