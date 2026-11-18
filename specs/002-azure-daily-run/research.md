# Research: Unattended Daily Run on Azure

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Date**: 2026-11-11

Each section records a decision, why it was made, and what else was considered. Facts taken from
Microsoft's documentation are quoted or cited. Inferences are marked as such. Items that can only
be settled against the owner's own subscription are turned into explicit checks in
[quickstart.md](./quickstart.md), to be run on the host before the first real deployment.

---

## R1. Job time limit: per attempt or per execution?

**Decision**: `replicaTimeout = 3600` seconds, `replicaRetryLimit = 2`, `parallelism = 1`,
`replicaCompletionCount = 1`. Before go-live, quickstart check Q3 confirms the behaviour
empirically. If the timer turns out to apply per attempt, the value is lowered to `1800`.

**Confirmed by check Q3 (2026-11-13): the timer is per execution, so `3600` stays.** A temporary
copy of the job with `replicaTimeout 120`, `replicaRetryLimit 2` and a container running
`sh -c 'sleep 50; exit 1'` ran only 2 replicas, started 11 s and 79 s after the execution start.
The second was cut off when the execution reached 120 s, and no third replica started. Under a
per-attempt timer all 3 would have run to their `exit 1`. This matches the reading below.

**What the documentation says** ([Jobs in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/jobs)):

- `replicaTimeout`: "The maximum time in seconds to wait for a replica to complete."
- `replicaRetryLimit`: "The maximum number of times to retry a failed replica. To fail a replica
  without retrying, set the value to `0`." So the limit counts retries, and 2 means up to
  3 attempts.
- "The `replicaTimeout` setting takes precedence if it expires before all retries occur."
- No backoff between retries is documented.
- "Cron expressions in scheduled jobs are evaluated in Coordinated Universal Time (UTC)."

**Reading**: the documentation never says outright whether the timer is per attempt or per
execution. The "takes precedence … before all retries occur" sentence only makes sense if a single
timer can cut off retries that have not yet run, which means it covers the whole execution
(*inference*). That reading is the conservative one for sizing, so the value is chosen to fit all
3 attempts:

| Item | Minutes |
|---|---|
| 3 attempts × ~13 min (101 requests at the free plan's 8 per minute) | 39 |
| Image pull and container start, 3 × ~1 | 3 |
| Retry restarts and rate-limit waits at attempt boundaries (an immediate retry can hit the per-minute limit and wait up to 60 s, `RATE_LIMIT_WAIT_SECONDS`) | 3 |
| Slack for a slower vendor | ~15 |
| **Limit** | **60** |

**Effect on FR-011's deadline**: if the timer covers the execution, a run started at 11:00 UTC
ends by about 12:00 UTC at the latest. If it turns out to be per attempt, the value becomes 1800,
three attempts end by about 12:30 UTC, and a hung first attempt is still stopped after 30 minutes.
Either way the run ends before the earliest US open at 13:30 UTC (SC-011). Keeping 3600 under a
per-attempt reading would allow up to 3 hours, which is why check Q3 is a gate and not optional.

**Alternatives considered**:

- *An in-application wall-clock limit as a second guard.* Rejected for now (Principle II). The
  platform limit plus check Q3 meets FR-011, and the vendor client already has a 30-second
  per-request timeout (`TIMEOUT_SECONDS`).
- *Setting 1800 straight away.* Rejected. Under the per-execution reading, which the documentation
  suggests, it would cut off the third attempt and break US3 scenario 3.

## R2. Region: France Central

**Decision** (2026-11-12, after probing): target **France Central** (`francecentral`) for every
resource of the feature, for all resources together (FR-015 requires one region). The fallback is
**Italy North** (`italynorth`), the only other region where the probe succeeded. The original
target, Germany West Central, and the original fallback list (West Europe → North Europe → Sweden
Central) are all ruled out for this subscription; see the probe results below.

**Probe results** (2026-11-12): the owner created a Burstable `Standard_B1ms` PostgreSQL 17
Flexible Server in each candidate region, in a throwaway resource group:

| Region | Result |
|---|---|
| `germanywestcentral` | Refused: "The location is restricted from performing this operation" |
| `westeurope` | Refused: the region is "not accepting new customers" |
| `northeurope` | Refused: `SkuNotAvailable` for `Standard_B1ms` |
| `swedencentral` | Refused: `SkuNotAvailable` for `Standard_B1ms` |
| `polandcentral` | Refused: `SkuNotAvailable` for `Standard_B1ms` |
| `francecentral` | **Created** (then deleted) |
| `italynorth` | **Created** (then deleted) |

**The listing is not enough.** `preflight.sh` had passed `germanywestcentral`: the capabilities
listing (`az postgres flexible-server list-skus`) showed `Standard_B1ms`, version `17` and no
`restricted` flag, yet provisioning was blocked. The listing does not reveal subscription-level
location restrictions, capacity closures or SKU shortages. So quickstart check Q1 now treats the
listing as a first filter only, and the decisive check is a probe: create a B1ms server in a
throwaway resource group, then delete the group.

**Findings**:

- Container Apps jobs: `Microsoft.App/jobs` is generally available, and France Central is a
  Container Apps region. Check Q1 confirms this with
  `az provider show -n Microsoft.App --query "resourceTypes[?resourceType=='jobs'].locations"`.
- PostgreSQL 17 is generally available on Flexible Server
  ([release notes](https://learn.microsoft.com/en-us/azure/postgresql/release-notes/release-notes)).
- **Risk**: Microsoft Q&A threads report that Germany West Central restricts Flexible Server
  provisioning, and in particular the Burstable tier, for some subscription types because of
  capacity. Examples: a 2025 moderator answer that Burstable "is not supported in Germany West
  Central", and a later report that provisioning is limited to EA customers
  ([Q&A 2182828](https://learn.microsoft.com/en-us/answers/questions/2182828/burstable-skus-seem-not-to-be-available-in-new-sub),
  [Q&A 2260300](https://learn.microsoft.com/en-us/answers/questions/2260300/how-do-i-request-region-enablement-for-postgresql)).
  The probe of 2026-11-12 confirmed this risk for this subscription (see the table above).
- A support request for region enablement is possible, but it is not on the critical path: France
  Central and Italy North are also EU and keep the design unchanged. Only the `location` parameter
  and the cost table change.

**Alternatives considered**:

- *General Purpose `D2ds_v5` in Germany West Central.* Rejected: it costs well over USD 100 per
  month and breaks the cap on its own.
- *Italy North as the target.* Kept as the fallback. Both regions passed the probe; France Central
  is the more established region.
- *A support request to unlock Germany West Central.* Not pursued: it is slow, its outcome is
  uncertain, and France Central works today.

**API version for PostgreSQL** (recorded 2026-11-12, after the first real deployment; cause
revised the same day after the region probes): the server and its child resources
(`administrators`, `configurations`, `firewallRules`) use **`2025-08-01`**, the GA version before
the newest one, `2026-10-01`.

- **What failed:** the first real deployment, in `germanywestcentral` with `2026-10-01`, failed in
  the `maa-data` module with `ParameterOutOfRange`: "The value of the 'Version' should be in: []".
- **Most likely cause: the region restriction, not the API version.** The list of allowed values
  was *empty*, so the resource provider offered no PostgreSQL version at all to this subscription
  in that region. The probes of 2026-11-12 (above) showed that `germanywestcentral` refuses any
  server for this subscription ("The location is restricted from performing this operation"),
  although its capabilities listing showed version `17`. A restricted location offering nothing
  explains the empty list; nothing shows that `2026-10-01` itself was at fault.
- **Why `2025-08-01` is kept anyway:** it is GA and it is proven in `francecentral`. Moving to
  `2026-10-01` would bring nothing this feature needs. The properties it uses (`authConfig`,
  `network.publicNetworkAccess`, `highAvailability.mode`, `principalType: 'Group'`) are unchanged
  in `2025-08-01`.
- **What it means for later features:** a new PostgreSQL API version counts as confirmed only
  after a real deployment against this subscription and region has succeeded. The template
  reference listing it is not enough, and neither is a what-if: the what-if for this deployment
  passed, and only the real deployment hit the error.

## R3. Database authentication and roles (who runs the migrations)

**Decision**: the server allows Microsoft Entra authentication only
(`authConfig: { activeDirectoryAuth: 'Enabled', passwordAuth: 'Disabled' }`). There are three
principals:

| Principal | PostgreSQL role | Created by | Privileges |
|---|---|---|---|
| Entra security group `maa-db-admins`, whose only member is the owner's account (R16) | `maa-db-admins` (Entra admin) | Group: `entra-bootstrap.sh` on the host. Admin entry: Bicep, `flexibleServers/administrators` with `principalType: 'Group'` | Member of `azure_pg_admin`, CREATEROLE, CREATEDB. The owner signs in as `maa-db-admins` with their own token. Used only for bootstrap, read-only queries, `pg_dump` and restore. Never used by the job. |
| Job's user-assigned managed identity `id-maa-job` | `id-maa-job` | Bootstrap SQL, `pgaadauth_create_principal('id-maa-job', false, false)` in database `postgres` | `CONNECT, TEMPORARY` on database `market_anomaly_agent`; `USAGE, CREATE` on schema `public`. Owns every table its migrations create. Not superuser, no CREATEROLE, no CREATEDB. |
| Admin group → job role membership | — | Bootstrap SQL, `GRANT "id-maa-job" TO "maa-db-admins" WITH INHERIT TRUE, SET TRUE` | Lets the owner, signed in as the group, read every table the job owns (queries, `pg_dump`) and restore as the job role (`pg_restore --role`). |

**The migrations run inside the job, as `id-maa-job`**, before detection starts (FR-006, the new
`--migrate` flag in [contracts/cli-contract.md](./contracts/cli-contract.md)). The tables are
therefore owned by the role that writes them, and no human step is needed when a later feature adds
a migration. The owner never runs migrations against Azure.

**Bootstrap** (`infra/sql/bootstrap.sql`, run once per new server by `infra/scripts/db-bootstrap.sh`
on the host, signed in as the admin group `maa-db-admins`; idempotent):

1. In `postgres`: create the principal if it is missing (`pgaadauth_create_principal`).
2. In `postgres`: `CREATE DATABASE market_anomaly_agent` if it is missing. The admin creates it, so
   the admin owns it and the `public` schema (PostgreSQL 15+: `pg_database_owner`) and can grant on
   both. Bicep deliberately does not create the database, because the owner of an ARM-created
   database is not documented.
3. Grant the privileges in the table above, and `REVOKE ALL ON DATABASE market_anomaly_agent FROM
   PUBLIC`.
4. Grant the admin group `maa-db-admins` membership in `id-maa-job`.
5. Fail loudly unless `has_schema_privilege('id-maa-job', 'public', 'CREATE')` and
   `pg_has_role(current_user, 'id-maa-job', 'USAGE')` are both true.

**Findings** ([connect with managed identity](https://learn.microsoft.com/en-us/azure/postgresql/security/security-connect-with-managed-identity),
[manage Entra users](https://learn.microsoft.com/en-us/azure/postgresql/security/security-manage-entra-users),
[configure Entra](https://learn.microsoft.com/en-us/azure/postgresql/security/security-entra-configure)):

- `pgaadauth_create_principal(roleName text, isAdmin boolean, isMfa boolean)` "must be run on the
  Postgres database". The role name "must match the name of the Microsoft Entra principal". For a
  managed identity that is its name, and the token is its password.
- The documentation says not to use the Entra administrator for regular database operations, so the
  job gets its own role.
- Step 4 relies on PostgreSQL 16+ behaviour: a CREATEROLE role that creates a role receives ADMIN
  OPTION on it. Here that role is the group role `maa-db-admins`, because a group member's session
  runs as the group (`current_user = 'maa-db-admins'`). Microsoft's pages do not say whether this holds when the role is created through
  `pgaadauth_create_principal`. **Open risk, guarded**: step 5 fails the bootstrap if the
  membership is missing. If it fails, the fallback is to drop the membership and give the admin
  group `SELECT` through `ALTER DEFAULT PRIVILEGES` issued by the job role in its own first execution.
  That needs a small Azure-only bootstrap path in code and is not planned unless step 5 fails.
- Token resource: `https://ossrdbms-aad.database.windows.net` (scope `…/.default`). On the host:
  `az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv`. "The access
  token is valid for 5 to 60 minutes." Because the token is only checked at login, the application
  fetches one for each connection it opens.

**Alternatives considered**:

- *One Entra group shared by the owner and the job.* Rejected: both principals would act as the
  same role, so the job would get admin rights. The admin group of R16 holds the owner only; the
  job keeps its own role.
- *Making the job role the database owner.* Rejected: it grants more than FR-017a's minimum.
- *Having the owner run migrations from the host.* Rejected: it breaks FR-006 (unattended) and
  splits table ownership.

## R4. Identity survives teardown

**Decision**: the job's user-assigned identity `id-maa-job` lives in the **data** resource group
`rg-maa-data`, next to the server, under the CanNotDelete lock. The pipeline identity
`id-maa-github` lives in the **environment** group `rg-maa-env` and is recreated with it.

**Rationale**: the PostgreSQL role created by `pgaadauth_create_principal` is bound to the
identity's object ID. Recreating `id-maa-job` under the same name after a teardown would produce a
new object ID and lock the job out of its own tables. Keeping the identity with the data means a
rebuild of the environment needs no database step. Its role assignments (Key Vault secret reader)
are scoped to resources in `rg-maa-env`, so they are deleted and recreated with that group. Locks
block deleting role assignments only inside the locked scope
([lock-resources](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/lock-resources)).

## R5. Twelve Data key: Key Vault secret reference

**Decision**: Key Vault (Standard, RBAC authorization, soft-delete retention 7 days, purge
protection **off**) holds the single secret `twelve-data-api-key`. The job declares it as a secret
reference and maps it to the existing variable:

```bicep
configuration: {
  secrets: [ {
    name: 'price-data-api-key'
    keyVaultUrl: '${vault.properties.vaultUri}secrets/twelve-data-api-key'   // versionless → latest
    identity: jobIdentity.id                                                  // id-maa-job resource ID
  } ]
}
template: { containers: [ { env: [ { name: 'PRICE_DATA_API_KEY', secretRef: 'price-data-api-key' } ] } ] }
```

The field names follow the [Microsoft.App/jobs reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/jobs):
`identity` is the "Resource ID of a managed identity to authenticate with Azure Key Vault", and the
identity must also appear under `identity.userAssignedIdentities`. `id-maa-job` gets the built-in
**Key Vault Secrets User** role, scoped to that one secret (FR-019).

**How the value gets in**: `deploy.sh` prompts with `read -rs` and passes it as the `@secure()`
Bicep parameter `twelveDataApiKey`. The secret resource is created only when the parameter is
non-empty, so a later redeploy can leave it empty and keep the stored value. Secure parameters are
not shown by `what-if` and are not kept in deployment history. The admin group `maa-db-admins`
(R16) gets **Key Vault Secrets Officer** on the vault, so the owner can also rotate the value with
`az keyvault secret set`. Assigning the role to the group rather than to the user keeps the
owner's object ID out of the deployment.

**Soft delete and teardown**: purge protection stays off, so `teardown.sh` can purge the vault and
an immediate redeploy can reuse its deterministic name (US4 scenario 3, FR-027).

**Alternatives considered**: a plain job secret (value stored in the job resource). Rejected: the
secret would be visible to anyone who can read the job, and FR-018 requires Key Vault.

## R6. Container registry: ACR Basic vs GitHub Container Registry

**Decision**: **GitHub Container Registry (GHCR), public package**
`ghcr.io/xaverpilgrimm/market-anomaly-agent:<commit-sha>`. Azure Container Registry Basic remains
the documented alternative. Switching back adds one Bicep module and an AcrPull assignment, and
costs about EUR 4.45 per month (see R10).

**Why**: with ACR the estimate is about EUR 23.33 of the EUR 30 cap (78%), against EUR 18.88 (63%)
with GHCR. That cap must also cover Azure OpenAI once a later feature adds it (constitution
Principle V), so the headroom matters: GHCR leaves about EUR 11 for it, ACR about EUR 7. ACR Basic
is the only optional line item. GHCR is free for public packages, and the repository is already
public. The owner confirmed GHCR on 2026-11-11.

| | ACR Basic | GHCR (public) |
|---|---|---|
| Monthly cost | ~EUR 4.45 (~USD 5.00, daily-billed tier) | EUR 0 |
| Push from GitHub Actions | OIDC identity needs `AcrPush` | `GITHUB_TOKEN` with `packages: write`; no Azure permission |
| Pull by the job | `registries[].identity` + `AcrPull` for `id-maa-job` | Anonymous; no `registries` block, no role |
| Azure permissions of pipeline identity | push + job update | job update only (narrower) |
| Exposure | Private | Image is public. It contains no secret (FR-005, SC-007), and the code is already public |
| Teardown | Images deleted with the group | Images survive teardown, so a rebuild can use the last image at once |
| One-time manual step | none | Set the package's visibility to **public** once, after the first push (a GitHub setting, not an Azure portal step, so FR-024 is unaffected) |

The spec only says "the project's container registry" (FR-007, FR-009, FR-019), so the
requirements stand. The user description named ACR, so this choice is surfaced in the plan summary
for the owner to confirm.

## R7. GitHub Actions: checks, OIDC and deployment

**Decision**: one workflow, `.github/workflows/ci.yml`.

- Triggers: `pull_request` (branches: `main`), `push` (branches: `main`), and `workflow_dispatch`
  (used once on `main` to publish the first image after the first deployment).
- **Job `checks`** (all triggers, `permissions: contents: read`): `uv sync --frozen`, then
  `ruff check`, `ruff format --check`, `mypy`, `pytest` (unit, network-guarded), and
  `pytest -m integration` against a `postgres:17` service container. That container gets a
  throwaway CI-only password and `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres`;
  it is not a credential for anything real. The job also runs `az bicep build --file infra/main.bicep`,
  an offline compile and lint that needs no Azure sign-in.
- **Job `publish-deploy`** (`needs: checks`; `if: github.event_name != 'pull_request' &&
  github.ref == 'refs/heads/main'`; `permissions: contents: read, packages: write, id-token:
  write`):
  1. Build the image with `--build-arg GIT_SHA=${{ github.sha }}` and push `:<sha>` to GHCR.
  2. `azure/login` with `client-id`, `tenant-id` and `subscription-id` taken from **repository
     secrets** (*changed 2026-11-13*, see below; they were repository variables at first).
  3. `az containerapp job update -n job-maa-detect -g rg-maa-env --image ghcr.io/…:<sha>`.
- **No `environment:` key** on the deploy job. With one, the OIDC subject becomes
  `repo:…:environment:<name>` and no longer matches the branch-restricted credential.
- Pull requests never get `id-token: write`. Pull requests from forks get no secrets or OIDC
  anyway.

**Federated credential** (Bicep, `Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials`
on `id-maa-github`):

- issuer `https://token.actions.githubusercontent.com`
- audience `api://AzureADTokenExchange`
- subject `repo:XaverPilgrimm@<owner_id>/market-anomaly-agent@<repo_id>:ref:refs/heads/main`
  (*changed 2026-11-13*; it was `repo:XaverPilgrimm/market-anomaly-agent:ref:refs/heads/main`)

A token request from any other ref is refused (FR-008, SC-007). A user-assigned identity is used
instead of an app registration, so everything stays in Bicep without Microsoft Graph.

**Immutable subject claims** (recorded 2026-11-13, after the first `publish-deploy` run):

- **Error:** `azure/login` failed with `AADSTS700213`: "No matching federated identity record
  found for presented assertion subject
  'repo:XaverPilgrimm@<owner_id>/market-anomaly-agent@<repo_id>:ref:refs/heads/main'".
- **Cause:** since 2026-07-15, GitHub issues immutable subject claims by default for newly created
  repositories: the owner and repository names are each followed by `@` and their numeric GitHub
  ID. This repository is one of them. The federated credential still expected the name-based
  subject, so the two no longer matched.
- **Fix:** the credential's subject is built in `env.bicep` as
  `repo:<owner>@<owner_id>/<repo>@<repo_id>:ref:refs/heads/main`. The names come from
  `githubRepository` (GitHub's exact case, from `gh repo view`). The IDs are not hard-coded:
  `deploy.sh` reads them at deploy time with `gh api repos/<owner>/<repo>` (`owner.id` and `id`),
  refuses anything that is not numeric, and passes them as the Bicep parameters `githubOwnerId` and
  `githubRepositoryId`. The IDs never change, even if the repository or owner is renamed; the names
  in the subject would then need a redeploy.

**Sign-in values as secrets, gated by a variable** (recorded 2026-11-13):

- **Problem:** the same `azure/login` run printed `client-id`, `tenant-id` and `subscription-id` in
  its inputs. The repository is public, so its Actions logs are public too, and values from the
  `vars` context are not masked.
- **Fix:** the three values are repository **secrets** (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
  `AZURE_SUBSCRIPTION_ID`), which GitHub masks in logs. `deploy.sh` sets them with `gh secret set`,
  passing each value from the deployment outputs on stdin: never printed, never on a command line,
  never interactive. It then offers to delete the old repository variables of the same names.
- **Gate:** the `secrets` context is not available in a job-level `if`, so `publish-deploy` can no
  longer skip itself by checking the values. It is gated instead on a non-sensitive repository
  variable, `AZURE_DEPLOY_ENABLED == 'true'`, which `deploy.sh` offers to set after the secrets.
  Until it is set the job is skipped, as before, rather than failing. If the variable is set but a
  secret is missing, the job's first step fails with a message naming the missing secret, before
  anything is pushed. Chosen over a step-level check alone, which would turn every merge before
  the first deployment into a failed run.

**Redeploy keeps the job's image** (checked 2026-11-13): `deploy.sh` already passed the job's
current image as `jobImage`, so a rerun after `publish-deploy` has switched the job does not reset
it to the placeholder. One gap was fixed: the lookup used `az containerapp job show … 2>/dev/null
|| true`, so any failure (a missing CLI extension, a transient error) silently fell back to the
placeholder and would have rolled the job back. Now the placeholder is used only when
`rg-maa-env` or the job provably does not exist; any failed lookup stops the deployment.

**Pipeline identity's permission**: a custom role **"Market Anomaly Agent job image updater"**
(defined in Bicep at subscription scope, assigned at the scope of `job-maa-detect` only) with the
actions `Microsoft.App/jobs/read` and `Microsoft.App/jobs/write`. It deliberately leaves out
`Microsoft.App/jobs/start/action`: the jobs documentation warns that whoever can start a job can
reference its secrets and identities through the start-template override. Check Q5 in
quickstart.md verifies the role on the first pipeline run. If `az containerapp job update` turns out
to also need `Microsoft.App/managedEnvironments/join/action` (on the environment) or
`Microsoft.App/jobs/listSecrets/action` (which returns only the Key Vault reference, not the
value), add exactly that action and record it.

**Confirmed by check Q5 (2026-11-13):** `publish-deploy` succeeded with the custom role only
(`Microsoft.App/jobs/read` and `Microsoft.App/jobs/write`, at job scope).
`Microsoft.App/managedEnvironments/join/action` was not needed, and neither was
`Microsoft.App/jobs/listSecrets/action`; the role stays as it is.

**Bicep ↔ pipeline drift**: the pipeline changes the job's image outside Bicep. `deploy.sh`
therefore reads the image currently on the job (`az containerapp job show …`) and passes it as the
`jobImage` parameter, so a redeploy never rolls the job back. On the very first deployment there is
no image yet. `jobImage` then defaults to the public placeholder
`mcr.microsoft.com/k8se/quickstart-jobs:latest`, and the owner runs
`gh workflow run ci.yml --ref main` to publish and switch to the real image.

## R8. TLS to the database

**Decision**:

- Server: the `require_secure_transport` parameter is set explicitly to `on` (the default, pinned
  in Bicep so it cannot drift), and `ssl_min_protocol_version` to `TLSv1.2`.
- Client in Azure: `DATABASE_URL` carries
  `sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt`.
- In Entra mode the application refuses to connect unless `sslmode=verify-full` is present
  (FR-016).

**Where libpq finds the CA certificates**: without `sslrootcert`, libpq reads
`~/.postgresql/root.crt`, which the image does not have, so every `verify-full` connection would
fail. The job's `DATABASE_URL` therefore always names the file. The two ways the owner proposed
were checked on 2026-11-11:

1. **`sslrootcert=system` does not work with the `psycopg[binary]` wheel.** With `system`, libpq
   uses OpenSSL's default trust store. The wheel in use (psycopg 3.3.6, bundled libpq 18, OpenSSL
   3.5.8 in `psycopg_binary.libs`) has OpenSSL compiled with `OPENSSLDIR: "/host/tmp/libpq.build"`,
   a path on the wheel's build machine. Its default store would be looked up under that directory,
   which does not exist in the image, so `system` would fail there (unless `SSL_CERT_FILE` were also
   set, which would be the explicit path again by another route). This was checked by reading the
   `OPENSSLDIR` string from the bundled `libcrypto`.
2. **Shipping Microsoft's roots in the repository works, but it is not needed.** Microsoft
   documents that the servers' certificates chain to **DigiCert Global Root G2** and **Microsoft
   RSA Root CA 2017** (subject `Microsoft RSA Root Certificate Authority 2017`). It also says
   clients should trust both and only roots, not intermediates
   ([TLS in Azure Database for PostgreSQL](https://learn.microsoft.com/en-us/azure/postgresql/security/security-tls),
   [configure TLS](https://learn.microsoft.com/en-us/azure/postgresql/security/security-tls-how-to-connect)).
   Both roots are part of the Mozilla store that Debian's `ca-certificates` package installs as
   `/etc/ssl/certs/ca-certificates.crt`. This was checked in an Ubuntu 24.04 bundle built from the
   same store (`ca-certificates 20260601`): DigiCert Global Root G2 expires 2038-01-15 and Microsoft
   RSA Root Certificate Authority 2017 expires 2042-07-18.

**Decision**: use the system store, but name it by file. The image installs `ca-certificates` in
its `python:3.12-slim-bookworm` runtime stage, and the job's `DATABASE_URL` carries
`sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt`. libpq then loads exactly that
file and does not depend on the bundled OpenSSL's compiled-in paths or on `~/.postgresql`. The
bundle is refreshed with every image build, so a root rotation needs no repository change.

**Guards**:

- The image build fails unless that file loads with Python's `ssl` module and contains both
  roots by subject name (tasks.md T016). A base image without them can never be published.
- Quickstart check Q7 makes a real `verify-full` connection from the published image, plus a
  negative connection that must fail without the bundle, so the test is known to check something.

**Confirmed by check Q7 part 2 (2026-11-13):** from the published image, a `verify-full`
connection with `sslrootcert=/etc/ssl/certs/ca-certificates.crt` printed `(True, 'TLSv1.3')`, and
the same connection with `sslrootcert=/dev/null` failed with "could not read root certificate
file". So the image's libpq finds and uses the bundle, the negative run proves the check is real,
and the fallback below is not needed.

**Fallback**, if a future base image dropped either root: copy the two PEM files from Microsoft's
documentation into the image as `/app/certs/azure-postgres-roots.pem` and point `sslrootcert` at
that file. Roots only, never intermediates, as Microsoft advises.

**Owner-side clients** (`psql`, `pg_dump` and `pg_restore` in the `postgres:17` container,
deployment-contract.md §6) also use `sslrootcert=/etc/ssl/certs/ca-certificates.crt`, the same
path as the job (*revised 2026-11-12*). The earlier plan, `sslrootcert=system`, failed in
`db-bootstrap.sh` with "certificate verify failed": the `postgres:17` image has no trusted roots in
`/etc/ssl/certs`. `pg_client` in `_common.sh` therefore checks that the host's
`/etc/ssl/certs/ca-certificates.crt` exists and mounts it read-only into the container at the same
path. On the host, `openssl s_client -starttls postgres` verified the server (return code 0) with
the chain: server certificate → `Microsoft TLS G2 RSA CA OCSP 10` → `Microsoft TLS RSA Root G2` →
`DigiCert Global Root G2`, one of the two roots the image's CA guard checks for (tasks.md T023).

## R9. Failure alerting

**Decision**: one action group (email to the deployment-time address), plus two scheduled query
rules on the Log Analytics workspace:

1. **Execution failed**: runs every 5 minutes over a 10-minute window. It looks for
   `ContainerAppSystemLogs_CL` rows (or `ContainerAppSystemLogs`, whichever the workspace has) for
   `job-maa-detect` that report a failed execution or a failed or timed-out replica. Each attempt's
   failure fires it, so the owner may get up to 3 emails for one bad execution. FR-022 asks for at
   least one; deduplicating is not worth the extra machinery. *(Superseded 2026-11-16 by Q4: one
   alert per failed execution, see below.)* This rule meets SC-004 (within
   15 minutes).
2. **No completed run**: evaluated every hour (`PT1H`). Scheduled query rules run at a fixed
   frequency, not at a time of day, so the query itself holds the schedule. It returns a row only
   when the evaluation time is between 12:45 and 13:45 UTC on a Tuesday to Saturday, and
   `ContainerAppConsoleLogs_CL` holds no line containing `status=completed` from `job-maa-detect`
   since 11:00 UTC that day. It therefore fires at most once a day. FR-002 makes even an "already stored" start print that line, so every
   healthy scheduled day produces one. The rule catches whatever rule 1's reason strings miss: an
   image that cannot be pulled, an unforeseen event name, a broken schedule.

**Rationale**: Microsoft's pages document neither a job-failure metric nor the exact failure
`Reason_s` values. Community guides show `Reason_s == "Completed"` for success and say failure
reasons vary, and that a failure is sometimes marked only when the replica timeout is reached
([azureossd troubleshooting](https://azureossd.github.io/2023/10/30/Troubleshooting-failed-job-executions-on-Container-App-Jobs),
[practical guide: job execution history](https://yeongseon.github.io/azure-container-apps-practical-guide/troubleshooting/kql/dapr-and-jobs/job-execution-history)).
Check Q4 in quickstart.md forces a failing execution, so the real reason strings and columns can
be read from the workspace and set in the rule. Rule 2 guards against getting them wrong.

**Alternatives considered**: Event Grid or a function app reacting to job events. Rejected: an
extra service (Principle II).

*Implemented 2026-11-14 (tasks.md T034–T036), not yet deployed:* `Microsoft.Insights/actionGroups@2023-01-01`
(newest GA; 2024-10-01 is preview) and `Microsoft.Insights/scheduledQueryRules@2026-03-01` (newest
GA). Two deviations from the plan: rule 2's `windowSize` is `PT3H`, not `PT2H`, because the
window also filters the tables and an evaluation at 13:45 must still see a line from 11:00; and
it matches `Log_s contains 'status=completed'` instead of `has`, because `has` matches whole
terms and `=` splits the string into two. Both rules set `skipQueryValidation: true`, because a
recreated workspace (US4) has no `_CL` tables until the environment writes logs, and validation
would fail the deployment. The reason strings and column names stay provisional until Q4.

*Pinned 2026-11-16 from check Q4 (run 2026-11-14):*
- **Forcing the failure:** `az containerapp job start … --image "$image" --container-name detect
  --cpu 0.5 --memory 1.0Gi --args=--date=2026-11-08`. The `--args=` form passes a value that
  starts with `-` without "unrecognized arguments". The execution failed after 3 attempts, each
  logging `detect: error: 2026-11-08 is not a NASDAQ trading day`.
- **Columns:** `ContainerAppSystemLogs_CL` has `JobName_s`, `ExecutionName_s`, `ReplicaName_s`,
  `Reason_s`, `Log_s`, `Type_s` and `EventSource_s`; `ContainerAppConsoleLogs_CL` has
  `ContainerJobName_s`, `ContainerGroupName_s`, `ContainerName_s` and `Log_s`. So `JobName_s`
  (rule 1) and `ContainerJobName_s` (rule 2) are confirmed.
- **Reasons:** each failed attempt logs `Error`; the execution then logs `BackoffLimitExceeded`
  when the retries are used up. The Q3 timeout execution logged `Error` and `DeadlineExceeded`.
  Rule 1 now matches only `Reason_s in ('BackoffLimitExceeded', 'DeadlineExceeded')` for
  `job-maa-detect`, summarized to one row per `ExecutionName_s`: one alert per failed
  execution instead of one per attempt.
- **Emails:** the provisional rule sent two "Fired" emails for the one execution, the first about
  8 minutes after the start (SC-004 passes) and a duplicate 5 minutes later, because the
  10-minute window evaluated every 5 minutes saw the same rows twice. The overlap stays, for late
  logs; `muteActionsDuration: 'PT1H'` suppresses the repeat. The reference documents
  `muteActionsDuration` for `kind: LogAlert` and states no constraint with `autoMitigate`; its own
  sample uses `autoMitigate: false`. The rule has no dimensions, so the mute applies to the whole
  rule.
- **Provider registration:** `Microsoft.Insights` was `NotRegistered` in this subscription; the
  owner registered it on 2026-11-14 (`az provider register -n Microsoft.Insights --wait`).
  `preflight.sh` now checks `Microsoft.App`, `Microsoft.Consumption` (budget, US5),
  `Microsoft.DBforPostgreSQL`, `Microsoft.Insights`, `Microsoft.KeyVault`,
  `Microsoft.ManagedIdentity` and `Microsoft.OperationalInsights` and prints the register command for
  any that is not `Registered`.

*Retest 2026-11-16/17, after the redeploy with the pinned rule:* `muteActionsDuration: 'PT1H'`
together with `autoMitigate: false` was accepted and is live. A deliberate failing start on
2026-11-16 at 21:22 UTC sent exactly one email, so the duplicate is gone. The scheduled run on
2026-11-17 succeeded in about 13 minutes, and rule 2 (`alert-maa-no-completed-run`) sent no
email that day, so its 12:45–13:45 UTC evaluation found the `status=completed` line as intended.

## R10. Monthly cost estimate and budget

**Cap**: the constitution (v1.2.0, Principle V) sets the Azure cap at **EUR 30** per month. It is
stated in EUR because Azure bills this subscription in EUR, so the budget compares like with like
and needs no conversion. At about 1.12 USD per EUR (mid-market quotes from 6 to 9 November 2026,
for example [exchange-rates.org](https://www.exchange-rates.org/converter/eur-usd)), EUR 30 is
about USD 34. Together with the X API's USD 15 that is about USD 49, within the USD 50 total.

**Decision**: a subscription-scope budget `budget-maa-monthly` of **EUR 30**. Notifications on
**actual** cost at 50% (EUR 15), 80% (EUR 24) and 100% (EUR 30), sent to the deployment-time
address. Azure's EUR list prices are set by Microsoft, not converted daily, so actual charges and
the budget are both in EUR. The README states that budgets only notify and never stop spending
(FR-028).

The exchange rate is used only to convert the USD-quoted price estimates below. It does not affect
the budget.

**Estimate**, pay-as-you-go list prices, taken for Germany West Central before the region moved
to France Central (R2). These are estimates: the PostgreSQL figures come from third-party price
trackers because Microsoft's calculator could not be read automatically. Check Q2 in quickstart.md
re-prices them for France Central in the [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/)
in EUR before the first deployment.

| Resource | Basis | USD / month | EUR / month (×0.89 EUR per USD) |
|---|---|---|---|
| PostgreSQL Flexible Server, B1ms compute | 730 h, ~USD 15 in GWC ([bytebase dbcost](https://www.bytebase.com/dbcost/azure-flexible/instance/B1ms/)) | 15.00 | 13.35 |
| PostgreSQL storage | 32 GiB minimum, ~USD 0.13/GiB | 4.20 | 3.74 |
| PostgreSQL backup | 7-day retention; free up to 100% of provisioned storage | 0.00 | 0.00 |
| Container Apps job (consumption) | 0.5 vCPU / 1 GiB. Worst case 22 days × 3 attempts × 14 min = 27,720 vCPU-s and 55,440 GiB-s, inside the free grant of 180,000 vCPU-s and 360,000 GiB-s per subscription | 0.00 | 0.00 |
| Container Apps environment (consumption only) | No idle charge | 0.00 | 0.00 |
| Log Analytics | < 0.2 GB/month ingested, inside the 5 GB free allowance; 30-day retention (free up to 31 days) | 0.00 | 0.00 |
| Alert rule 1 (5-minute frequency) | ~USD 1.50 per rule | 1.50 | 1.34 |
| Alert rule 2 (hourly) | ~USD 0.50 per rule (frequencies of 15 minutes or longer) | 0.50 | 0.45 |
| Action group email, budget | Free | 0.00 | 0.00 |
| Key Vault Standard | ~1,000 operations, USD 0.03 per 10,000 | 0.01 | 0.01 |
| Bandwidth | Image pull from GHCR is inbound (free); egress < 1 GB | 0.00 | 0.00 |
| **Subtotal with GHCR (chosen)** | | **21.21** | **18.88** (63% of EUR 30) |
| Azure Container Registry Basic (alternative) | ~USD 0.167/day | 5.00 | 4.45 |
| **Subtotal with ACR** | | **26.21** | **23.33** (78% of EUR 30) |

**Check Q2 result** (2026-11-13, Azure pricing calculator, France Central, EUR):

| Item | EUR / month |
|---|---|
| PostgreSQL Flexible Server, Burstable B1ms, 730 h, Premium SSD 32 GiB | about 16.30 |
| Container Apps job and environment, Log Analytics, Key Vault | within the free grants, or a few cents at this usage |
| Two log alert rules | **not priced yet**: they arrive with US3 (tasks.md T034–T036) and must be added to this estimate then; the estimate above puts them at about EUR 1.79 |
| **Total so far** | **about 16.30** (54% of EUR 30), below the EUR 24 limit of check Q2 |

This replaces the third-party PostgreSQL figures above (EUR 17.09 for compute and storage together)
for France Central. With the alert rules at the estimated EUR 1.79 the total would be about
EUR 18.09 (60% of the cap), still below EUR 24.

*Budget implemented 2026-11-18 (tasks.md T047):* `Microsoft.Consumption/budgets@2024-08-01` at subscription scope, `budget-maa-monthly`, EUR 30, notifications `actual50`, `actual80` and `actual100` (`GreaterThanOrEqualTo`, `Actual`) to the deployment-time address. It is outside both resource groups, so a teardown keeps it. *Deployed 2026-11-18:* the what-if showed it as the only Create, Azure accepted API version `2024-08-01` (kept), and `az consumption budget show` returned amount 30.0, Monthly, start 2026-11-01 and thresholds 50/80/100 (tasks.md T049).

**Watch items**: Microsoft Defender for Cloud plans must stay off (they bill per resource). A
General Purpose SKU, high availability or geo-redundant backup would each break the cap and are
not used.

## R11. Application changes (feature 001 code)

| Concern | Decision | Rationale |
|---|---|---|
| New mode | `detect --latest`, mutually exclusive with `--date`, exactly one required. Day = `calendar.previous_trading_day(now_in(America/New_York).date())`. The clock is injected for tests. | Reuses the existing NASDAQ calendar (FR-001). |
| Already-stored check | In `--latest` mode, after migrations and before reading the API key, the universe or any vendor call: `load_run(conn, day)`. If found, log INFO, print the stored summary, exit 0. | FR-002: no vendor request, no write, no false exit 3. |
| Migrations | New flag `--migrate`, valid with either mode. It applies `apply_migrations` in the same process, inside the same scrubbed error handling. The job runs `detect --latest --migrate`. | FR-006. One process, so a migration error is redacted and exits 1 like any other error. `--date` without the flag behaves exactly as before. |
| Start log | One INFO line: `start mode=<latest|date> evaluated_date=<d> version=<APP_COMMIT or "unknown">`. | FR-004. |
| Version | `APP_COMMIT` is set in the image from build arg `GIT_SHA`, plus the label `org.opencontainers.image.revision`. | FR-005. |
| Entra auth | `storage/db.py`: `DATABASE_AUTH=password` (default, unchanged) or `entra`. In `entra` mode, `DATABASE_URL` must have no password and must have `sslmode=verify-full`. The token comes from `azure.identity.ManagedIdentityCredential(client_id=$AZURE_CLIENT_ID)` for scope `https://ossrdbms-aad.database.windows.net/.default`, is passed as `password=` to `psycopg.connect`, and is registered with the scrubber. | FR-017a, FR-020, FR-023. Microsoft's Container Apps page says a user-assigned identity's client ID must be given explicitly to the Azure Identity library. |
| Dependency | Add `azure-identity` to runtime dependencies. | It is the supported client: it finds the platform identity endpoint and caches tokens. Rejected: hand-written HTTP to `IDENTITY_ENDPOINT` (the docs do not state that jobs set it, and it means more code to own). |
| Redaction | `cli._secrets()` also returns every access token issued in the process. | FR-023: a token is a credential for its lifetime. |

## R12. Image

**Decision**: a two-stage `Containerfile` at the repository root.

- Build stage: `python:3.12-slim-bookworm`, the same image as the runtime stage, so the venv's
  interpreter path (`/usr/local/bin/python3.12`) is identical in both. uv is added as uv's Docker
  guide recommends, `COPY --from=ghcr.io/astral-sh/uv:<pinned> /uv /uvx /bin/` (now `0.12.19`, the
  release in use). Then `uv sync --frozen --no-dev --no-editable` with system Python only
  (`UV_PYTHON_DOWNLOADS=never`, `UV_PYTHON_PREFERENCE=only-system`).
- Rejected: the combined tag `ghcr.io/astral-sh/uv:0.12.19-python3.12-bookworm-slim`. It does not
  exist ("manifest unknown" on the first host build, 2026-11-11), and a uv-provided Python base
  could differ from the runtime image's interpreter path.
- Runtime stage: `python:3.12-slim-bookworm` with `ca-certificates`, running as a non-root user.
  `ENTRYPOINT ["detect"]`; the job passes `--latest --migrate` as its arguments.
- `.containerignore` excludes `.env`, `.git`, `specs/`, `tests/` and caches (FR-005).

The universe CSV is package data under `src/`, so it is copied into the image with the package.

**Local use**: `podman build` and `podman run` work on the host for a smoke test (see
quickstart.md). The daily image is only ever built by the pipeline.

## R13. What-if before every real deployment

**Decision**: all infrastructure is one subscription-scope deployment of `infra/main.bicep`, which
creates both resource groups, the budget and the custom role. `deploy.sh` always runs
`az deployment sub what-if` first and then `az deployment sub create --confirm-with-what-if`, so
nothing is applied without the owner reading and confirming the diff. The first real deployment
also requires a clean what-if (quickstart check Q6). Pull requests compile and lint the Bicep
offline (R7).

**CanNotDelete side effect**: a lock on a resource group "prevents Resource Manager from
automatically deleting deployments in the history. If you reach 800 deployments in the history,
your deployments fail." The data group receives one module deployment per `deploy.sh` run, so
this is far away. The README notes how to prune the history if it is ever needed.

## R14. Firewall rule for the owner under the lock

**Decision**: there are two firewall rules on the server:

- `AllowAllAzureServicesAndResourcesWithinAzureIps` (0.0.0.0–0.0.0.0, Microsoft's convention for
  "allow Azure services")
- `owner-access`

`owner-access` is never deleted. `db-access.sh open` updates it to the owner's current public IP.
`db-access.sh close` updates it to `192.0.2.1`, a reserved documentation address (RFC 5737
TEST-NET-1) that is never routed on the internet. Every Bicep deployment resets it to closed.

**Rationale**: the CanNotDelete lock on `rg-maa-data` is inherited by child resources ("When you
apply a lock at a parent scope, all resources within that scope inherit the same lock"), so
deleting a firewall rule would be refused (*inference* from the lock documentation). Updating a
rule is allowed: "authorized users can read and modify a resource, but they can't delete it". The
Azure-services rule admits any Azure tenant at the network level. That is accepted because
password login is disabled and only Entra principals with a database role can connect (spec edge
case).

## R15. Commands run on the host only

**Decision**: every `az`, `gh`, `psql`, `pg_dump` and `podman build` command in this feature is
run by the owner on the host, never inside the agent container, and never by Claude Code. The
scripts in `infra/scripts/` start by refusing to run in the agent container (they check
`/run/.containerenv` and exit 1). `psql`, `pg_dump` and `pg_restore` are used from a
`docker.io/library/postgres:17` container started with `podman run --rm` on the host, so no local
client installation is needed. The only Azure sign-in outside the host is GitHub Actions through
the OIDC federated credential (R7).

## R16. Entra administrator: a security group, and no identifiers in the public repository

**Decision**: the server's Entra administrator is the security group **`maa-db-admins`**, whose
only member is the owner's account. It is not the owner's user account.

- `infra/scripts/entra-bootstrap.sh` (host) creates the group if it is missing:
  `az ad group create --display-name maa-db-admins --mail-nickname maa-db-admins`. Groups created
  this way are security groups.
- It reads the signed-in user's object ID at run time
  (`az ad signed-in-user show --query id -o tsv`) and adds that user if not yet a member
  (`az ad group member check`, then `az ad group member add`).
- It fails, without removing anyone, if the group has any other member, so the "only member" rule
  is checked on every deployment.
- `deploy.sh` runs it first, then reads the group's object ID (`az ad group show --group
  maa-db-admins --query id -o tsv`) and passes it as the deployment parameter
  `dbAdminGroupObjectId`.
- Bicep sets `flexibleServers/administrators/<dbAdminGroupObjectId>` with
  `principalType: 'Group'` and `principalName: 'maa-db-admins'`, and assigns Key Vault Secrets
  Officer to the group (R5).
- The owner signs in to PostgreSQL with **user `maa-db-admins`** and their own token from
  `az account get-access-token --resource-type oss-rdbms`. Microsoft's documentation describes
  group members signing in with the group's name as the role name and their own token
  ([manage Entra users](https://learn.microsoft.com/en-us/azure/postgresql/security/security-manage-entra-users);
  the admin "can be a user, group, service principal, or managed identity").

**Rationale**:

- The owner's account is a personal Microsoft account. Microsoft's PostgreSQL pages list groups as
  supported administrators but say nothing about personal accounts.
- A group ties admin access to membership rather than to one user. If personal accounts turn out
  not to work, a cloud-only work account in the same tenant can be added to the group instead
  (and the personal account removed), without changing the server or redeploying.
- The Entra group is a directory object, not an Azure resource, so it is created by the host
  script rather than by Bicep. FR-024 covers Azure resources, and the group is still created from
  the repository, never by hand.

**Open risk, gated**: whether the server accepts a personal account's token through group
membership is not documented. Check Q7 in quickstart.md is the first sign-in
(`db-bootstrap.sh`). If it fails with an authentication error, use the work-account fallback above
before anything else.

**No identifiers in files**: the repository is public. No file may contain the owner's email
address, tenant domain, tenant ID, subscription ID or any object ID (spec FR-025). Scripts read
them at run time from `az`:

| Value | Read with |
|---|---|
| Owner's object ID | `az ad signed-in-user show --query id -o tsv` |
| Group's object ID | `az ad group show --group maa-db-admins --query id -o tsv` |
| Tenant ID | `az account show --query tenantId -o tsv` |
| Subscription ID | `az account show --query id -o tsv` |

The documentation uses placeholders such as `<tenant-id>`, `<subscription-id>` and
`<server-fqdn>`. The pipeline gets the tenant ID, the subscription ID and its own client ID from
GitHub repository **secrets**, set by `deploy.sh` through `gh secret set` (*changed 2026-11-13*
from variables, because the public Actions logs showed them; R7); these are never written to a
file. The numeric GitHub owner and repository IDs in the OIDC subject are read with `gh api` at
deploy time and not written to a file either. Fixed public names (`maa-db-admins`, `id-maa-job`, the repository slug) and
Microsoft's documented built-in role definition IDs are not personal identifiers and may appear in
files.
