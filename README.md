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
The CLI has two modes:

```bash
uv run detect --date 2026-11-10    # evaluate the given trading day
uv run detect --latest             # evaluate the most recent completed trading day (New York time);
                                   # exits 0 without fetching if that day is already stored
uv run detect --latest --migrate   # apply pending database migrations first (either mode)
```

The arguments, output and exit codes are in the CLI contracts of
[feature 001](specs/001-nasdaq100-anomaly-detection/contracts/cli-contract.md) and
[feature 002](specs/002-azure-daily-run/contracts/cli-contract.md), which adds `--latest` and
`--migrate`. The full specifications are in
[001 `spec.md`](specs/001-nasdaq100-anomaly-detection/spec.md) and
[002 `spec.md`](specs/002-azure-daily-run/spec.md).

## Azure daily run

Feature 002 runs the detection unattended on Azure, in France Central:

- **When:** an Azure Container Apps scheduled job starts at **11:00 UTC, Tuesday to Saturday**,
  after every US close and before every US open. It runs `detect --latest --migrate`, which
  evaluates the most recent completed NASDAQ trading day. If that day is already stored (a
  repeated start, or the morning after a holiday) it exits without fetching or writing anything.
- **Where:** the image comes from GitHub Container Registry and is published by GitHub Actions on
  every merge to `main`, which signs in to Azure with OIDC. Results go to Azure Database for
  PostgreSQL (Burstable B1ms, PostgreSQL 17) in its own locked resource group. The Twelve Data key
  is in Key Vault. Logs go to Log Analytics, and a failed run or a missing run sends an email.
- **Retries:** at most 3 attempts per start, within a one-hour limit.

Entry points, all in `infra/scripts/`, with the procedures in
[`specs/002-azure-daily-run/quickstart.md`](specs/002-azure-daily-run/quickstart.md) and the
guarantees in
[`contracts/deployment-contract.md`](specs/002-azure-daily-run/contracts/deployment-contract.md):

| Task | Script |
|---|---|
| Check a region and the subscription | `preflight.sh <region>` |
| Deploy or converge (always behind a what-if) | `deploy.sh` |
| Create the database role and grants (new server only) | `db-access.sh open`, `db-bootstrap.sh`, `db-access.sh close` |
| Query the database as the owner | `db-access.sh open`, then the owner query in the contract, then `db-access.sh close` |
| Delete and rebuild the environment (the database stays) | `teardown.sh`, then `deploy.sh` |
| Export, then delete everything | `decommission.sh <dump path outside the repository>` |
| Bring an export back | `deploy.sh`, `db-bootstrap.sh`, `restore.sh <dump path>` |

**Every `az`, `gh`, `podman` and deployment command runs on the owner's host only**, never inside a
container or in the agent's environment. The repository is public, so the scripts read tenant,
subscription and object IDs, the alert address and the key at run time and never write them to a
file.

**Cost.** The cap is EUR 30 per month, enforced as an Azure budget that emails at 50%, 80% and
100% of the actual cost. Azure budgets only send notifications; they do not stop spending.
Estimate for France Central (research R10, check Q2):

| Item | EUR / month |
|---|---|
| PostgreSQL Flexible Server, Burstable B1ms, 730 h, 32 GiB storage | about 16.30 |
| Two log alert rules (estimate) | about 1.79 |
| Container Apps job and environment, Log Analytics, Key Vault | within the free grants, or a few cents |
| Container registry (GitHub Container Registry, public package) | 0 |
| **Total** | **about 18**, against the EUR 30 cap |

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
