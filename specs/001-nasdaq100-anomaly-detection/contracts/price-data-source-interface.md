# Contract: `PriceDataSource` interface

**Feature**: [../spec.md](../spec.md) | **Research**: [../research.md](../research.md)

The vendor-specific HTTP details (FMP today — research.md §1) MUST stay behind this interface, so
the vendor can be swapped later without touching `calc/`, `detection.py`, or storage code.

## Shape (illustrative — not literal code)

```
class DailyBar:
    trading_date: date
    open: Decimal
    close: Decimal       # split-adjusted
    adjusted_close: Decimal  # split- and dividend-adjusted (FR-004/FR-005 use this one)
    volume: int

class PriceDataSource(Protocol):
    def get_daily_bars(
        self, ticker: str, start: date, end: date
    ) -> list[DailyBar]:
        """Return adjusted daily bars for `ticker` between `start` and `end`, inclusive,
        for trading days only. Raises PriceDataUnavailable if the vendor has no data for
        `ticker` at all; returns a (possibly shorter) list, never raises, when some days
        within the range are simply missing — callers decide what a gap means (FR-012)."""
```

## Error semantics

- A ticker the vendor has never heard of → `PriceDataUnavailable` (caller treats as "missing
  data," FR-012).
- A transient failure (timeout, HTTP 5xx, HTTP 429) → the adapter retries with backoff internally,
  within the run's overall budget; it only raises to the caller once retries are exhausted.
- The adapter never silently fabricates a bar for a day it has no data for.

## Implementations

- `FmpPriceDataSource` — the production adapter, documented in research.md §1 and §6.
- A test double (fixed, hand-built `DailyBar` lists) is used for every unit test; no test ever
  calls the real adapter or makes a network request (constitution Principle VIII).
