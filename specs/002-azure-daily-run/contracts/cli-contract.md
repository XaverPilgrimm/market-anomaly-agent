# Contract: `detect` CLI command (amended by feature 002)

**Feature**: [../spec.md](../spec.md) · **Amends**:
[001 cli-contract](../../001-nasdaq100-anomaly-detection/contracts/cli-contract.md)

Everything in the 001 contract still holds unless this document says otherwise. In particular,
the exit codes, the stdout summary line and the idempotency rules for `--date` are unchanged. The
001 sentence "No other mode of operation exists" is replaced by the modes below (spec FR-001).

## Invocation

```
detect --date YYYY-MM-DD [--migrate]     # explicit day (unchanged from 001)
detect --latest          [--migrate]     # resolve the day itself (new)
```

- Exactly one of `--date` and `--latest` is required. Giving both, or neither, is a usage error
  (exit 2).
- `--latest`: the evaluated day is the most recent NASDAQ trading day strictly before the current
  calendar date in `America/New_York`. The resolved day is always a trading day, so `--latest`
  never exits 2 for a non-trading day.
- `--migrate`: apply pending migrations (`storage/migrations/*.sql`, feature 001's runner) before
  anything else touches the database. Already applied migrations are a no-op. A migration failure
  exits 1 and nothing else runs.
- Still exactly one evaluated day per invocation. There is no backlog or catch-up mode.

The scheduled job runs `detect --latest --migrate`.

## Processing order

```
parse args ──▶ resolve day ──▶ log start line ──▶ [--migrate: apply migrations]
   │
   ├─ --date:   unchanged 001 flow (validate trading day, API key, universe, fetch, compare/store)
   │
   └─ --latest: load_run(day)
                  ├─ found:     log INFO "… already stored; nothing fetched", print stored summary, exit 0
                  └─ not found: identical to `detect --date <day>` from "API key" onwards
```

In `--latest` mode, a day that is already stored causes **0 vendor requests and 0 writes**. The API
key is not read, the universe file is not loaded, and the snapshot comparison of 001's FR-018 is
skipped, so a repeated start can never exit 3 (spec FR-002, SC-002).

## Logging (stderr)

The first INFO line of every run (spec FR-004):

```
INFO market_anomaly_agent: start mode=latest evaluated_date=2026-11-10 version=3f9c2ab…
```

`mode` is `latest` or `date`. `version` is `$APP_COMMIT`, or `unknown` when it is unset (local
runs). With `--migrate`, one INFO line lists the applied filenames, or `none`.

## Environment

Feature 001's table still applies, with these additions:

| Variable | Purpose |
|---|---|
| `DATABASE_AUTH` | `password` (default; 001 behaviour) or `entra` |
| `AZURE_CLIENT_ID` | Client ID of the user-assigned managed identity; required with `DATABASE_AUTH=entra` |
| `APP_COMMIT` | Commit SHA the image was built from; logged only |

With `DATABASE_AUTH=entra`:

- `DATABASE_URL` names the role (`id-maa-job`), host and database, carries
  `sslmode=verify-full&sslrootcert=…`, and contains **no password**.
- Before each connection, the process obtains a Microsoft Entra access token for
  `https://ossrdbms-aad.database.windows.net/.default` from the managed identity and uses it as
  the connection password.
- Each token is added to the redaction list, so it never appears in any output, including
  tracebacks.
- A `DATABASE_URL` with a password, a missing `sslmode=verify-full`, a missing `AZURE_CLIENT_ID`
  or a failed token request exits 1, and nothing is written.

## Exit codes

Unchanged from 001 (0, 1, 2, 3, 4). Additions:

- `--latest` with the resolved day already stored → `0`.
- `--date` and `--latest` both given, or neither → `2`.
- An Entra configuration or token failure, or a migration failure → `1`.

## Stdout contract

Unchanged. The last line is the summary line. In the `--latest` "already stored" case it is the
stored summary of that day, so the alert rule that looks for `status=completed` sees a line on
every healthy scheduled day.

## Test obligations (test-first, constitution VIII)

- **Day resolution** with an injected clock. Cases: Tuesday morning → Monday; Saturday → Friday;
  the morning after a market holiday → the trading day before the holiday; 23:30 New York on a
  trading day → the previous trading day; the New York date differing from the UTC date (00:30 UTC
  is the previous New York date); both sides of a daylight-saving change.
- **`--latest` with the day stored**: the price-data fake records no calls, no rows are added,
  exit 0, and the last stdout line is the stored summary.
- **`--latest` with the day not stored**: same output and stored rows as `--date <day>`.
- **Argument errors**: `--date` together with `--latest` → exit 2; neither → exit 2.
- **`--migrate`**: applies migrations on an empty schema (integration); a second run applies none.
- **Entra mode**, with the credential mocked and no network: token passed as the password,
  configuration errors exit 1, and the token string is redacted from a forced error message.
