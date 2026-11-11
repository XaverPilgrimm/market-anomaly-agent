# Market Anomaly Agent

> **Experimental research project, not investment advice.**

A one-person research demo. Before each US market open it looks at the previous trading day and
flags the Nasdaq-100 stocks whose move was unusual relative to the overall market.

This first feature covers only the detection step:

- a frozen Nasdaq-100 universe;
- daily split- and dividend-adjusted bars for each stock and for the benchmark (QQQ);
- a market-model abnormal return and z-score per stock over a trailing window of up to 120
  trading days;
- flagging stocks with |z| ≥ 2, at most 10 per day;
- a volume ratio for each flagged case.

Results are stored append-only in PostgreSQL, so a re-run for the same day reproduces the stored
result. If the price data has changed since the first run, the re-run is rejected.

Gathering evidence, the LLM agent, trading signals and the public website are later features.

```bash
uv run detect --date 2026-11-10
```

## Getting started

Setup, configuration and the validation scenarios are in
[`specs/001-nasdaq100-anomaly-detection/quickstart.md`](specs/001-nasdaq100-anomaly-detection/quickstart.md).
The CLI's arguments, output and exit codes are in
[`contracts/cli-contract.md`](specs/001-nasdaq100-anomaly-detection/contracts/cli-contract.md),
and the full specification is in [`spec.md`](specs/001-nasdaq100-anomaly-detection/spec.md).

## Known limitations

- **The benchmark contains the evaluated stocks.** QQQ tracks the Nasdaq-100, so every evaluated
  stock is part of its own benchmark. Part of a large stock's move is already in the benchmark
  return it is compared against, so abnormal returns of the largest constituents are slightly
  understated.
- **No prediction-error correction in the z-score.** The z-score divides the abnormal return by
  the residual standard deviation of the estimation window. The event-study variance of the
  prediction error is not used: it would add the out-of-sample uncertainty of the fitted alpha
  and beta (MacKinlay 1997, "Event Studies in Economics and Finance"). This is a deliberate
  simplification. With 60–120 estimation days the correction is small, and it would slightly
  shrink every z-score.
- **Renamed or delisted tickers are not followed.** The universe is frozen at its snapshot date.
  A constituent that is later renamed or delisted is not replaced. On days with no data for it,
  the stock is recorded as a `missing_data` skip.

## Data

Price data comes from the Twelve Data free Basic plan. It is used for internal computation only.
Showing the data, or results derived from it, publicly is blocked until Twelve Data grants written
permission (see
[research.md §1](specs/001-nasdaq100-anomaly-detection/research.md)). This repository therefore
contains no prices, returns or run results. The tests use synthetic data.
