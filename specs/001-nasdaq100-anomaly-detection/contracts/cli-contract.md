# Contract: `detect` CLI command

**Feature**: [../spec.md](../spec.md)

## Invocation

```
uv run detect --date YYYY-MM-DD
```

- `--date` (required): the single explicit evaluated trading day (FR-021). Must be a calendar
  date on which the US equity market (NASDAQ calendar) was open (FR-013); a non-trading date is a
  usage error, not a run that silently does nothing.
- No other mode of operation exists: there is no "run for today" default and no date-range/backlog
  option (FR-021; see spec Assumptions on scheduling being out of scope).

## Required environment

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (e.g. `postgresql://...@host.containers.internal:5432/...` when run inside the agent-box container) |
| `PRICE_DATA_API_KEY` | API key for the price-data adapter (Twelve Data by default — see research.md §1) |

Secrets are read from the environment only; they are never logged or printed (constitution
Principle IV).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Run completed: either a fresh evaluation was stored, or the day's result was already stored and this run reproduced it (same input data — FR-018) |
| `1` | Unexpected error (network failure after retries, a vendor error other than "no matching data", database unavailable, programming error). Nothing is written. |
| `2` | Usage error: `--date` missing/malformed, or `--date` is not a trading day per the US market calendar |
| `3` | Reject: the day was already evaluated, and the freshly fetched price data does not match the bar snapshot stored by that day's original run (FR-018). Nothing is written. |
| `4` | Input data unusable for this day: the benchmark has no usable data (FR-022), or more than 20% of the universe would be skipped for missing data (FR-023). Nothing is written; the same command can be run again later. |

## Stdout contract

On success (`0`), the command prints one summary line as its last line of output, machine-parseable
as whitespace-separated `key=value` pairs, e.g.:

```
evaluated_date=2026-10-27 status=completed flagged=7 evaluated=94 skipped=7 rule_version=1
```

`evaluated` counts `evaluated_stock_records` rows and `skipped` counts `skipped_stock_records`
rows for the day. A stock that qualified but was dropped by the daily cap has both rows, so it is
counted in both, and `evaluated + skipped` can exceed the universe size.

Human-readable detail (per-ticker breakdown, timing) may precede that line but the last line is
always this summary, so scripts can reliably read it with `tail -1`.

## Idempotency (FR-018, FR-020)

Running the same command twice with the same `--date`, against unchanged underlying price data,
MUST exit `0` both times and MUST NOT create a second `detection_runs` row (or any other duplicate
row) for that date. Running it again after the vendor's data for that date range has changed MUST
exit `3` and MUST NOT modify anything already stored.
