---

description: "Task list for Unattended Daily Run on Azure"
---

# Tasks: Unattended Daily Run on Azure

**Input**: Design documents from `/specs/002-azure-daily-run/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli-contract.md,
contracts/deployment-contract.md, quickstart.md

**Tests**: Required for all application code. Constitution Principle VIII requires test-first
development: every test task below comes before the task that implements it, and its tests MUST be
seen failing first. No unit test makes a network call. The guard in `tests/conftest.py` stays
active, and `azure.identity.ManagedIdentityCredential` is always replaced by a fake. Bicep and shell
scripts have no unit tests. They are checked by `az bicep build` in CI and by the host-side checks
in quickstart.md.

**Organization**: Tasks are grouped by user story so each story can be implemented and tested on
its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: Which user story the task belongs to (US1 to US5)
- **(owner, host)**: the task is performed by the owner on the host, never in the agent container
  and never by Claude Code (research R15). An implementing agent stops at these tasks and hands
  over. It MUST NOT run `az`, `gh`, `podman`, `psql` or `pg_dump` itself, and it MUST NOT commit,
  push or merge (constitution, Development Workflow).

## Path Conventions

Single project at the repository root: `src/market_anomaly_agent/`, `tests/unit/`,
`tests/integration/`. Infrastructure in `infra/`, the workflow in `.github/workflows/ci.yml`,
the image definition in `Containerfile` (plan.md, Project Structure).

## Shared decisions used by several tasks

These are fixed here so no task has to decide them during implementation:

- **Names** (data-model.md §3): resource groups `rg-maa-data` and `rg-maa-env`; identities
  `id-maa-job` (in `rg-maa-data`) and `id-maa-github` (in `rg-maa-env`); server `psql-maa-<u>`;
  vault `kv-maa-<u>`; secret `twelve-data-api-key`; job secret `price-data-api-key`; Log Analytics
  workspace `log-maa`; environment `cae-maa`; job `job-maa-detect`; action group `ag-maa-owner`;
  alerts `alert-maa-execution-failed` and `alert-maa-no-completed-run`; budget
  `budget-maa-monthly`; lock `lock-maa-data`. In Bicep, `<u>` is
  `take(uniqueString(subscription().id), 6)`.
- **Database**: `market_anomaly_agent`. Job role `id-maa-job`. Entra administrator: the security
  group `maa-db-admins`, whose only member is the owner's account (research R16). The owner signs in
  with `user=maa-db-admins` and their own token.
- **Public repository rule** (spec FR-025, research R16): no file may contain the owner's email
  address, tenant domain, tenant ID, subscription ID or any object ID. Scripts read them at run
  time (`az ad signed-in-user show`, `az ad group show`, `az account show`). Documents and comments
  use placeholders such as `<tenant-id>`. Microsoft's documented built-in role definition IDs and
  fixed public names are allowed.
- **Image**: `ghcr.io/xaverpilgrimm/market-anomaly-agent:<commit-sha>`.
  Placeholder before the first publish: `mcr.microsoft.com/k8se/quickstart-jobs:latest`.
- **Constants in code**:
  - `storage/db.py`: `AUTH_ENV_VAR = "DATABASE_AUTH"`, `CLIENT_ID_ENV_VAR = "AZURE_CLIENT_ID"`,
    `ENTRA_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"`.
  - `cli.py`: `COMMIT_ENV_VAR = "APP_COMMIT"`.
  - `calendar.py`: the existing `_TZ = "America/New_York"`.
- **Injected clock**: `cli.main(argv, *, source_factory=None, now=None)`, where
  `now: Callable[[], datetime] | None` defaults to `lambda: datetime.now(UTC)`. Tests always pass
  `now`.
- **Start log line**, exactly:
  `start mode=<latest|date> evaluated_date=<YYYY-MM-DD> version=<APP_COMMIT or unknown>`, at INFO,
  before any database access.
- **Already-stored log line** (US2), at INFO:
  `<YYYY-MM-DD> already stored; nothing fetched`.
- **Bicep API versions**: `Microsoft.App/jobs@2026-07-01` and the matching
  `Microsoft.App/managedEnvironments` version. For the other resource types, use the newest
  non-preview version listed in the Azure template reference, **except**
  `Microsoft.DBforPostgreSQL/flexibleServers` and its children, which stay on `2025-08-01`
  (research R2, *API version for PostgreSQL*).
- **Fixed job settings** (contracts/deployment-contract.md §4): cron `0 11 * * 2-6`,
  `replicaTimeout` `3600` (set as Bicep parameter `jobReplicaTimeoutSeconds`, default 3600, so
  check Q3 can change it), `replicaRetryLimit` `2`, `parallelism` `1`, `replicaCompletionCount`
  `1`, `0.5` vCPU / `1Gi`, args `["--latest", "--migrate"]`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: dependencies, ignore files and directory skeleton

- [X] T001 Add `azure-identity` as a runtime dependency with `uv add azure-identity`, which updates `pyproject.toml` and `uv.lock`. Check that `uv run mypy` still passes; `azure-identity` ships type information, so no mypy override should be needed. If it is, add one to the `[[tool.mypy.overrides]]` section of `pyproject.toml`.
- [X] T002 [P] Append `*.dump` to `.gitignore` (decommission exports, FR-031), and create the empty directories `infra/modules/`, `infra/sql/` and `infra/scripts/`, each with a `.gitkeep` until its first file exists.
- [X] T003 [P] Create `.containerignore` excluding `.env`, `.env.*` (but not `.env.example`), `.git/`, `.github/`, `specs/`, `tests/`, `infra/`, `.venv/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`, `*.dump` and `.claude/` (FR-005: no secret or environment file in the image).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the subscription-scope Bicep entry point, the host-only guard shared by every
script, and the CI checks every later change is validated by

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 Create `infra/scripts/_common.sh`, sourced by every script. It does three things. (1) `set -euo pipefail`. (2) It exits 1 with `run this on the host, not inside a container (research R15)` if `/run/.containerenv` or `/.dockerenv` exists. (3) It defines `require_az_login` (runs `az account show` and exits 1 with a clear message if that fails), `repo_root` (the git top level) and `pg_client` (`podman run --rm -i -e PGPASSWORD --network host docker.io/library/postgres:17 "$@"`). Make it non-executable; it is only sourced. *Added later:* its first line is `# shellcheck shell=bash` (it is sourced, so it has no shebang), and every script that sources it carries `# shellcheck source=infra/scripts/_common.sh` above the `source` line, so `shellcheck -x` (T007) can follow it.
- [X] T005 Create `infra/main.bicep` with `targetScope = 'subscription'`:
  - The parameters from contracts/deployment-contract.md §2: `location`, `githubRepository`, `dbAdminGroupName` (default `'maa-db-admins'`), `dbAdminGroupObjectId`, `alertEmail`, `@secure() twelveDataApiKey` (default `''`), `jobImage`, `ownerAccessIp` (default `'192.0.2.1'`), `budgetAmountEur` (default `30`), `jobReplicaTimeoutSeconds` (default `3600`), and `budgetStartDate` (default `'2026-11-01'`).
  - The two resource groups `rg-maa-data` and `rg-maa-env` in `location`.
  - Module calls to `modules/data.bicep` (scope `rg-maa-data`) and `modules/env.bicep` (scope `rg-maa-env`), with env.bicep receiving data.bicep's outputs.
  - The outputs `AZURE_CLIENT_ID` (id-maa-github client ID), `AZURE_TENANT_ID` (`tenant().tenantId`), `AZURE_SUBSCRIPTION_ID` (`subscription().subscriptionId`), `serverFqdn` and `jobName`.

  Create both modules as compiling stubs with those params and outputs, so `az bicep build --file infra/main.bicep` succeeds.
- [X] T006 [P] Create `infra/main.bicepparam` with `using 'main.bicep'` and only non-secret defaults: `location = 'germanywestcentral'`, `githubRepository = 'XaverPilgrimm/market-anomaly-agent'`, `budgetAmountEur = 30`, `budgetStartDate = '2026-11-01'`. It MUST NOT contain the alert email, any tenant ID, subscription ID or object ID, any tenant domain, any IP address or the vendor key (FR-018, FR-022, FR-025). Add a header comment saying so.
- [X] T007 Create the `checks` job in `.github/workflows/ci.yml`:
  - Triggers `pull_request` (branches `[main]`), `push` (branches `[main]`) and `workflow_dispatch`.
  - Top-level `permissions: contents: read`.
  - Job `checks` on `ubuntu-24.04` with a `postgres:17` service container (`POSTGRES_PASSWORD: postgres`, health check `pg_isready`, port 5432) and env `DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres` (a throwaway CI-only value, not a credential).
  - Steps:
    1. `actions/checkout` and `astral-sh/setup-uv`, both pinned to full commit SHAs with a version comment.
    2. `uv sync --frozen`.
    3. `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`.
    4. `uv run pytest`, then `uv run pytest -m integration`.
    5. `az bicep build --file infra/main.bicep --stdout > /dev/null` (offline; no `azure/login` in this job).

  This covers FR-007 for pull requests and pushes.

  *Extended after T016:* two more steps at the end of `checks`, on every pull request and push, with no push and no registry login:
    6. `docker build --build-arg GIT_SHA="$GIT_SHA" -f Containerfile -t market-anomaly-agent:ci .`, with `GIT_SHA: ${{ github.sha }}` passed through `env:`. This also runs the Containerfile's CA guard (research R8).
    7. `docker run --rm market-anomaly-agent:ci --help`, failing the job unless the output has a line matching `^usage: detect .*--latest`.
    The build pulls `python:3.12-slim-bookworm` and `ghcr.io/astral-sh/uv:0.12.19` (uv binaries only, copied in by T016's build stage). Both are public, so the step still needs no registry login. A missing or mistyped base tag fails this step on the pull request, before anything reaches `main`.
  *Extended again:* a `Shellcheck host scripts` step before the Bicep build installs shellcheck with `apt-get` only if the runner lacks it, prints its version, and runs `shellcheck -x infra/scripts/*.sh`. Deliberate exceptions are disabled inline with the reason on the line above (SC2016 in `preflight.sh`, where `$d` is a jq variable; SC2069 in `db-bootstrap.sh`, where only stderr is captured).

**Checkpoint**: Bicep compiles, CI runs every check on pull requests, scripts refuse to run in the
agent container.

---

## Phase 3: User Story 1 - The day's detection runs and is stored without anyone starting it (Priority: P1) 🎯 MVP

**Goal**: a scheduled Container Apps job runs `detect --latest --migrate` as `id-maa-job` against
an Entra-only PostgreSQL 17 server and stores the most recent completed trading day. Every push to
`main` that passes the checks publishes an image and switches the job to it.

**Independent Test**: quickstart.md §4, rows US1-1 and US1-2: start the job by hand against a
database that does not hold the last trading day. The execution succeeds, its log shows the start
line with mode, day and commit, the migrations are applied, and the day is stored. Rows US1-3 and
US1-5: a pull request runs checks only; a merge publishes `:<sha>` and the job shows that image.

### Tests for User Story 1 ⚠️ write first, see them fail

- [X] T008 [P] [US1] In `tests/unit/test_calendar.py`, add tests for `calendar.latest_completed_trading_day(now: datetime) -> date`. Every input is a timezone-aware datetime, and the expected value is the most recent NASDAQ trading day strictly before the New York calendar date of `now`:
  - Tue 2026-11-10 11:00 UTC → Mon 2026-11-09.
  - Sat 2026-11-14 11:00 UTC → Fri 2026-11-13.
  - Fri 2026-11-27 11:00 UTC (the day after Thanksgiving, an early-close trading day) → Wed 2026-11-25.
  - Fri 2026-11-27 23:30 New York (`2026-11-28 04:30 UTC`) → Wed 2026-11-25. A trading day's own date is never returned.
  - 2026-11-10 00:30 UTC (still 2026-11-09 in New York) → Fri 2026-11-06.
  - Both sides of the 2026-11-01 daylight-saving change: 2026-10-31 11:00 UTC and 2026-11-03 11:00 UTC.
  - A naive datetime raises `ValueError`.
- [X] T009 [P] [US1] Create `tests/unit/storage/__init__.py` and `tests/unit/storage/test_db.py` for `storage/db.py` (monkeypatch env vars and `psycopg.connect`, so no connection is opened):
  - (a) With `DATABASE_AUTH` unset, empty (`DATABASE_AUTH=`) or `password`, `connect()` calls `psycopg.connect(url, autocommit=True)` exactly as before.
  - (b) With `DATABASE_AUTH=entra`:
    - `ManagedIdentityCredential` (monkeypatched on `market_anomaly_agent.storage.db`) is constructed with `client_id=$AZURE_CLIENT_ID`.
    - `get_token` is called with `ENTRA_SCOPE`.
    - `psycopg.connect` receives the URL plus `password=<token>` and `autocommit=True`.
    - `db.issued_tokens()` then contains the token.
    - A second `connect()` calls `get_token` again.
  - (c) With `entra`, each of these raises `DatabaseConfigError` before `get_token`: `AZURE_CLIENT_ID` missing; `DATABASE_URL` containing a password; `DATABASE_URL` without `sslmode=verify-full`.
  - (d) Any non-empty `DATABASE_AUTH` value other than `password` or `entra` raises `DatabaseConfigError`.
  - (e) A `get_token` exception propagates (the CLI maps it to exit 1).
- [X] T010 [US1] Extend `tests/unit/test_cli.py`, following its existing fakes:
  - (a) `--date` together with `--latest` → exit 2. Neither → exit 2.
  - (b) `--latest` with `now=lambda: datetime(2026, 11, 10, 11, 0, tzinfo=UTC)` evaluates 2026-11-09. `run_detection` receives that day, and the output and exit code match a `--date 2026-11-09` run with the same fakes.
  - (c) The first INFO line is exactly `start mode=latest evaluated_date=2026-11-09 version=abc123` with `APP_COMMIT=abc123`, and ends `version=unknown` when unset. A `--date` run logs `mode=date`.
  - (d) `--migrate` calls a faked `apply_migrations` once, before `load_universe_tickers` or `load_run`, and logs `applied migration(s): …` or `none`. Without `--migrate` it is not called.
  - (e) `apply_migrations` raising → exit 1, nothing else called, and the message is scrubbed.
  - (f) A token registered through a faked `db.issued_tokens()` that appears in a forced exception message is printed as `***` in stderr, including the traceback.
- [X] T011 [US1] In `tests/integration/test_detect_end_to_end.py`, reuse the `make_detect` and `Detect` pattern, extended so `Detect` can pass extra argv and `now`. Add three tests:
  - (a) `--latest --migrate` on a fresh, unmigrated schema (use `db_conn`, not `migrated_conn`) creates every table, stores the resolved day, and `schema_migrations` lists `0001_init.sql`.
  - (b) A second `--migrate` run applies `none`.
  - (c) `--latest` for a day that is not stored produces the same stored rows as `--date` for that day (compare with `table_contents`).

### Implementation for User Story 1

- [X] T012 [P] [US1] Implement `latest_completed_trading_day(now: datetime) -> date` in `src/market_anomaly_agent/calendar.py`: raise `ValueError` if `now.tzinfo is None`, convert with `zoneinfo.ZoneInfo(_TZ)`, and return `previous_trading_day(ny_date)`. Makes T008 pass.
- [X] T013 [P] [US1] Implement Entra mode in `src/market_anomaly_agent/storage/db.py` per contracts/cli-contract.md (Environment) and data-model.md §1, making T009 pass:
  - The constants `AUTH_ENV_VAR`, `CLIENT_ID_ENV_VAR` and `ENTRA_SCOPE`.
  - A module-level `_issued: list[str]` and `issued_tokens() -> tuple[str, ...]`.
  - In `connect()`, read `DATABASE_AUTH`; unset or empty means `password` (`os.environ.get(AUTH_ENV_VAR) or "password"`). In `entra` mode, apply data-model.md §1's rules verbatim: "must have no password and must have `sslmode=verify-full`, otherwise exit 1 before connecting" (raise `DatabaseConfigError`), and require `AZURE_CLIENT_ID`. Then get a token with `ManagedIdentityCredential(client_id=…).get_token(ENTRA_SCOPE).token` (imported as `from azure.identity import ManagedIdentityCredential`), append it to `_issued`, and return `psycopg.connect(url, password=token, autocommit=True)`.
  - Never log the URL or the token. Update the module docstring.
- [X] T014 [US1] Update `src/market_anomaly_agent/cli.py` per contracts/cli-contract.md, making T010 pass:
  - A required mutually exclusive argparse group of `--date` and `--latest`, plus `--migrate`.
  - The `now` keyword parameter.
  - Resolve the day with `calendar.latest_completed_trading_day(now())` for `--latest`; keep the trading-day check for `--date`.
  - Log the start line (shared decisions).
  - Inside the existing `try`, with `--migrate`: `with connect() as conn: applied = apply_migrations(conn)`, then log `applied migration(s): <names or none>`.
  - Then continue into `_run`.
  - `_secrets()` also returns every value of `db.issued_tokens()`.
  - Update the module docstring to name both modes. Exit codes are unchanged.
- [X] T015 [US1] Run `uv run pytest`, `uv run pytest -m integration` (local PostgreSQL via `podman compose`, as in feature 001), `uv run ruff check`, `uv run ruff format --check` and `uv run mypy`. Everything must pass, T011 included. Fix failures in the files of T012 to T014 only.
- [X] T016 [P] [US1] Create `Containerfile` per research R12:
  - Stage `build` from `python:3.12-slim-bookworm`, the same image as the runtime stage, so the venv's interpreter path (`/usr/local/bin/python3.12`) matches. Add uv as uv's Docker guide recommends: `COPY --from=ghcr.io/astral-sh/uv:<version> /uv /uvx /bin/`, with `<version>` the exact uv release in use (`uv --version`, at least two weeks old; now `0.12.19`). *Corrected after the first host build:* the combined tag `ghcr.io/astral-sh/uv:0.12.19-python3.12-bookworm-slim` does not exist ("manifest unknown"), while `ghcr.io/astral-sh/uv:0.12.19` does. `WORKDIR /app`, copy `pyproject.toml`, `uv.lock`, `README.md` and `src/`, then `uv sync --frozen --no-dev --no-editable`.
  - Stage `runtime` from `python:3.12-slim-bookworm`: `apt-get install --no-install-recommends ca-certificates` and clean the apt lists, create user `app` (uid 10001), copy `/app/.venv` from `build`, and set `PATH=/app/.venv/bin:$PATH`.
  - CA guard (research R8): right after installing `ca-certificates`, a `RUN` step fails the build unless `/etc/ssl/certs/ca-certificates.crt` contains both roots Microsoft documents for Azure Database for PostgreSQL: `python -c "import ssl; s={dict(x[0] for x in c['subject']).get('commonName') for c in ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt').get_ca_certs()}; missing={'DigiCert Global Root G2','Microsoft RSA Root Certificate Authority 2017'}-s; assert not missing, missing"`. Do not set `PGSSLROOTCERT`, `SSL_CERT_FILE` or `SSL_CERT_DIR`: the job's `DATABASE_URL` names the file (`sslrootcert=/etc/ssl/certs/ca-certificates.crt`, T018), because `sslrootcert=system` does not find a store with the wheel's bundled OpenSSL.
  - `ARG GIT_SHA=unknown`, `ENV APP_COMMIT=$GIT_SHA`, and `LABEL org.opencontainers.image.revision=$GIT_SHA` and `org.opencontainers.image.source=https://github.com/XaverPilgrimm/market-anomaly-agent`.
  - `USER app`, `ENTRYPOINT ["detect"]`, no `CMD`.
  - No `ENV` with any secret (FR-005).
- [X] T017 [US1] Implement `infra/modules/data.bicep` per data-model.md §3 "`rg-maa-data`":
  - User-assigned identity `id-maa-job`.
  - Server `psql-maa-<u>`: `sku { name: 'Standard_B1ms', tier: 'Burstable' }`, `version: '17'`, `storage { storageSizeGB: 32, autoGrow: 'Disabled' }`, `backup { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }`, `highAvailability { mode: 'Disabled' }`, `authConfig { activeDirectoryAuth: 'Enabled', passwordAuth: 'Disabled', tenantId: tenant().tenantId }`, `network { publicNetworkAccess: 'Enabled' }`, and no `administratorLogin`.
  - Child `administrators/${dbAdminGroupObjectId}` with `principalType: 'Group'`, `principalName: dbAdminGroupName` and `tenantId: tenant().tenantId` (research R16).
  - Child `configurations`: `require_secure_transport` = `on` and `ssl_min_protocol_version` = `TLSv1.2`, deployed one after the other (`dependsOn`), because concurrent configuration updates conflict.
  - Firewall rules `AllowAllAzureServicesAndResourcesWithinAzureIps` (0.0.0.0–0.0.0.0) and `owner-access` (`ownerAccessIp`–`ownerAccessIp`).
  - Outputs: `serverFqdn`, `serverName`, `jobIdentityId`, `jobIdentityClientId`, `jobIdentityPrincipalId`.

  - *Fixed after the first real deployment:* the server and its `administrators`, `configurations` and `firewallRules` use API version `2025-08-01`, not `2026-10-01`. With `2026-10-01` the `maa-data` module failed with `ParameterOutOfRange`: "The value of the 'Version' should be in: []". That deployment ran in `germanywestcentral`, and the empty list most likely came from that region's restriction for this subscription (the probe of 2026-11-12 was refused there with "The location is restricted from performing this operation"), not from API `2026-10-01`. `2025-08-01` is kept because it is GA and proven in `francecentral` (research R2).

  No database resource (research R3). *Re-checked before the first deployment:* `infra/sql/bootstrap.sql` step 2 creates `market_anomaly_agent` (`CREATE DATABASE … \gexec` in the `postgres` phase, which `db-bootstrap.sh` runs before connecting to it), so `DATABASE_URL` names a database that exists once T028 step 2 has run. A `flexibleServers/databases` resource stays out on purpose: an ARM-created database would not be owned by `maa-db-admins`, so the bootstrap would skip `CREATE DATABASE` and its `GRANT … ON SCHEMA public` could fail. Until the bootstrap has run, a job execution fails with exit 1 and alerts (deployment-contract.md §6).
- [X] T018 [US1] Implement the US1 part of `infra/modules/env.bicep` per data-model.md §3 "`rg-maa-env`" and contracts/deployment-contract.md §4:
  - `log-maa` (`PerGB2018`, `retentionInDays: 30`).
  - `cae-maa`: consumption only, `appLogsConfiguration` to `log-maa` with its shared key via `listKeys`.
  - `kv-maa-<u>`: Standard, `enableRbacAuthorization: true`, `softDeleteRetentionInDays: 7`, `enablePurgeProtection` omitted or false.
  - Secret `twelve-data-api-key`, created only `if (!empty(twelveDataApiKey))`.
  - Role assignments: Key Vault Secrets User (`4633458b-17de-408a-b874-0445c86b69e6`) for `jobIdentityPrincipalId`, always scoped to the secret itself: an `existing` reference to `twelve-data-api-key`, with `dependsOn` the conditionally created secret, so the job never gets vault-wide read (*tightened during implementation*; it replaces the earlier "otherwise on the vault", and T025 therefore refuses an empty key while the secret does not exist); Key Vault Secrets Officer (`b86a8fe4-44ce-4948-aee5-eccb2c155cd7`) for `dbAdminGroupObjectId` on the vault with `principalType: 'Group'`. The job's assignment uses `principalType: 'ServicePrincipal'`.
  - Job `job-maa-detect` with the fixed job settings, user-assigned identity `jobIdentityId`, secret `price-data-api-key` with `keyVaultUrl: '${vault.properties.vaultUri}secrets/twelve-data-api-key'` and `identity: jobIdentityId`, image `jobImage`, and env:
    - `DATABASE_URL = 'postgresql://id-maa-job@${serverFqdn}:5432/market_anomaly_agent?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt'`
    - `DATABASE_AUTH = 'entra'`
    - `AZURE_CLIENT_ID = jobIdentityClientId`
    - `PRICE_DATA_API_KEY` from secretRef `price-data-api-key`

    No `registries` block. The job depends on the secret role assignment.
- [X] T019 [US1] Add the pipeline identity:
  - In `infra/main.bicep`, a custom role definition (`Microsoft.Authorization/roleDefinitions`, name `guid(subscription().id, 'maa-job-image-updater')`, role name `Market Anomaly Agent job image updater`), with `actions: ['Microsoft.App/jobs/read', 'Microsoft.App/jobs/write']` and `assignableScopes: [subscription().id]`. Pass its ID to env.bicep.
  - In `infra/modules/env.bicep`, identity `id-maa-github`.
  - Its child `federatedIdentityCredentials` `github-main`: issuer `https://token.actions.githubusercontent.com`, `audiences: ['api://AzureADTokenExchange']`, subject `'repo:${githubRepository}:ref:refs/heads/main'`.
    - *Fixed after the first pipeline run (2026-11-13):* `azure/login` failed with `AADSTS700213` ("No matching federated identity record found for presented assertion subject 'repo:XaverPilgrimm@<owner_id>/market-anomaly-agent@<repo_id>:ref:refs/heads/main'"). Cause: since 2026-07-15 GitHub issues immutable subject claims (names plus numeric owner and repository IDs, joined with `@`) by default for newly created repositories, and this repository is one of them; the credential still expected the name-based subject. Fix: new parameters `githubOwnerId` and `githubRepositoryId` (in `main.bicep`, `env.bicep` and, as `''` placeholders, `main.bicepparam`), and the subject `'repo:${githubOwner}@${githubOwnerId}/${githubRepoName}@${githubRepositoryId}:ref:refs/heads/main'`, with owner and name split from `githubRepository` (research R7). The ID parameters have no `@minLength(1)`: it was tried (2026-11-13) and made `deploy.sh` fail before the what-if with `BCP333` on the `''` placeholders in `main.bicepparam`, because Bicep checks the parameter file's values at compile time, before the `-p` overrides apply (a `readEnvironmentVariable(…, '')` default fails the same way). As for the other values `deploy.sh` passes, the guard is `deploy.sh`'s own check: it exits 1, before the what-if, unless both IDs are numeric.
  - A role assignment of the custom role to `id-maa-github` with `scope: job` (FR-008, FR-009, research R7).
  - The `AZURE_CLIENT_ID` output.
- [X] T020 [P] [US1] Create `infra/sql/bootstrap.sql` per research R3 steps 1–5. It is idempotent and driven by psql variables `:"job_role"` and `:"admin_role"`, and runs in two connections, split by the script with `\if :{?phase_db}`:
  - Phase `postgres`:
    - Create the principal only if `pg_roles` lacks it: `SELECT … pgaadauth_create_principal(:'job_role', false, false) \gexec`, guarded with `\if`.
    - `CREATE DATABASE market_anomaly_agent` only if it is missing (`\gexec`).
    - `GRANT CONNECT, TEMPORARY ON DATABASE market_anomaly_agent TO :"job_role"`.
    - `REVOKE ALL ON DATABASE market_anomaly_agent FROM PUBLIC`.
    - `GRANT :"job_role" TO :"admin_role" WITH INHERIT TRUE, SET TRUE`.
  - Phase `market_anomaly_agent`: `GRANT USAGE, CREATE ON SCHEMA public TO :"job_role"`.
  - Finally, a `DO` block that raises an exception unless `has_schema_privilege(:'job_role', 'public', 'CREATE')` and `pg_has_role(current_user, :'job_role', 'USAGE')` are both true. psql runs with `-v ON_ERROR_STOP=1`.
- [X] T021 [P] [US1] Create `infra/scripts/db-access.sh` (sources `_common.sh`; `open|close`):
  - `open` gets the owner's public IP from `curl -4 -fsS https://api.ipify.org`, checks it is a valid IPv4 address, and runs `az postgres flexible-server firewall-rule update --resource-group rg-maa-data --server-name <server> --name owner-access --start-ip-address <ip> --end-ip-address <ip>`.
    - *Fixed after the first deployment (2026-11-12):* `db-access.sh open` failed with "unrecognized arguments: -r owner-access". In the current Azure CLI, the `firewall-rule` commands take the server as `--server-name` (`-s`) and the rule as `--name` (`-n`); `--rule-name`/`-r` no longer exists, and `-n` now means the rule, not the server (confirmed with `--help` on the host). Every `firewall-rule` call (open and close share one) now uses the long forms `--resource-group`, `--server-name` and `--name`. The IP lookup asks for IPv4 explicitly (`curl -4`), because firewall rules accept IPv4 only, and exits 1 with a clear message, changing nothing, if no IPv4 address comes back. No other `az` call in `infra/scripts`, quickstart.md or tasks.md used the old syntax.
  - `close` sets `192.0.2.1`.
  - The rule is never deleted, because the lock blocks deletion (research R14).
  - The server name is read with `az postgres flexible-server list -g rg-maa-data --query "[0].name" -o tsv`.
- [X] T022 [P] [US1] Create `infra/scripts/entra-bootstrap.sh` (sources `_common.sh`; `require_az_login`) per contracts/deployment-contract.md §2a and research R16. It is idempotent:
  - `uid=$(az ad signed-in-user show --query id -o tsv)`, read at run time and never printed.
  - Create the security group if `az ad group show --group maa-db-admins` fails: `az ad group create --display-name maa-db-admins --mail-nickname maa-db-admins`.
  - Add the signed-in user unless `az ad group member check --group maa-db-admins --member-id "$uid" --query value -o tsv` prints `true`: `az ad group member add --group maa-db-admins --member-id "$uid"`.
  - If `az ad group member list --group maa-db-admins --query "[].id" -o tsv` lists any ID other than `$uid`, exit 1 with `maa-db-admins must have exactly one member` and change nothing.
  - Print `admin group ok`. No object, tenant or subscription ID appears in output or in the file.
- [X] T023 [US1] Create `infra/scripts/db-bootstrap.sh` (sources `_common.sh`, depends on T020):
  - Get `PGPASSWORD` with `az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv`.
  - Run `bootstrap.sql` through `pg_client psql "host=<server-fqdn> dbname=postgres user=maa-db-admins sslmode=verify-full sslrootcert=/etc/ssl/certs/ca-certificates.crt" -v ON_ERROR_STOP=1 -v job_role=id-maa-job -v admin_role=maa-db-admins -v phase_db=postgres -f -` (file piped on stdin), then again with `dbname=market_anomaly_agent` and without `phase_db`.
  - Read the server FQDN at run time (`az postgres flexible-server list -g rg-maa-data --query "[0].fullyQualifiedDomainName" -o tsv`).
  - On an authentication failure of the first connection, print the check Q7 fallback from quickstart.md §2 (work account in the group) and exit 1.
  - Print `bootstrap ok` only if both succeed. Remind the owner to run `db-access.sh close`.
  - *Fixed after the first deployment (2026-11-12):* `db-bootstrap.sh` failed with "SSL error: certificate verify failed". Cause: `pg_client` in `_common.sh` runs `psql` in `docker.io/library/postgres:17`, and `sslrootcert=system` finds no trusted roots in that image: the owner checked, and the image's `/etc/ssl/certs` holds only `ssl-cert-snakeoil.pem` and its hash link (`ce275665.0`), no CA bundle. The server itself was fine: on the host, `openssl s_client -starttls postgres` verified it (`Verify return code: 0`). Observed chain: the server certificate, then `Microsoft TLS G2 RSA CA OCSP 10`, then `Microsoft TLS RSA Root G2`, then `DigiCert Global Root G2`. Fix: `pg_client` first checks that `/etc/ssl/certs/ca-certificates.crt` exists (and is non-empty) on the host, exiting 1 with a message if not, then mounts it read-only into the container at the same path (`-v /etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-certificates.crt:ro`, no SELinux relabel option). Every connection string in `infra/scripts` now uses `sslrootcert=/etc/ssl/certs/ca-certificates.crt` (via `PG_CA_BUNDLE` in `_common.sh`), the same path the job's `DATABASE_URL` uses, instead of `sslrootcert=system`. The host-side docs were changed to match: research R8, the owner query in deployment-contract.md §6, and T044.
- [X] T024 [US1] Create `infra/scripts/preflight.sh <region>` (sources `_common.sh`) for quickstart check Q1. Print PASS or FAIL per item and exit non-zero on any FAIL:
  - `az postgres flexible-server list-skus -l <region>` must list `Standard_B1ms` in tier `Burstable`, and version `17` must be supported in that region.
  - `az provider show -n Microsoft.App --query "resourceTypes[?resourceType=='jobs'].locations"` must contain the region's display name.
  - It prints the subscription's display name (`az account show --query name -o tsv`), never its ID or the tenant ID.
- [X] T025 [US1] Create `infra/scripts/deploy.sh` (depends on T022) per contracts/deployment-contract.md §3 (sources `_common.sh`; `require_az_login`):
  - *Added later:* pass `githubRepository` from `gh repo view --json nameWithOwner -q .nameWithOwner` (run in the repository root), not from `main.bicepparam`, and exit 1 if `gh` cannot read it. The federated credential's subject must equal the OIDC token's `sub` claim exactly, including case. The GHCR image name in `ci.yml` stays lowercase, as GHCR requires. The what-if header prints the repository name.
  - First run `infra/scripts/entra-bootstrap.sh`, and stop if it fails.
  - `dbAdminGroupObjectId` from `az ad group show --group maa-db-admins --query id -o tsv`, at run time.
  - Alert email from `$MAA_ALERT_EMAIL`, or `read -r` if unset.
  - Refuse to deploy (exit 1, before the what-if) when `dbAdminGroupObjectId` or the alert email is empty. `infra/main.bicepparam` sets both to `''` only because a parameter file must give a value for every required parameter, so an empty value always means the run-time lookup or the prompt failed.
  - Vendor key from `IFS= read -rs` (`IFS=` so leading and trailing whitespace is kept and caught, not trimmed); empty keeps the stored secret. *Added before the first deployment:* a non-empty key containing any whitespace is refused (exit 1, nothing deployed). Otherwise the script prints only the key's length and its last 4 characters (`key read: N characters, ending in ...XXXX`), before the what-if, so the owner can compare it with `.env` before confirming. It never prints more. Refuse to deploy when the key is empty and `twelve-data-api-key` does not exist yet (first deployment, or after teardown purged the vault), because the job's role assignment is scoped to that secret (T018).
  - `jobImage` from `az containerapp job show -n job-maa-detect -g rg-maa-env --query "properties.template.containers[0].image" -o tsv`, falling back to the placeholder when the job does not exist.
  - Run `az deployment sub what-if -l <location> -n maa-<timestamp> -f infra/main.bicep -p infra/main.bicepparam -p dbAdminGroupObjectId=… alertEmail=… jobImage=… twelveDataApiKey="$KEY"`.
  - Then run `az deployment sub create … --confirm-with-what-if` with the same parameters.
  - Print the outputs, then offer `gh variable set AZURE_CLIENT_ID|AZURE_TENANT_ID|AZURE_SUBSCRIPTION_ID --body …` (y/N each).
    - *Fixed after the first deployment (2026-11-12):* `gh variable set` prompted "Paste your variable", because the value passed with `--body` was empty, and `gh` prompts when it is. Cause, confirmed on the host: `az deployment sub show` returns the output names as `azurE_CLIENT_ID`, `azurE_TENANT_ID` and `azurE_SUBSCRIPTION_ID` (`serverFqdn` and `jobName` come back unchanged), so the exact-name JMESPath query `properties.outputs.AZURE_CLIENT_ID.value` found nothing. Now the script reads `properties.outputs` once as JSON, looks each name up case-insensitively with `jq` (now checked for at the start, like `preflight.sh`), and still asks y/N first. On y it refuses an empty value with a message and leaves that variable unset, and otherwise runs `gh variable set "$var" --body "$value" </dev/null`, so nothing is ever interactive. That first time the owner set the three variables by hand: `AZURE_CLIENT_ID` from `id-maa-github`, plus `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`.
  - Print the next steps: `db-access.sh open`, `db-bootstrap.sh`, `db-access.sh close`, `gh workflow run ci.yml --ref main`, and set the GHCR package to public.
  - *Changed after the first pipeline run (2026-11-13):*
    - **OIDC IDs.** After `gh repo view`, read the owner and repository IDs with `gh api repos/<owner>/<repo> --jq '[(.owner.id|tostring),(.id|tostring)]|@tsv'`, exit 1 unless both are numeric, and pass them as `githubOwnerId` and `githubRepositoryId` (T019 fix: `AADSTS700213`, immutable subject claims).
    - **Secrets, not variables.** The first `publish-deploy` log, which is public, showed `client-id`, `tenant-id` and `subscription-id` in the `azure/login` inputs. The y/N offer per value now runs `printf '%s' "$value" | gh secret set "$var" --repo "$repository"`: the value goes on stdin, so it is never printed, never on a command line and never prompted for; an empty value is refused as before. Then a y/N offer for `gh variable set AZURE_DEPLOY_ENABLED --body true` (the gate for T026), and, for each old repository variable `AZURE_CLIENT_ID|AZURE_TENANT_ID|AZURE_SUBSCRIPTION_ID` that still exists, a y/N offer to delete it. All `gh` calls name `--repo "$repository"`.
    - *Fixed after the first deployment (2026-11-13):* `deploy.sh` never offered to delete the old repository variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`, although they existed; the owner deleted them by hand. Cause: the existence check was `if gh variable get "$var" --repo … >/dev/null 2>&1`, which treats *any* failure of `gh variable get` as "no such variable" and hides the error. The exact error was not captured; the likeliest one is a `gh` too old to have the `variable get` subcommand. Fix: the names are listed once through the REST API, `gh api repos/<owner>/<repo>/actions/variables --paginate --jq '.variables[].name'` (names only, values never fetched), and each offer checks that list. If the listing fails, the script says so and tells the owner to check by hand, instead of skipping silently.
    - **Job image on a redeploy.** Checked: a rerun after `publish-deploy` has switched the job already kept its image, because `jobImage` is read from the job. But the lookup's `2>/dev/null || true` turned any failure into the placeholder, which would have rolled the job back. Now `az group exists`, `az resource list --resource-type Microsoft.App/jobs` and `az resource show` (no containerapp extension needed) decide: the placeholder only when `rg-maa-env` or the job does not exist; a failed lookup, or an existing job whose image cannot be read, exits 1 before the what-if.
  - Never echo the key, the email or any object, tenant or subscription ID. The `gh variable set` values are passed straight from the deployment outputs.
- [X] T026 [US1] Add the `publish-deploy` job to `.github/workflows/ci.yml` per contracts/deployment-contract.md §5:
  - *Added later:* `concurrency: { group: publish-deploy, cancel-in-progress: false }`, so two quick merges deploy one after the other and no deployment is cancelled midway. GitHub keeps one run in progress and one pending; a third queued run replaces the pending one, so the newest commit is the one that ends up deployed.
  - `needs: checks`; `if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main' && vars.AZURE_CLIENT_ID != '' && vars.AZURE_TENANT_ID != '' && vars.AZURE_SUBSCRIPTION_ID != ''`. *Added after implementation:* while any of the three repository variables that `deploy.sh` sets is empty (before the first deployment), the whole job is skipped, so the merge to `main` before T028 shows a skipped job rather than a failed run. Nothing is pushed in that case. After `deploy.sh` has set the variables, `gh workflow run ci.yml --ref main` (T028) runs it; `permissions: contents: read, packages: write, id-token: write`; no `environment:` key.
  - Steps (actions pinned to commit SHAs):
    1. Checkout.
    2. `docker/login-action` to `ghcr.io` with `${{ github.actor }}` and `${{ secrets.GITHUB_TOKEN }}`.
    3. `docker build --build-arg GIT_SHA=${{ github.sha }} -f Containerfile -t ghcr.io/xaverpilgrimm/market-anomaly-agent:${{ github.sha }} .`, then `docker push` of that tag.
    4. `azure/login` with `client-id: ${{ vars.AZURE_CLIENT_ID }}`, `tenant-id: ${{ vars.AZURE_TENANT_ID }}` and `subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}`.
  - *Changed after the first pipeline run (2026-11-13):* the `azure/login` step printed the three values in its inputs, and this repository's Actions logs are public. They are now `${{ secrets.AZURE_CLIENT_ID }}`, `${{ secrets.AZURE_TENANT_ID }}` and `${{ secrets.AZURE_SUBSCRIPTION_ID }}`, masked in the log. The `secrets` context is not available in a job-level `if`, so the condition is now `github.event_name != 'pull_request' && github.ref == 'refs/heads/main' && vars.AZURE_DEPLOY_ENABLED == 'true'`: a non-sensitive gate that `deploy.sh` sets (T025), so the job is still skipped, not failed, until Azure is set up. A new first step, `Check the Azure sign-in secrets`, fails with `::error::` naming any empty secret before anything is pushed. The federated subject is now the immutable one (T019 fix; research R7).
    5. `az containerapp job update -n job-maa-detect -g rg-maa-env --image ghcr.io/xaverpilgrimm/market-anomaly-agent:${{ github.sha }}`.
- [X] T027 [US1] (owner, host) Run the pre-deployment checks from quickstart.md §1:
  - Q1: (a) `infra/scripts/preflight.sh francecentral`, the listing, a first filter only; (b) the decisive probe from quickstart.md Q1: create a B1ms PostgreSQL 17 server in the throwaway group `rg-maa-probe`, then delete the group. On failure of either, repeat with `italynorth` and set `location` in `infra/main.bicepparam` to it.
    - *Probed 2026-11-12:* the listing had passed `germanywestcentral`, but its probe was refused ("The location is restricted from performing this operation"). `westeurope` is "not accepting new customers"; `northeurope`, `swedencentral` and `polandcentral` returned `SkuNotAvailable` for `Standard_B1ms`. `francecentral` and `italynorth` created the server (both deleted again). `location` is now `francecentral`, with `italynorth` as the fallback (research R2). *Q1 passed (2026-11-13):* the France Central probe server was created; Q1 is done for `francecentral`.
  - Q2: price the resources in the Azure pricing calculator in EUR. The total must be ≤ EUR 24.
    - *Q2 passed (2026-11-13):* France Central, PostgreSQL B1ms 730 h with Premium SSD 32 GiB about EUR 16.30 per month; Container Apps, Log Analytics and Key Vault within free grants or a few cents. The two log alert rules are not priced yet and must be added to the estimate in research R10 when they arrive with US3 (T034–T036).
  - Q6: run `infra/scripts/deploy.sh` and answer **n** at the confirmation. (Expect that the later real run in T028 may need a second attempt while a role assignment propagates; see quickstart.md Q6.) `entra-bootstrap.sh` must print `admin group ok` (the group `maa-db-admins` now exists with you as its only member). The what-if must list only data-model.md §3 resources, with a `Group` Entra administrator.
    - *Q6 passed (2026-11-13)* during the first deployment.

  The region is recorded in research R2. Record the real EUR prices for France Central in `specs/002-azure-daily-run/research.md` (R10).
- [X] T028 [US1] (owner, host) Run the first deployment from quickstart.md §2:
  1. `infra/scripts/deploy.sh` (answer **y**), and set the three GitHub variables. From this first run on, `rg-maa-data` carries the CanNotDelete lock (T041, pulled forward), so starting over by deleting that group needs `az lock delete -n lock-maa-data -g rg-maa-data` first. The first run can also fail while the new Key Vault role assignment for `id-maa-job` propagates (for example, the job cannot read its secret reference); `deploy.sh` is idempotent, so wait a few minutes and run it again.
  2. `infra/scripts/db-access.sh open`, `infra/scripts/db-bootstrap.sh` (must print `bootstrap ok`; this is check Q7 part 1, the first sign-in as `maa-db-admins`; on failure follow the work-account fallback in quickstart.md §2), `infra/scripts/db-access.sh close`.
  3. `gh workflow run ci.yml --ref main`, then set the GHCR package `market-anomaly-agent` to public.
     - *Corrected 2026-11-13:* `gh workflow run` runs the `ci.yml` on `main`, so it is only valid once `main` has the current workflow. For the recovery from `AADSTS700213` (T019, T025, T026 fixes) the order is: `deploy.sh` (answer **y** to the secrets, `AZURE_DEPLOY_ENABLED` and deleting the old variables), then a pull request from `002-azure-daily-run` to `main`; the merge's push to `main` runs `publish-deploy`. Running `gh workflow run ci.yml --ref main` before the merge would run the old, vars-based job.
  4. Check Q7 part 2 (quickstart.md §2): with `db-access.sh open`, a `verify-full` connection from the published image using `sslrootcert=/etc/ssl/certs/ca-certificates.crt` succeeds and reports `ssl = true`, and the same connection with `sslrootcert=/dev/null` fails with a certificate error. Then `db-access.sh close`. On failure, apply the fallback in research R8.
  5. Check Q5: `publish-deploy` succeeds with the custom role only. If it fails with an authorization error, add exactly the missing action to the role in `infra/main.bicep`, record it in research R7, and redeploy.

  *Done 2026-11-13:* `db-bootstrap.sh` printed `bootstrap ok`, so Q7 part 1 passed: the personal account signs in through `maa-db-admins`, and the work-account fallback is not needed. Q7 part 2 passed: with the CA bundle the check printed `(True, 'TLSv1.3')`, with `/dev/null` it failed with "could not read root certificate file" (research R8). Q5 passed: `publish-deploy` succeeded with the custom role only; `Microsoft.App/managedEnvironments/join/action` was not needed (research R7).
- [X] T029 [US1] (owner, host) Validate US1 per quickstart.md §4 rows US1-1/US1-2 and US1-3/US1-5/SC-010:
  - A manual `az containerapp job start -n job-maa-detect -g rg-maa-env` succeeds, its log shows the start line with the commit and the applied migration, and the owner query shows the day in `detection_runs`.
  - A pull request runs `checks` only.
  - A merge publishes `:<sha>` and the job shows that image.

  *Done 2026-11-13:* a manual execution of `job-maa-detect` succeeded in about 15 minutes. Its log shows `start mode=latest` with the version matching the image tag, `applied migration(s): 0001_init.sql`, and a final `status=completed` line. The owner query (deployment-contract.md §6) shows one `detection_runs` row for the evaluated day. (No results are recorded here: the repository is public.)

**Checkpoint**: the daily run works end to end. A repeated start still re-fetches and reproduces
the day; US2 removes that cost.

---

## Phase 4: User Story 2 - Repeated starts and non-trading days cost nothing (Priority: P1)

**Goal**: in `--latest` mode, a day that is already stored exits 0 with no vendor request, no
write and no exit 3 (FR-002).

**Independent Test**: quickstart.md §4, rows US2/SC-002 and US2/SC-003: a second start right after
a successful one finishes in under 2 minutes, uses 0 vendor credits and adds no rows.

### Tests for User Story 2 ⚠️ write first, see them fail

- [X] T030 [P] [US2] Extend `tests/unit/test_cli.py` with a faked `load_run` that returns a `StoredRun` for the resolved day, under `--latest`:
  - The source factory is never called, `PRICE_DATA_API_KEY` may be unset, `load_universe_tickers`, `run_detection`, `load_bar_snapshot` and `save_run` are not called, and the exit code is 0.
  - The last stdout line equals the stored summary.
  - The INFO line `2026-11-09 already stored; nothing fetched` is logged.
  - The holiday case: `now` on Fri 2026-11-27 11:00 UTC resolves to 2026-11-25, which is stored, so exit 0.
  - With `--date` and the same stored run, the 001 reproduce path still runs (`run_detection` is called).
- [X] T031 [P] [US2] Extend `tests/integration/test_detect_end_to_end.py`:
  - After a first `--latest` run stores the day, change a bar in the fake source (as the existing exit-3 test does). A second `--latest` run exits 0 (not 3), the fake source records 0 calls, and `table_contents` is unchanged.

### Implementation for User Story 2

- [X] T032 [US2] In `src/market_anomaly_agent/cli.py`, for `--latest` after the optional migrations and before reading `PRICE_DATA_API_KEY`: open `connect()`, `load_run(conn, day)`, and if it is found, log the already-stored line, print `_stored_summary(stored)` and return `EXIT_OK`. Otherwise call `_run` unchanged. Move the API-key check so it is reached only on the fetch path. Makes T030 and T031 pass. Then rerun the T015 command set.
  *Done 2026-11-14:* the `--latest` check sits in `main` after the migrations; `--date` is unchanged and still re-fetches and reproduces (or exits 3). The API-key check was already inside `_run`, so it is now reached only on the fetch path. The key stays the evaluated day alone: a stored run with an older rule version is still skipped (001 FR-018, 002 FR-002); re-evaluating after a rule change would be a future spec change. Cause recorded from Azure: the 2026-11-13 scheduled start re-fetched every series for about 14 minutes before reproducing the stored day. T015 set: ruff, format and mypy clean; 163 unit and 37 integration tests pass.
- [X] T033 [US2] (owner, host) Validate quickstart.md §4 rows US2/SC-002 and US2/SC-003 after the pipeline has deployed the T032 image: a second manual start succeeds in under 2 minutes, with "already stored" in the log, 0 new Twelve Data credits and unchanged row counts.
  *Done 2026-11-14:* after the US2 deploy (image 3e756e8f), a manual start succeeded in 39 s with "already stored; nothing fetched" in the log, no `fetched` lines and 0 new Twelve Data credits (quickstart.md §4, row US2/SC-002). The owner query (deployment-contract.md §6) after the start shows the `detection_runs` row count unchanged and its latest `computed_at` still from the 11:00 UTC scheduled run, so nothing was written. SC-003 is covered by the T030 holiday unit test; the scheduled run on 2026-11-27 can confirm it on Azure.

**Checkpoint**: repeated starts and holidays are free; US1 and US2 together are the complete daily
run.

---

## Phase 5: User Story 3 - A failed run is retried a bounded number of times and reported (Priority: P2)

**Goal**: at most 3 attempts per execution, a time limit that fits all 3, and an email within
15 minutes when an execution fails (FR-011, FR-022, SC-004, SC-011).

**Independent Test**: quickstart.md §3 Q3 and Q4, and §4 row US3/SC-008: with an invalid key, an
execution makes at most 3 attempts, stores nothing, and the owner gets a failure email.

### Implementation for User Story 3

- [X] T034 [US3] In `infra/modules/env.bicep`, add action group `ag-maa-owner`: short name `maa`, one email receiver from `alertEmail`, `useCommonAlertSchema: true`.
- [X] T035 [US3] In `infra/modules/env.bicep`, add scheduled query rule `alert-maa-execution-failed` (research R9):
  - Scope `log-maa`; `evaluationFrequency: 'PT5M'`; `windowSize: 'PT10M'`; severity 2; action `ag-maa-owner`; `autoMitigate: false`.
  - Query: `ContainerAppSystemLogs_CL | where JobName_s == 'job-maa-detect' | where Reason_s has_any (dynamic(['Failed','BackOff','Error','Timeout','DeadlineExceeded'])) or Log_s has_any (dynamic(['failed','timed out','deadline'])) | project TimeGenerated, ExecutionName_s, Reason_s, Log_s`, with the condition count > 0.
  - Add a Bicep comment that the reason strings are provisional until check Q4 (T039) pins them.
  - *Done 2026-11-14 (T034–T036, not built or deployed yet: no `az` in the agent container):* API versions `actionGroups@2023-01-01` and `scheduledQueryRules@2026-03-01` (newest GA in the template reference). Both rules are `kind: 'LogAlert'` with `timeAggregation: 'Count'`, `operator: 'GreaterThan'`, `threshold: 0` and `skipQueryValidation: true` (a recreated workspace has no `_CL` tables yet). The action group's address is the `alertEmail` deploy input only.
- [X] T036 [US3] In `infra/modules/env.bicep`, add scheduled query rule `alert-maa-no-completed-run` (research R9):
  - Scope `log-maa`; `evaluationFrequency: 'PT1H'`; `windowSize: 'PT2H'`; severity 2; action `ag-maa-owner`.
  - The query returns one row only when all of these hold:
    - `dayofweek(now())` is Tuesday to Saturday (2d to 6d);
    - `now()` is between today's 12:45 and 13:45 UTC;
    - `ContainerAppConsoleLogs_CL | where ContainerJobName_s == 'job-maa-detect' and TimeGenerated >= startofday(now()) + 11h and Log_s has 'status=completed'` returns no rows.

    Write it as `let completed = toscalar(…| count); print fire = …| where fire`. Add a comment that the column name `ContainerJobName_s` is confirmed in Q4.
  - *Pinned 2026-11-16 (T035) from check Q4:* the query is now `ContainerAppSystemLogs_CL | where JobName_s == 'job-maa-detect' and Reason_s in ('BackoffLimitExceeded', 'DeadlineExceeded') | summarize arg_max(TimeGenerated, Reason_s, Log_s) by ExecutionName_s`, so one failed execution gives one row instead of one per attempt (`Error`). `muteActionsDuration: 'PT1H'` with `autoMitigate: false` stops the overlapping 5-minute/10-minute evaluation from sending a duplicate email, which Q4 observed. The provisional comments are gone from both rules.
  - *Changed 2026-11-14:* `windowSize` is `PT3H`, not `PT2H`: the window also filters the tables, so an evaluation at 13:45 with `PT2H` would miss a `status=completed` line from 11:14 and alert falsely. The match is `Log_s contains 'status=completed'` instead of `has`, which matches whole terms (`=` is a separator). Recorded in research R9.
- [X] T037 [US3] (owner, host) Run check Q3 from quickstart.md §3: create a temporary job copy in `rg-maa-env` with `replicaTimeout 120`, `replicaRetryLimit 2` and image `docker.io/library/busybox:1.37` running `sh -c 'sleep 50; exit 1'`. Start it once, count the replicas, and delete it.
  - 3 replicas: set `jobReplicaTimeoutSeconds = 1800` in `infra/main.bicepparam` and redeploy.
  - 2 replicas: keep 3600.

  Record the observed behaviour and the final value in `specs/002-azure-daily-run/research.md` R1.

  *Done 2026-11-13:* 2 replicas ran, started 11 s and 79 s after the execution start; the second was cut off at 120 s and no third started. The timer is per execution, so `replicaTimeout` stays `3600` (research R1). `az containerapp job create` rejected `--args "-c" …` with "unrecognized arguments", because a value starting with `-` is parsed as an option, so the copy was created with `--yaml` from a file outside the repository (quickstart.md §3, "Q3 job copy").
- [X] T038 [US3] (owner, host) Redeploy with `infra/scripts/deploy.sh` so the alerts from T034–T036 exist (empty key input keeps the secret).
  *Done 2026-11-14:* deployed; the action group's confirmation email and the Q4 alert email both arrived.
- [X] T039 [US3] Run check Q4 from quickstart.md §3, in two parts:
  - (owner, host) Start the job once with failing args (`--date 2026-11-08`, a Sunday, exit 2 on every attempt). Confirm a failure email within 15 minutes. Then query `log-maa` and copy the real `Reason_s` values and column names of the failed attempts and execution.
  - (agent, from the owner's notes) Pin those values in the queries of `infra/modules/env.bicep` (T035 and T036), and record them in research R9.
  - *Note 2026-11-14, open:* the quickstart.md Q4 command `az containerapp job start … --args "--date" "2026-11-08"` will likely fail with "unrecognized arguments", as `--args "-c" …` did in Q3 (T037), because the Azure CLI parses a value that starts with `-` as its own option. Check `az containerapp job start --help` on the host and decide the override method (for example `--yaml`, or `--args=…`) before running Q4; fix the Q4 row then.
  - *Updated 2026-11-14:* quickstart.md §3 now has a "Q4 failing start" block using `--args=--date=2026-11-08` with `--image`, `--container-name`, `--cpu` and `--memory` given explicitly, plus a `--yaml` fallback. It is unverified until checked with `--help` on the host.
  - *Done 2026-11-14/16:* the `--args=` form worked (no "unrecognized arguments"); 3 failed attempts; first email about 8 minutes after the start, plus one duplicate. Columns `JobName_s` and `ContainerJobName_s` confirmed; reasons `Error` (per attempt), `BackoffLimitExceeded` and, from Q3, `DeadlineExceeded` (per execution). Pinned in `infra/modules/env.bicep` (see T035) and recorded in research R9 and quickstart.md Q4. `Microsoft.Insights` was `NotRegistered` and was registered by hand on 2026-11-14; `infra/scripts/preflight.sh` now checks the providers the templates use (Microsoft.App, Microsoft.Consumption, Microsoft.DBforPostgreSQL, Microsoft.Insights, Microsoft.KeyVault, Microsoft.ManagedIdentity, Microsoft.OperationalInsights) and prints `az provider register -n <namespace> --wait` for each missing one. Not yet deployed: redeploy with `deploy.sh` (T038 path) to apply the pinned rule.
- [ ] T040 [US3] (owner, host) Validate quickstart.md §4 row US3/SC-008: set an invalid value with `az keyvault secret set`, start once, and confirm ≤ 3 attempts, nothing stored and a failure email. Then restore the real key.

**Checkpoint**: failures are bounded, stopped in time and reported.

---

## Phase 6: User Story 4 - The whole environment can be deleted and recreated from the repository (Priority: P2)

**Goal**: the environment group can be torn down and rebuilt at once, the database group is locked
and survives, and a separate decommission exports before deleting, with a restore path (FR-024 to
FR-027, FR-030 to FR-032).

**Independent Test**: quickstart.md §4, rows US4-2 to US4-5 and SC-005, and §5 / SC-009.

### Implementation for User Story 4

- [X] T041 [US4] In `infra/modules/data.bicep`, add `Microsoft.Authorization/locks` `lock-maa-data` at resource-group scope (no `scope` property, so it applies to `rg-maa-data`) with `level: 'CanNotDelete'` and notes `Stored detection results; see deployment-contract.md §6 (decommission)`. It is deployed on every run, so it is in place after every deployment (FR-030). *Pulled forward into US1 (before T028):* the database holds the research record from the first run on, so the lock is part of the first deployment. The teardown, decommission and restore scripts (T042–T044) stay in US4.
- [ ] T042 [P] [US4] Create `infra/scripts/teardown.sh` (sources `_common.sh`):
  - Print what will be deleted and require typing `rg-maa-env` to confirm.
  - `az group delete -n rg-maa-env --yes`, then purge the soft-deleted vault: `az keyvault list-deleted --query "[?name=='kv-maa-<u>']"`, then `az keyvault purge -n <name> -l <location>`. Read the vault name from `az keyvault list -g rg-maa-env` before the delete.
  - Never reference `rg-maa-data`, and say so in a comment (FR-027).
- [ ] T043 [P] [US4] Create `infra/scripts/decommission.sh <dump-path>` (sources `_common.sh`):
  1. Resolve the path with `realpath -m`. Refuse it if it is inside `repo_root`. Refuse an existing file.
  2. Run `teardown.sh`.
  3. `db-access.sh open`.
  4. `pg_dump -Fc` signed in as `maa-db-admins` with the owner's token, writing to the path through a bind-mounted directory of the `pg_client` container.
  5. Verify the file is non-empty and that `pg_restore --list` exits 0.
  6. Require typing `rg-maa-data` to confirm.
  7. `az lock delete -n lock-maa-data -g rg-maa-data`, then `az group delete -n rg-maa-data --yes`.

  Stop at the first failure, before step 7. Never upload the file (FR-031).
- [ ] T044 [P] [US4] Create `infra/scripts/restore.sh <dump-path>` (sources `_common.sh`):
  - Require an existing, non-empty file.
  - `db-access.sh open`.
  - Refuse if `SELECT to_regclass('public.detection_runs')` is non-null and the table has rows.
  - `pg_restore --no-owner --role=id-maa-job --exit-on-error -d "host=<server-fqdn> dbname=market_anomaly_agent user=maa-db-admins sslmode=verify-full sslrootcert=/etc/ssl/certs/ca-certificates.crt"` through `pg_client` (T023 fix).
  - Print `SELECT count(*) FROM detection_runs`, then `db-access.sh close` (FR-032).
- [ ] T045 [US4] (owner, host) Validate quickstart.md §4 rows US4-2, US4-3/4/SC-005 and US4-5/FR-030:
  - A rerun of `deploy.sh` shows a what-if with no changes.
  - `teardown.sh`, then at once `deploy.sh` and `gh workflow run ci.yml --ref main`, then a manual start, reports the last stored day as "already stored", with all earlier days present and no bootstrap needed.
  - `az group delete -n rg-maa-data` is refused by the lock.
- [ ] T046 [US4] (owner, host, optional; only when actually pausing) Validate quickstart.md §5 / SC-009: decommission to a path outside the repository, redeploy, bootstrap, restore, and compare the `detection_runs` counts.

**Checkpoint**: the environment is disposable, and the results are not.

---

## Phase 7: User Story 5 - Spending stays within the constitution's caps and is visible (Priority: P3)

**Goal**: a EUR 30 monthly budget with actual-cost notifications at 50, 80 and 100%, and a
documented cost estimate below EUR 30 (FR-028, FR-029, SC-006).

**Independent Test**: quickstart.md §4 row US5/FR-028.

### Implementation for User Story 5

- [ ] T047 [US5] In `infra/main.bicep`, add `Microsoft.Consumption/budgets` `budget-maa-monthly` at subscription scope:
  - `category: 'Cost'`, `amount: budgetAmountEur`, `timeGrain: 'Monthly'`, `timePeriod { startDate: budgetStartDate }`.
  - Three notifications, `actual50`, `actual80` and `actual100`: `enabled: true`, `operator: 'GreaterThanOrEqualTo'`, `threshold` 50, 80 and 100, `thresholdType: 'Actual'`, `contactEmails: [alertEmail]`.

  The amount is in the billing currency, EUR (constitution v1.2.0).
- [ ] T048 [P] [US5] Add an "Azure daily run" section to `README.md`:
  - What runs where and when (Tuesday to Saturday, 11:00 UTC).
  - The deploy, operate, teardown and decommission entry points, with links to `specs/002-azure-daily-run/quickstart.md` and `contracts/deployment-contract.md`.
  - The cost table from research R10 in EUR (GHCR, about EUR 19 per month against the EUR 30 cap).
  - The sentence "Azure budgets only send notifications; they do not stop spending." (FR-028).
  - The note that every `az` and deployment command runs on the host only.
- [ ] T049 [US5] (owner, host) Validate quickstart.md §4 row US5/FR-028: `az consumption budget show --budget-name budget-maa-monthly` shows EUR 30 with the three actual-cost notifications.

**Checkpoint**: all five stories delivered.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T050 [P] Update the "Getting started" section of `README.md` so it lists both modes (`uv run detect --date …` and `uv run detect --latest`, optionally with `--migrate`) and links the 002 CLI contract next to the 001 one.
- [ ] T051 [P] In `specs/001-nasdaq100-anomaly-detection/quickstart.md`, add one note under its run instructions that `--migrate` can replace the separate `python -m market_anomaly_agent.storage.migrate` step. Change nothing else in feature 001's documents.
- [ ] T052 (owner, host) Validate SC-007 from quickstart.md §4:
  - `podman image inspect` and an `env` listing of the image show no key.
  - A search of the Actions logs and `log-maa` for the key finds nothing.
  - A password login with `psql` is refused.
  - `workflow_dispatch` on a non-`main` branch fails at `azure/login` with `AADSTS70021`.
- [ ] T053 (owner, host) Run quickstart.md §6 (the local workflow is unchanged), then let the schedule run for a week to confirm SC-001 (5 trading days stored with no manual action).
- [ ] T054 Constitution compliance pass over the whole diff (Governance: every pull request is checked against the principles). Confirm:
  - No secret in any committed file (`git grep -nE 'td-|PASSWORD=|AccountKey'` reviewed by hand).
  - No personal or tenant identifier in any file (research R16): `git grep -nE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'` may only find Microsoft's documented built-in role definition IDs; `git grep -niE '[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|onmicrosoft\.com'` finds no email address of the owner and no tenant domain. Known, allowed hits: `noreply@` attribution lines, the vendor's public contact address in feature 001's research.md, and the fake `user:password@host` connection strings in tests and examples.
  - Type hints on every new function.
  - Every new test passed only after being seen failing.
- [X] T055 *Added after the first deployment (2026-11-13):* make `computed_at` the time the computation is performed (feature 001 FR-014), fixing the code, not the spec. The first Azure run stored `computed_at` 09:05:03 UTC for a run that started at 09:04:29 and fetched until about 09:18: `cli.py` passed `datetime.now(UTC)`, taken once before the fetch, and `run_detection` stored it as `computed_at`, while `save_run` reused it as every bar's `fetched_at`.
  - Tests first (committed red): `test_fetch_start_and_computation_instants_come_from_the_injected_clock` (unit; a fake clock moved forward one minute per fetch, read exactly twice), `test_the_injected_clock_is_passed_to_run_detection` (unit, CLI) and `test_bars_store_the_fetch_start_and_the_run_the_computation_instant` (integration).
  - `run_detection(evaluated_date, tickers, source, clock)` now takes the injected clock (`Callable[[], datetime]`) and reads it twice: `fetched_at` right before the first fetch, and `computed_at` after every bar has been fetched, right before the computation. To make that possible, all bars are fetched first and evaluated afterwards; the "skipped …" log lines for gaps and short history therefore now follow all "fetched …" lines. The benchmark is still checked straight after its fetch, so an unusable benchmark still aborts before any other request.
  - `DetectionResult` has a new `fetched_at` field; `save_run` writes it to `daily_price_bars.fetched_at`. The CLI passes `main`'s `now` clock (default `datetime.now(UTC)`), the same one `--latest` uses.
  - Checked: nothing compares `computed_at` or `fetched_at` between runs. The rerun check (`snapshots_match`, FR-018) compares only trading date, open, close and volume, and the end-to-end comparison drops both timestamps. No schema change.
  - The one row already stored in Azure keeps its old `computed_at` and `fetched_at` values; the cloud database is not touched.

  Record any deviation with its justification in `specs/002-azure-daily-run/plan.md`, Complexity Tracking.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user stories.
- **US1 (Phase 3)** depends on Phase 2. It is the MVP and the base for every later story: the
  later stories add to `cli.py`, `env.bicep`, `data.bicep` and `main.bicep`, which US1 creates.
- **US2 (Phase 4)** depends on US1's T014 (the CLI modes). Its validation T033 depends on T028
  (deployed environment).
- **US3 (Phase 5)** depends on US1's T018 (job and workspace exist). T037 to T040 depend on T028.
- **US4 (Phase 6)** depends on US1 (T017, T025). T045 depends on T028 and, for "already stored",
  on US2.
- **US5 (Phase 7)** depends on Phase 2 (T005) only. It can be built in parallel with US2 to US4.
- **Polish (Phase 8)** after all desired stories.

### Within each story

- Tests (T008–T011, T030–T031) are written first and seen failing, then the implementation.
- Bicep tasks on the same file run in order: data.bicep T017 → T041; env.bicep T018 → T019 →
  T034 → T035 → T036 → T039; main.bicep T005 → T019 → T047.
- Owner tasks come after the code they validate has been merged to `main` and deployed by the
  pipeline.

### Parallel opportunities

- Phase 1: T002 and T003.
- Phase 2: T006 alongside T004 and T007.
- US1 tests: T008 and T009 together (T010 and T011 touch the shared cli and e2e files, so they run
  one after the other).
- US1 implementation: T012 and T013 together. T016, T020, T021 and T022 alongside the Bicep tasks.
- US2: T030 and T031 together.
- US4: T042, T043 and T044 together.
- US5: T048 alongside T047. US5 as a whole alongside US2 to US4.

## Parallel Example: User Story 1

```text
# Write the failing tests first, in parallel:
Task: "T008 [US1] latest_completed_trading_day tests in tests/unit/test_calendar.py"
Task: "T009 [US1] Entra-mode tests in tests/unit/storage/test_db.py"

# Then implement in parallel:
Task: "T012 [US1] latest_completed_trading_day in src/market_anomaly_agent/calendar.py"
Task: "T013 [US1] Entra mode in src/market_anomaly_agent/storage/db.py"

# Independent of the Python work, in parallel:
Task: "T016 [US1] Containerfile"
Task: "T020 [US1] infra/sql/bootstrap.sql"
Task: "T021 [US1] infra/scripts/db-access.sh"
Task: "T022 [US1] infra/scripts/entra-bootstrap.sh"
```

## Implementation Strategy

### MVP first (User Story 1)

1. Phases 1 and 2.
2. Phase 3 up to T026. That is all agent work, reviewed and committed by the owner, then merged
   through a pull request.
3. Owner tasks T027 to T029 on the host. **Stop and validate**: one manual execution stores a day.

### Incremental delivery

1. US1: the daily run works (a repeated start re-fetches but is safe, since 001 reproduces).
2. US2: repeated starts and holidays become free. US1 and US2 make up the complete P1 scope.
3. US3: bounded retries are verified and alerts are live.
4. US4: teardown, rebuild, lock and decommission.
5. US5: budget and cost documentation (can land earlier, as it only needs T005).

### Notes

- [P] tasks touch different files and depend on no incomplete task.
- Owner tasks are the only ones that touch Azure, GitHub settings or the real database.
- Claude Code never commits, pushes or merges. The owner reviews each change and commits.
