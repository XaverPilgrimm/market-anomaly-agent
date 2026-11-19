# Implementation Plan: Unattended Daily Run on Azure

**Branch**: `002-azure-daily-run` | **Date**: 2026-11-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-azure-daily-run/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Run feature 001's `detect` unattended once per US trading day, as an Azure Container Apps
**scheduled job** (`0 11 * * 2-6` UTC) in **France Central**. Results are stored in an
**Entra-only PostgreSQL 17 Flexible Server (B1ms)**. The application gains a `--latest` mode,
which resolves the last completed New York trading day and exits 0 without a vendor call when
that day is already stored, and a `--migrate` flag, so the job runs `detect --latest --migrate` as
its own managed identity `id-maa-job`. That identity owns the tables and is the only role that runs
migrations.

The Twelve Data key, the only secret, reaches the job as a **Key Vault secret reference** read
through `id-maa-job`. There is no database password anywhere in Azure. GitHub Actions runs every
check, including PostgreSQL 17 integration tests and an offline Bicep build, on each pull request
and push. Only pushes to `main` publish the image and switch the job to it, signing in through an
**OIDC federated credential restricted to `refs/heads/main`**.

All Azure resources are Bicep, deployed from the owner's host only, always behind a **what-if**.
The database and its identity sit in a separate, CanNotDelete-locked resource group that survives
teardown. The server's Entra administrator is the security group **`maa-db-admins`**, whose only
member is the owner's account; the owner signs in with the group name and their own token. The
group is created by the host script `entra-bootstrap.sh`. Because the repository is public, no file
contains the owner's email, tenant domain, tenant ID, subscription ID or any object ID; scripts
read them from `az` at run time (research R16). A subscription budget of **EUR 30**, the constitution's Azure cap (v1.2.0, stated in
EUR because Azure bills this subscription in EUR), alerts at 50, 80 and 100%. The estimated cost is
**about EUR 19 per month** (63%) with GHCR as the registry, against about EUR 23 (78%) with ACR
Basic (see [research.md](./research.md) R6 and R10).

## Technical Context

**Language/Version**: Python 3.12+ with `uv` (unchanged); Bicep (Azure CLI's bundled `az bicep`)
for infrastructure; Bash for the host-side operation scripts

**Primary Dependencies**:
- Existing: `numpy`, `pandas-market-calendars`, `psycopg[binary]`, `requests`.
- **New**: `azure-identity`, for the managed-identity token in `DATABASE_AUTH=entra` mode
  (research R11).
- Build and run: `python:3.12-slim-bookworm` for both stages, with uv copied into the build stage
  from `ghcr.io/astral-sh/uv:0.12.19` (`COPY --from=… /uv /uvx /bin/`, research R12).
- Azure resource API versions are pinned in Bicep, `Microsoft.App/jobs@2026-07-01` (latest listed; GA).

**Storage**: Azure Database for PostgreSQL Flexible Server, PostgreSQL 17, `Standard_B1ms`,
32 GiB, 7-day backup, Entra-only authentication, TLS 1.2+ enforced and verified by the client
(`sslmode=verify-full`). The client trusts the image's Debian CA bundle, named by file
(`sslrootcert=/etc/ssl/certs/ca-certificates.crt`). `sslrootcert=system` does not work with the
`psycopg[binary]` wheel's bundled OpenSSL (research R8).
Feature 001's schema and migrations are unchanged, and no new tables are added
([data-model.md](./data-model.md)).

**Testing**: `pytest`. New unit tests cover day resolution with an injected clock, the `--latest`
already-stored path, argument rules, Entra configuration and token redaction (azure-identity
mocked, network guard on). Integration tests (`-m integration`) cover `--migrate` and the
`--latest` flows against PostgreSQL 17. Both suites run in CI; integration uses a `postgres:17`
service container. `ruff`, `mypy --strict` and `az bicep build` also run. The Azure behaviour
itself is validated by the host-side checks Q1 to Q6 in [quickstart.md](./quickstart.md).

**Target Platform**: Linux container on an Azure Container Apps consumption environment
(0.5 vCPU, 1 GiB). Locally: `podman compose` plus `uv run detect` inside the agent container,
unchanged.

**Project Type**: Single project (CLI), plus infrastructure as code and a CI workflow

**Performance Goals**: one attempt takes about 13 minutes, bounded by the vendor's 8 requests per
minute. An "already stored" start finishes in under 2 minutes (SC-002). An execution of up to
3 attempts ends before 12:30 UTC (SC-011).

**Constraints**:
- Azure spend at or below EUR 30 per month in total (constitution v1.2.0).
- At most 303 of the 800 daily vendor requests.
- No VNet, private endpoint or NAT gateway.
- No database password.
- No secret in the image, repository, pipeline or logs.
- Every `az` and deployment command runs on the host, never in the agent container (research R15).
- Claude Code does not commit or deploy (constitution, Development Workflow).

**Scale/Scope**: 1 job, 1 database, about 22 executions per month, 1 operator

**Resolved unknowns** (all in [research.md](./research.md)):
- Timeout semantics: R1
- Region availability: R2
- Database roles and who migrates: R3
- Identity across teardown: R4
- Key Vault reference: R5
- Registry choice: R6
- OIDC and least privilege: R7
- TLS: R8
- Alerts: R9
- Cost and EUR budget: R10
- Code changes: R11
- Image: R12
- What-if: R13
- Firewall under the lock: R14
- Host-only commands: R15
- Entra admin group and no identifiers in files: R16

The ones that can only be confirmed on the owner's subscription are gates in quickstart.md:
Q1 region, Q2 EUR prices, Q3 timeout scope, Q4 alert reason strings, Q5 pipeline role, Q6 what-if,
Q7 admin sign-in through the group with a personal account.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / constraint | Status | How the design complies |
|---|---|---|
| I. Deterministic numbers | ✅ Pass | No change to calculations. The new mode only chooses the day, through the existing tested NASDAQ calendar. |
| II. Simplest solution first | ✅ Pass | Consumption environment; public endpoint (no VNet, private endpoint or NAT); one workflow; no agent loop. Every addition is required by a present FR: the `azure-identity` dependency (FR-017a), the second alert rule (FR-022, guarding against unconfirmed event names), the custom role (FR-009 least privilege). See Complexity Tracking. |
| III. Research integrity | ✅ Pass | The universe file ships inside the image (001 FR-003). A stored day is never re-evaluated in `--latest` mode; immutability is unchanged. The unattended run makes "stored before outcome" reliable. The database survives teardown, and decommission exports before deleting. |
| IV. Security | ✅ Pass | The only secret is in Key Vault, read through a managed identity. No database password exists. Admin access depends on membership of the group `maa-db-admins` (one member), not on one user account. No personal identifier or tenant, subscription or object ID is in the public repository (R16). GitHub uses OIDC with no stored credential, restricted to `main`. Tokens are redacted. TLS uses `verify-full`. Pull requests have no Azure access. |
| V. Cost control | ✅ Pass | Budget of EUR 30, the constitution's Azure cap, at 50/80/100% on actual cost (R10). Estimate EUR 18.88 (63%); check Q2 in France Central gives about EUR 18.09 (60%) with the alert rules at their estimate (R10). The vendor stays on the free plan, with at most 303 requests per day (FR-014), enforced in code by the existing rate limiter and a fail-closed exit 1 when the vendor refuses. Cost per run: Azure compute stays inside the free grant and the vendor is USD 0, so no per-run paid API is called; no cost row is stored (nothing to measure). Recorded here as a known non-applicability, not a violation. |
| VI. Data and compliance | ✅ N/A | No public pages and no X or Brave data in this feature |
| VII. Observability | ✅ Pass | All stdout and stderr plus system events go to Log Analytics (30 days); the start line carries mode, day and commit. Agent traces are not applicable (no agent). |
| VIII. Testing and quality | ✅ Pass | Test-first obligations are listed in contracts/cli-contract.md. Tests stay offline (azure-identity is mocked). Type hints and `mypy --strict` stay. The integration suite now also runs in CI. |
| Tech: PostgreSQL, one image, ACA job, `podman compose` locally | ✅ Pass | All four hold; the local path keeps the password `DATABASE_URL`. |
| Workflow: no commits or pushes by Claude Code, feature branch plus pull request | ✅ Pass | Commands are host-only and run by the owner; the plan creates files only. |

**Post-design re-check (after Phase 1)**: still passing. The design introduced no new service, and
GHCR removes one Azure resource. The Entra group is a directory object, not a service. Three items
depend on the owner's subscription and are gated rather than assumed: B1ms and PostgreSQL 17
availability in the target region (Q1, decided by a probe; France Central since 2026-11-12, with
Italy North as the fallback), the timeout scope (Q3), and whether a personal account can sign in
through the admin group (Q7, with a work-account fallback).

## Project Structure

### Documentation (this feature)

```text
specs/002-azure-daily-run/
├── plan.md                      # This file
├── research.md                  # Phase 0: decisions R1–R15
├── data-model.md                # Phase 1: config, DB principals, resources, execution states
├── quickstart.md                # Phase 1: host-side gates Q1–Q6 and scenario validation
├── contracts/
│   ├── cli-contract.md          # `detect --latest` / `--migrate` / Entra mode (amends 001)
│   └── deployment-contract.md   # Bicep params, job definition, pipeline, operations scripts
├── checklists/requirements.md
└── tasks.md                     # Phase 2 (/speckit-tasks; not created here)
```

### Source Code (repository root)

```text
src/market_anomaly_agent/
├── cli.py                  # + --latest, --migrate, start log line, token redaction
├── calendar.py             # + latest_completed_trading_day(now) (wraps previous_trading_day)
└── storage/
    ├── db.py               # + DATABASE_AUTH=entra: token via azure-identity, URL checks
    └── migrate.py          # unchanged (called from cli with --migrate)

tests/
├── unit/
│   ├── test_calendar.py        # + latest-day resolution cases
│   ├── test_cli.py             # + --latest, --migrate, argument rules, start line
│   └── storage/test_db.py      # new: entra mode config and redaction (credential mocked)
└── integration/
    └── test_detect_end_to_end.py   # + --latest stored/not-stored, --migrate on empty schema

infra/
├── main.bicep  main.bicepparam
├── modules/{data.bicep, env.bicep}
├── sql/bootstrap.sql
└── scripts/{_common,preflight,entra-bootstrap,deploy,db-bootstrap,db-access,teardown,decommission,restore}.sh

Containerfile  .containerignore
.github/workflows/ci.yml
README.md                   # + Azure section: deploy, operate, cost, "budgets only notify"
specs/001-nasdaq100-anomaly-detection/contracts/cli-contract.md   # + pointer to the 002 amendment
```

**Structure Decision**: keep the single-project layout of feature 001. Application changes stay
inside the three modules they concern (`cli`, `calendar`, `storage/db`). Infrastructure lives in
a new top-level `infra/` with one subscription-scope entry point and two resource-group modules,
matching the two lifecycles: the data group is locked and kept, the environment group is
disposable. The CI workflow and `Containerfile` sit at the conventional root locations.

## Owner decisions (confirmed 2026-11-11)

1. **Registry: GHCR instead of ACR** (research R6). It saves about EUR 4.45 per month (78% → 63%
   of the EUR 30 cap), leaves more room for Azure OpenAI later, and narrows the pipeline's Azure
   permissions. The trade-off is that the image is public, and its package visibility has to be
   set once on GitHub. Reverting to ACR Basic is a small, documented change.
2. **Budget EUR 30**, the constitution's Azure cap, stated in EUR (constitution v1.2.0,
   research R10).
3. **Region: France Central, fallback Italy North** (research R2, decided 2026-11-12). Probes on
   this subscription refused a B1ms PostgreSQL 17 server in Germany West Central (location
   restricted), West Europe (not accepting new customers), North Europe, Sweden Central and Poland
   Central (`SkuNotAvailable`); France Central and Italy North accepted it. The SKU listing had
   passed Germany West Central, so check Q1 is now decided by a probe, not by the listing.
4. **Entra administrator: the security group `maa-db-admins`**, with the owner's personal
   Microsoft account as its only member, created by `entra-bootstrap.sh`. No owner, tenant,
   subscription or object identifiers in any file (research R16).

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|---|---|---|
| Two resource groups and a lock | FR-030: the database must survive teardown | One group: teardown would delete the results, or need per-resource exclusions |
| Custom RBAC role for the pipeline | FR-009: the pipeline may only update this one job | Built-in Contributor on the job: it also grants `jobs/start/action`, which lets the caller reference the job's secrets (Microsoft's jobs documentation) |
| Second alert rule ("no completed run") | FR-022 / SC-004 must hold even though Microsoft does not document the failure event names | A single rule on undocumented `Reason_s` values could stay silent |
| `azure-identity` dependency | FR-017a: managed-identity token for PostgreSQL | Hand-written HTTP to the identity endpoint: more code to own, and Microsoft does not document that jobs set the endpoint variables |
| Time-window guard in `teardown.sh` (*added 2026-11-18*, tasks.md T042) | A teardown between 10:30 and 14:00 UTC, Tuesday to Saturday, kills the 11:00 run, so the 12:45–13:45 completion check sends a failure email (FR-022). *(Corrected 2026-11-18: a teardown does not empty `log-maa`; recreating it recovers the soft-deleted workspace with its logs, tasks.md T045.)* | A note in the quickstart only: the script is the one place it is run from, so a refusal with an explicit `MAA_TEARDOWN_ANYTIME=1` override is the cheapest reliable guard |
| One optional directory mount in `pg_client` (*added 2026-11-18*, T043/T044) | `pg_dump -Fc` must write a real file (a pipe loses the custom format's data offsets) and `pg_restore` must read one | A second client helper: duplicates the CA-bundle mount and the token handling |
