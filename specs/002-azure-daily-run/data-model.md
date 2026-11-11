# Data Model: Unattended Daily Run on Azure

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

This feature adds **no tables, columns or migrations**. Feature 001's schema
([../001-nasdaq100-anomaly-detection/data-model.md](../001-nasdaq100-anomaly-detection/data-model.md))
is reused unchanged in the Azure database. What this feature does add is configuration, database
roles and cloud resources, all modelled below.

## 1. Run configuration (environment of the `detect` process)

| Variable | Local (`podman compose`) | Azure job | Secret? | Validation |
|---|---|---|---|---|
| `DATABASE_URL` | `postgresql://market_anomaly_agent:<pw>@host.containers.internal:5432/market_anomaly_agent` | `postgresql://id-maa-job@<server>.postgres.database.azure.com:5432/market_anomaly_agent?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt` | Local: yes (contains password). Azure: no | Required. With `DATABASE_AUTH=entra`: must have no password and must have `sslmode=verify-full`, otherwise exit 1 before connecting. |
| `DATABASE_AUTH` | unset (= `password`) | `entra` | no | `password` or `entra`; any other value exits 1. |
| `AZURE_CLIENT_ID` | unset | client ID of `id-maa-job` | no | Required when `DATABASE_AUTH=entra`. |
| `PRICE_DATA_API_KEY` | from `.env` | Key Vault reference `twelve-data-api-key` via job secret `price-data-api-key` | **yes** | Required, but only once a vendor fetch is needed. A `--latest` start whose day is already stored does not read it. |
| `APP_COMMIT` | unset (logged as `unknown`) | baked into the image from build arg `GIT_SHA` | no | Free text, logged as is. |

The access token obtained in `entra` mode is held only in memory and is added to the redaction
list for the life of the process.

## 2. Database principals (Azure only)

```text
<owner UPN>  ──member of──▶ azure_pg_admin          (Entra admin; bootstrap, read, dump, restore)
     │
     └──member of (INHERIT, SET)──▶ id-maa-job      (job's managed identity)
                                       │
                                       ├─ CONNECT, TEMPORARY on DATABASE market_anomaly_agent
                                       ├─ USAGE, CREATE on SCHEMA public
                                       └─ owner of every table created by migrations
PUBLIC ── no privileges on DATABASE market_anomaly_agent
```

| Rule | Source |
|---|---|
| No role has a password, and password authentication is disabled server-wide. | FR-017a, FR-020 |
| `id-maa-job` is not superuser and has neither CREATEROLE nor CREATEDB. | FR-017a, research R3 |
| Migrations run only as `id-maa-job`, from the job (`detect --latest --migrate`). | FR-006, research R3 |
| The owner reads through membership and queries with `default_transaction_read_only=on` by convention. | research R3 |
| The bootstrap is idempotent and verifies its result (`has_schema_privilege`, `pg_has_role`). | research R3 |

## 3. Cloud resources

Names are fixed by convention; `<u>` is `uniqueString(subscription().id)` truncated to 6 characters
(stable across teardown and redeploy).

### Subscription scope (survives teardown)

| Resource | Name | Notes |
|---|---|---|
| Resource group | `rg-maa-data` | CanNotDelete lock `lock-maa-data` |
| Resource group | `rg-maa-env` | Deleted by teardown |
| Budget | `budget-maa-monthly` | EUR 30, actual-cost notifications at 50/80/100% (EUR 15 / 24 / 30) |
| Custom role definition | Market Anomaly Agent job image updater | `Microsoft.App/jobs/read`, `Microsoft.App/jobs/write` |

### `rg-maa-data` (locked, kept by teardown)

| Resource | Name | Key settings |
|---|---|---|
| User-assigned identity | `id-maa-job` | Its object ID is bound to the PostgreSQL role (research R4) |
| PostgreSQL Flexible Server | `psql-maa-<u>` | `Standard_B1ms` (Burstable), version 17, 32 GiB storage, backup 7 days, no HA, no geo-backup, Entra-only auth, public access |
| Entra administrator | (child of server) | Owner's object ID, principal type User |
| Server parameter | `require_secure_transport` | `on` |
| Server parameter | `ssl_min_protocol_version` | `TLSv1.2` |
| Firewall rule | `AllowAllAzureServicesAndResourcesWithinAzureIps` | 0.0.0.0–0.0.0.0 |
| Firewall rule | `owner-access` | Owner's IP while open, `192.0.2.1` while closed (research R14) |
| Database | `market_anomaly_agent` | Created by bootstrap SQL, not by Bicep (research R3) |

### `rg-maa-env` (deleted and recreated)

| Resource | Name | Key settings |
|---|---|---|
| Log Analytics workspace | `log-maa` | PerGB2018, retention 30 days |
| Container Apps environment | `cae-maa` | Consumption only, logs to `log-maa` |
| Key Vault | `kv-maa-<u>` | Standard, RBAC, soft delete 7 days, purge protection off |
| Key Vault secret | `twelve-data-api-key` | Created only when the deploy input is non-empty |
| Container Apps job | `job-maa-detect` | Schedule trigger `0 11 * * 2-6`, `replicaTimeout` 3600 (research R1), `replicaRetryLimit` 2, parallelism 1, completions 1, 0.5 vCPU / 1 GiB, identity `id-maa-job`, args `--latest --migrate` |
| User-assigned identity | `id-maa-github` | Federated credential `github-main`, subject `repo:XaverPilgrimm/market-anomaly-agent:ref:refs/heads/main` |
| Action group | `ag-maa-owner` | Email receiver = deploy input |
| Scheduled query rule | `alert-maa-execution-failed` | Every 5 minutes (research R9) |
| Scheduled query rule | `alert-maa-no-completed-run` | Daily 12:45 UTC, Tuesday to Saturday (research R9) |

### Role assignments

| Principal | Role | Scope |
|---|---|---|
| `id-maa-job` | Key Vault Secrets User | secret `twelve-data-api-key` |
| Owner | Key Vault Secrets Officer | vault `kv-maa-<u>` |
| `id-maa-github` | Market Anomaly Agent job image updater | job `job-maa-detect` |

With GHCR (research R6) there is no registry resource and no AcrPull or AcrPush assignment.

## 4. Scheduled execution: states

```text
            schedule (Tue–Sat 11:00 UTC) or manual start
                            │
                            ▼
                     ┌─ attempt n ─┐            n = 1..3
                     │  running    │
                     └──────┬──────┘
         exit 0 ┌───────────┼──────────────┐ exit ≠ 0, start failure, or timeout
                ▼                          ▼
           Succeeded            n < 3 and time limit left? ── yes ──▶ attempt n+1
                                            │ no
                                            ▼
                                         Failed ──▶ alert rule 1 (≤ 15 min)
```

- Every attempt stores either a complete day or nothing (feature 001's single transaction,
  FR-012).
- A `--latest` attempt whose day is already stored ends `Succeeded` without any vendor request
  (FR-002).
- Execution records live in the platform (the last 100 executions) and in Log Analytics for
  30 days, not in the detection database.
