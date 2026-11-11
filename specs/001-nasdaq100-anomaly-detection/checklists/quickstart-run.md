# Quickstart Run: Nasdaq-100 Universe and Daily Anomaly Detection

**Task**: T045 | **Guide**: [quickstart.md](../quickstart.md)

**Evaluated date**: 2026-11-10 | **Run on**: 2026-11-11 | **Environment**: real Twelve Data key
(free Basic plan), local PostgreSQL

This repository is public, and Twelve Data's terms do not cover publishing its data or results
derived from it ([research.md §1](../research.md)). This record therefore holds only pass/fail,
dates, exit codes, counts and run times. It contains no prices, returns, z-scores or other
numbers derived from vendor data, and no tickers tied to results.

## Scenarios

| Scenario | How it was checked | Exit code | Result |
|---|---|---|---|
| 1. First run for a known day | real run | 0 | PASS: summary `flagged=3 evaluated=100 skipped=0` |
| 2. Same-day re-run | real run | 0 | PASS: logged "already stored with the same price data; reproduced", printed the same summary, stored nothing new; the vendor returned identical data on the second fetch |
| 3. Re-run after the price data changed | not run against the vendor; covered by the T031 end-to-end test | 3 (in the test) | PASS (by test): nothing written |
| 4. Insufficient history and missing data | real run, plus the T041 end-to-end tests | — | PASS: on the evaluated day no universe stock had fewer than 60 paired returns, so the real run had no `insufficient_history` skip; the behaviour is covered by the T041 tests |

For scenario 4, the shortest estimation windows in the real run were 102 and 104 paired returns.
One stock had 118 instead of 120 because the vendor lacked one bar.

## Run times

| Run | real | user |
|---|---|---|
| Scenario 1, first run | 12m31s | 2.4s |
| Scenario 2, re-run | 12m31s | — |

The run is bound by the vendor's limit of 8 requests per minute, not by computation: 101
requests (100 stocks plus the benchmark) take about 12.5 minutes at that rate.

## Hand check (scenario 1)

| Check | Result |
|---|---|
| For one flagged stock, alpha, beta, residual standard deviation (n − 2 degrees of freedom) and z-score were recomputed independently in SQL (`regr_slope`, `regr_intercept`) from the stored bars | PASS: all four matched the stored values to 4 decimal places |

## Further checks

| Check | Result |
|---|---|
| One stock's latest stored close compared with nasdaq.com | PASS: matched to the cent |
| Stock and benchmark trading dates aligned (for a 10-stock sample, the same-day correlation with the benchmark was the highest among the day offsets tried) | PASS |
| Real vendor `meta` for the benchmark and one stock: `exchange` and `currency` | PASS: `NASDAQ` and `USD` for both |
