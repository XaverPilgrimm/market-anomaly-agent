# Contract: `PriceDataSource` interface

**Feature**: [../spec.md](../spec.md) | **Research**: [../research.md](../research.md)

The vendor-specific HTTP details (Twelve Data today — research.md §1) MUST stay behind this interface, so
the vendor can be swapped later without touching `calc/`, `detection.py`, or storage code.

## Shape (illustrative — not literal code)

```
class DailyBar:
    trading_date: date
    open: Decimal        # split- and dividend-adjusted (FR-004)
    close: Decimal       # split- and dividend-adjusted (FR-004; FR-005 computes returns from this)
    volume: int

class PriceDataSource(Protocol):
    def get_daily_bars(
        self, ticker: str, start: date, end: date
    ) -> list[DailyBar]:
        """Return adjusted daily bars for `ticker` between `start` and `end`, inclusive,
        for trading days only. Raises PriceDataUnavailable if the vendor reports no
        matching data for `ticker`, and PriceDataError for any other failure; returns a (possibly shorter) list, never raises, when some days
        within the range are simply missing — callers decide what a gap means (FR-012)."""
```

## Error semantics

- The vendor reports that it has no matching data for the ticker → `PriceDataUnavailable`
  (caller treats as "missing data," FR-012). For Twelve Data this is error code `404` only.
- The vendor rejects the request itself (for Twelve Data, error code `400`: an invalid or malformed
  parameter) → `PriceDataError`, never `PriceDataUnavailable`, so a parameter bug fails the run
  loudly instead of looking like missing data.
- Any other non-retryable vendor error (for example an invalid API key) or a malformed response
  → `PriceDataError`.
- A transient failure (timeout, HTTP 5xx, HTTP 429) → the adapter retries with backoff internally,
  within the run's overall budget; it only raises `PriceDataError` to the caller once retries are
  exhausted.
- The caller treats `PriceDataError` for any ticker as a failed run (CLI exit `1`), not as a
  skipped stock.
- The adapter never silently fabricates a bar for a day it has no data for.

## Implementations

- `TwelveDataPriceDataSource` — the production adapter, documented in research.md §1 and §6. It
  calls `/time_series` with `interval=1day`, `adjust=all` (the API default is `splits`, which
  would leave dividends unadjusted) and `start_date`/`end_date`, so one request per ticker covers
  the whole range.
- A test double (fixed, hand-built `DailyBar` lists) is used for every unit test; no test ever
  calls the real adapter or makes a network request (constitution Principle VIII).
