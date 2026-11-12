# Contract: Deployment, pipeline and operations

**Feature**: [../spec.md](../spec.md) · Resources: [../data-model.md](../data-model.md) §3 ·
Rationale: [../research.md](../research.md)

**Host only.** Every command in this contract is run by the owner on the host, never inside the
agent container, and never by Claude Code (research R15). Each script exits 1 straight away if
`/run/.containerenv` exists. Database clients come from `podman run --rm docker.io/library/postgres:17 …`.

**Public repository rule** (spec FR-025, research R16): no file, this one included, contains the
owner's email address, tenant domain, tenant ID, subscription ID or any object ID. Scripts read them
at run time from `az`. Documents use placeholders: `<tenant-id>`, `<subscription-id>`,
`<server-fqdn>`, `<u>`.

## 1. Repository layout

```text
infra/
├── main.bicep              # targetScope = 'subscription': RGs, lock, budget, custom role, modules
├── main.bicepparam         # non-secret defaults only (location, names); no email, key, IP, tenant/subscription/object ID
├── modules/
│   ├── data.bicep          # rg-maa-data: id-maa-job, PostgreSQL server, admin, params, firewall
│   └── env.bicep           # rg-maa-env: logs, environment, Key Vault, job, id-maa-github, alerts
├── sql/
│   └── bootstrap.sql       # idempotent role/database bootstrap (research R3)
└── scripts/
    ├── preflight.sh        # region and SKU checks (quickstart Q1)
    ├── entra-bootstrap.sh  # admin group maa-db-admins: create if missing, add signed-in user, check sole member
    ├── deploy.sh           # entra-bootstrap, what-if, then create (confirm); sets GitHub variables
    ├── db-bootstrap.sh     # runs bootstrap.sql signed in as maa-db-admins
    ├── db-access.sh        # open | close the owner-access firewall rule
    ├── teardown.sh         # deletes rg-maa-env, purges the vault; never touches rg-maa-data
    ├── decommission.sh     # pg_dump → verify → remove lock → delete rg-maa-data
    └── restore.sh          # pg_restore into a freshly bootstrapped server
Containerfile
.containerignore
.github/workflows/ci.yml
```

## 2. `main.bicep` parameters

| Parameter | Type | Source | In repo? |
|---|---|---|---|
| `location` | string | `main.bicepparam`, default `francecentral` (research R2) | yes |
| `githubRepository` | string | `main.bicepparam`, `XaverPilgrimm/market-anomaly-agent` | yes |
| `dbAdminGroupName` | string | default `maa-db-admins` | yes (a fixed public name) |
| `dbAdminGroupObjectId` | string | `deploy.sh`, at run time: `az ad group show --group maa-db-admins --query id -o tsv` | **no** |
| `alertEmail` | string | `deploy.sh` prompt, or `$MAA_ALERT_EMAIL` | **no** (FR-022) |
| `twelveDataApiKey` | `@secure()` string | `deploy.sh` `read -rs` prompt; empty keeps the stored secret | **no** (FR-018) |
| `jobImage` | string | `deploy.sh`: current image on the job, else the placeholder `mcr.microsoft.com/k8se/quickstart-jobs:latest` | no |
| `ownerAccessIp` | string | always `192.0.2.1` (closed) on deploy | default |
| `budgetAmountEur` | int | default `30` (constitution Principle V) | yes |

Outputs printed by `deploy.sh`: `AZURE_CLIENT_ID` (client ID of `id-maa-github`), `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`, the server FQDN, the job name. `deploy.sh` offers to store the first three
with `gh variable set`. These are repository **variables**, not secrets: they are identifiers, kept
in GitHub's settings and never written to a file.

The owner's own object ID is not a deployment parameter. Only `entra-bootstrap.sh` reads it, to
check and add the group membership.

## 2a. `entra-bootstrap.sh` behaviour

Run by `deploy.sh` as its first step, and runnable on its own. Idempotent.

1. Refuse to run inside a container. Require `az account show` to succeed.
2. `uid=$(az ad signed-in-user show --query id -o tsv)`.
3. If `az ad group show --group maa-db-admins` fails, run
   `az ad group create --display-name maa-db-admins --mail-nickname maa-db-admins` (a security
   group).
4. If `az ad group member check --group maa-db-admins --member-id "$uid" --query value -o tsv` is
   not `true`, run `az ad group member add --group maa-db-admins --member-id "$uid"`.
5. List the members (`az ad group member list --group maa-db-admins --query "[].id" -o tsv`). If
   there is any member other than `$uid`, exit 1 with "maa-db-admins must have exactly one member"
   and change nothing.
6. Print `admin group ok`. Never print object IDs.

## 3. `deploy.sh` behaviour

1. Refuse to run inside a container. Require `az account show` to succeed.
2. Run `entra-bootstrap.sh` (§2a). Stop if it fails.
3. Gather the inputs from §2, reading `dbAdminGroupObjectId` at run time.
4. Run `az deployment sub what-if -l <location> -f infra/main.bicep -p infra/main.bicepparam -p …`
   and show the result.
5. Run `az deployment sub create --confirm-with-what-if …`. Nothing is applied without an
   interactive "y" (research R13).
6. Print the outputs, then the next steps: run `db-bootstrap.sh` on a new server; run
   `gh workflow run ci.yml --ref main` while the job still has the placeholder image.

A rerun with unchanged inputs must produce a what-if with no changes, apart from the documented
noise of `ownerAccessIp` when the rule was left open (FR-026, US4 scenario 2).

## 4. Container Apps job definition

| Property | Value |
|---|---|
| `triggerType` | `Schedule` |
| `scheduleTriggerConfig.cronExpression` | `0 11 * * 2-6` (UTC) |
| `scheduleTriggerConfig.parallelism` / `replicaCompletionCount` | `1` / `1` |
| `replicaTimeout` | `3600` (research R1; `1800` if quickstart Q3 shows a per-attempt timer) |
| `replicaRetryLimit` | `2` |
| `identity` | user-assigned `id-maa-job` |
| `secrets` | `price-data-api-key` → Key Vault URL of `twelve-data-api-key`, identity `id-maa-job` |
| container `args` | `["--latest", "--migrate"]` (entrypoint `detect`) |
| container `env` | `DATABASE_URL`, `DATABASE_AUTH=entra`, `AZURE_CLIENT_ID`, `PRICE_DATA_API_KEY` (secretRef) |
| resources | `0.5` vCPU, `1Gi` |
| `registries` | none (public GHCR image, research R6) |

Manual start (FR-013): `az containerapp job start -n job-maa-detect -g rg-maa-env`.

## 5. Pipeline (`.github/workflows/ci.yml`)

| Trigger | `checks` | `publish-deploy` | Azure sign-in |
|---|---|---|---|
| `pull_request` → `main` | runs | **skipped** | **never** |
| `push` → `main` | runs | runs if `checks` passed | OIDC, subject `ref:refs/heads/main` |
| `workflow_dispatch` on `main` | runs | runs if `checks` passed | OIDC, same subject |
| any other ref | — | — | a token request is refused by the federated credential |

`checks`: `uv sync --frozen`, `ruff check`, `ruff format --check`, `mypy`, `pytest`,
`pytest -m integration` (service container `postgres:17`), `az bicep build --file infra/main.bicep`.

`publish-deploy`:

1. Build with `GIT_SHA=${{ github.sha }}`.
2. Push `ghcr.io/xaverpilgrimm/market-anomaly-agent:${{ github.sha }}`.
3. `azure/login` (OIDC).
4. `az containerapp job update -n job-maa-detect -g rg-maa-env --image <that tag>`.

If any step fails, the job keeps its previous image (FR-009).

Workflow permissions: `contents: read` by default. Only `publish-deploy` gets `packages: write`
and `id-token: write`. There is no `environment:` key (research R7).

## 6. Operations

| Procedure | Command | Guarantees |
|---|---|---|
| Owner database access | `db-access.sh open`, work, `db-access.sh close` | The rule is updated, never deleted (it is locked, research R14). `close` sets `192.0.2.1`. |
| Owner query | `psql "host=<server-fqdn> dbname=market_anomaly_agent user=maa-db-admins sslmode=verify-full sslrootcert=system options='-c default_transaction_read_only=on'"`, with `PGPASSWORD` set to the owner's own token from `az account get-access-token --resource-type oss-rdbms` | Read-only session; Entra token login as the admin group (research R16) |
| Rotate vendor key | `az keyvault secret set --vault-name kv-maa-<u> -n twelve-data-api-key --value "$KEY"` (after `read -rs KEY`) | The job reads the latest version on its next execution |
| Teardown | `teardown.sh` | Deletes `rg-maa-env` and purges `kv-maa-<u>`. `rg-maa-data`, the budget and the Entra group `maa-db-admins` are kept. A redeploy can run straight away (FR-027). |
| Decommission | `decommission.sh <dump path outside repo>` | Refuses a path inside the repository. Runs `teardown.sh` first, so no job is left referencing a deleted identity. Then runs `pg_dump -Fc`, checks the file is non-empty and that `pg_restore --list` succeeds, deletes `lock-maa-data`, and runs `az group delete -n rg-maa-data` (FR-031). It keeps the Entra group `maa-db-admins`, which costs nothing and is reused by a later deployment; when the project ends for good, the owner removes it with `az ad group delete --group maa-db-admins`. |
| Restore | `deploy.sh`, `db-bootstrap.sh`, then `restore.sh <dump path>`, in one sitting | `pg_restore --no-owner --role=id-maa-job` into `market_anomaly_agent`. Afterwards `schema_migrations` lists the migrations, so `--migrate` is a no-op (FR-032, SC-009). Until the bootstrap has run, the new `id-maa-job` has no database role, so a scheduled execution fails with exit 1 and alerts instead of writing into an empty database. `restore.sh` refuses to run if `detection_runs` already holds rows. |

The dump file holds stored results only; it contains no credentials. It is still private and must
not be committed or uploaded (FR-031). `.gitignore` gains `*.dump`, but the real protection is that
the script refuses paths inside the repository.
