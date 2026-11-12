# Quickstart & Validation: Unattended Daily Run on Azure

**Feature**: [spec.md](./spec.md) · Commands and scripts: [contracts/deployment-contract.md](./contracts/deployment-contract.md)
· CLI: [contracts/cli-contract.md](./contracts/cli-contract.md)

> **Host only.** Every `az`, `gh`, `podman` and database-client command below runs on the
> owner's host, never inside the agent container (research R15). Claude Code does not run any of
> them.
>
> **Placeholders.** The repository is public, so this guide never shows the owner's email
> address, tenant domain, tenant ID, subscription ID or any object ID (research R16). Where one is
> needed it appears as `<tenant-id>`, `<subscription-id>`, `<server-fqdn>` and so on, and the
> scripts read the real values at run time from `az`.

## 0. Prerequisites (host)

- `az` signed in to the project subscription as the owner, with Owner rights (or Contributor plus
  User Access Administrator), and with the right to create security groups in the tenant (the
  default for the account that created the tenant).
- `gh` signed in to `XaverPilgrimm/market-anomaly-agent`.
- `podman`, for the `postgres:17` client container.
- The Twelve Data key and the alert email address at hand. Neither goes into any file.

## 1. Pre-deployment checks (all must pass before the first real deployment)

| # | Check | How | Pass condition |
|---|---|---|---|
| Q1 | Region offers everything | `infra/scripts/preflight.sh germanywestcentral` (wraps `az postgres flexible-server list-skus -l …` and `az provider show -n Microsoft.App …`) | `Standard_B1ms` (Burstable) and version `17` listed; `jobs` lists the region. Otherwise rerun with the next fallback (`westeurope`, `northeurope`, `swedencentral`) and set `location` (research R2). |
| Q2 | Cost in EUR | Azure pricing calculator, chosen region, EUR: B1ms 730 h, 32 GiB storage, two log alert rules | Total ≤ EUR 24 (the 80% notification threshold of the EUR 30 budget, so a normal month never triggers it); update research R10 with the real figures |
| Q6 | What-if is clean | `infra/scripts/deploy.sh` first runs `entra-bootstrap.sh` (prints `admin group ok`), then stops at the what-if prompt; answer **n** the first time and read it | The group `maa-db-admins` exists with you as its only member (`az ad group member list --group maa-db-admins --query "[].displayName"`). Only the resources in data-model.md §3 are created; nothing outside `rg-maa-data`, `rg-maa-env` and the subscription-scope budget and role is touched. The Entra administrator in the what-if is a `Group`. When you later answer **y** (T028), the first real deployment can fail while the new Key Vault role assignment for `id-maa-job` propagates, for example with an error that the job cannot read its secret reference. `deploy.sh` is idempotent: wait a few minutes and run it again. |

## 2. Deploy and bootstrap

```bash
infra/scripts/deploy.sh            # entra-bootstrap → what-if → confirm "y" → create; prompts for key and email
gh variable set AZURE_CLIENT_ID --body <client-id>              # deploy.sh offers to do these three
gh variable set AZURE_TENANT_ID --body <tenant-id>
gh variable set AZURE_SUBSCRIPTION_ID --body <subscription-id>
infra/scripts/db-access.sh open
infra/scripts/db-bootstrap.sh      # signs in as maa-db-admins with your token (check Q7); role, database, grants
infra/scripts/db-access.sh close
gh workflow run ci.yml --ref main  # first real image → job
```

**Check Q7, part 1: admin sign-in through the group (research R16).** `db-bootstrap.sh` is the
first sign-in as `maa-db-admins`. It must print `bootstrap ok`. If it fails with an authentication
error (for example `password authentication failed for user "maa-db-admins"` or an
invalid-token message), the server does not accept the personal account through the group. Then
create a cloud-only work account in the tenant, add it to `maa-db-admins`, remove the personal
account, sign in to `az` with the work account, and rerun `entra-bootstrap.sh` and
`db-bootstrap.sh`. The server does not change.

**Check Q7, part 2: a real `verify-full` connection from the job image (research R8).** The unit
tests replace `psycopg.connect`, so only a real connection proves that the image's libpq finds the
CA bundle. Run this after the first image has been published (the `gh workflow run` below), with
`db-access.sh open`. `<sha>` is the published tag and `<server-fqdn>` comes from the `deploy.sh`
output:

```bash
export PGPASSWORD="$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv)"
IMAGE=ghcr.io/xaverpilgrimm/market-anomaly-agent:<sha>
CHECK='import psycopg, sys
conn = psycopg.connect(f"host=<server-fqdn> dbname=market_anomaly_agent user=maa-db-admins sslmode=verify-full sslrootcert={sys.argv[1]}")
print(conn.execute("SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone())'
podman run --rm -e PGPASSWORD --entrypoint python "$IMAGE" -c "$CHECK" /etc/ssl/certs/ca-certificates.crt
podman run --rm -e PGPASSWORD --entrypoint python "$IMAGE" -c "$CHECK" /dev/null
unset PGPASSWORD
```

| Run | Pass condition |
|---|---|
| With `/etc/ssl/certs/ca-certificates.crt` (what the job uses) | Prints `(True, 'TLSv1.3')` or `(True, 'TLSv1.2')` |
| With `/dev/null` (no trusted roots) | Fails with a certificate error, for example `certificate verify failed` or `could not read root certificate file`. This proves the first run really verified the server. |

If the first run fails with a certificate error, switch to the fallback in research R8 (ship
Microsoft's two roots as `/app/certs/azure-postgres-roots.pem`). The first scheduled or manual
execution of the job (§4, US1-1) then confirms the same path end to end with the job's own
identity.

After the first push, set the GHCR package `market-anomaly-agent` to **public** once (package
settings on GitHub). Without that step, the job cannot pull the image.

## 3. Platform behaviour checks (before relying on the schedule)

| # | Check | How | Pass condition |
|---|---|---|---|
| Q3 | Timeout scope (research R1) | Start one execution of a temporary copy of the job with `replicaTimeout 120`, `replicaRetryLimit 2`, command `sh -c 'sleep 50; exit 1'`: `az containerapp job create … --image docker.io/library/busybox:1.37` in `rg-maa-env`. Count the replicas in `az containerapp job execution show`. Delete the copy afterwards. | **3 replicas** ⇒ the timer is per attempt: set `replicaTimeout` to `1800`. **2 replicas** (the third cut off at 120 s) ⇒ the timer is per execution: keep `3600`. Record the result in research R1. |
| Q4 | Failure alert fires | Start one execution with an overridden argument that fails fast: `az containerapp job start … --args "--date" "2026-11-08"` (a Sunday, so exit 2) | Within 15 minutes an email arrives from `alert-maa-execution-failed`. Read the actual `Reason_s` and columns in `log-maa`, and pin them in the rule (research R9). |
| Q5 | Pipeline least privilege | First `push` to `main` after deployment | `publish-deploy` succeeds with the custom role only. If it fails with an authorization error, add exactly the missing action and record it in research R7. The likeliest one is `Microsoft.App/managedEnvironments/join/action`: updating a job can require it on the environment `cae-maa`. If so, grant it on the environment's scope only (a second, narrow custom role or an extra assignment), not by widening the role on the job. |

## 4. Feature scenarios

| Spec | Scenario | How | Expected |
|---|---|---|---|
| US1-1, US1-2 | First execution fills an empty database | `az containerapp job start -n job-maa-detect -g rg-maa-env` | Execution `Succeeded` in ~13 min. Logs show `start mode=latest evaluated_date=<prev trading day> version=<sha>`, the applied migration `0001_init.sql`, and the last line `… status=completed …`. The owner query (contract §6) shows that day in `detection_runs`. |
| US2, SC-002 | Repeated start is free | Start again straight away | `Succeeded` in < 2 min. Log says "already stored". The Twelve Data dashboard shows 0 new credits. Row counts unchanged. |
| US2 / SC-003 | Holiday | The morning after a market holiday (or a unit test with an injected clock) | Resolves to the trading day before the holiday: already stored, exit 0, no exit 3 |
| US1-3, US1-5, SC-010 | Pipeline | Open a pull request: checks run, `publish-deploy` is skipped, no Azure login in the log. Merge: a new `:<sha>` tag appears and the job shows that image. | As described |
| US1-4 | Failing checks block delivery | Open a pull request with a deliberate `ruff` violation and confirm `checks` fails. Read `ci.yml` to confirm `publish-deploy` has `needs: checks` (so a failing push to `main` cannot reach it). Close the pull request unmerged. | `checks` red, `publish-deploy` not run, no new tag, job image unchanged |
| US3, SC-008 | Bounded retries | Temporarily set the vault secret to an invalid key and start once. Restore the key afterwards. | ≤ 3 attempts, about 303 vendor requests at most, nothing stored, failure email |
| US4-2, FR-026 | Idempotent deploy | `deploy.sh` again, no changes | What-if shows no changes (except a rule left open) |
| US4-3/4, SC-005 | Teardown and rebuild | `teardown.sh`, then at once `deploy.sh` and `gh workflow run ci.yml --ref main` | Redeploy succeeds; no bootstrap needed (identity kept); the next execution says "already stored" for the last day; all earlier days are still present |
| US4-5, FR-030 | Lock | `az group delete -n rg-maa-data` | Refused by the lock |
| SC-007 | No secret leaks | `podman image inspect` and `podman run --rm --entrypoint sh <image> -c 'env; ls -la /app'` show no key. Search the Actions logs and `log-maa` for the first 6 characters of the key: 0 hits. A password login attempt with `psql` → `password authentication failed` or `not permitted`. | As described |
| SC-007 | Branch-restricted OIDC | Run the workflow on a branch with `workflow_dispatch` | `azure/login` fails with `AADSTS70021` (no matching federated credential) |
| US5, FR-028 | Budget | `az consumption budget show --budget-name budget-maa-monthly` | EUR 30; notifications at 50/80/100% (EUR 15 / 24 / 30) on actual cost |
| SC-001 | Five days unattended | Leave it running for a week | 5 trading days stored, no manual action |

## 5. Decommission and restore (only when pausing or ending the project)

```bash
infra/scripts/decommission.sh ~/private/maa-$(date +%F).dump   # path must be outside the repo
# … later …
infra/scripts/deploy.sh && infra/scripts/db-access.sh open && infra/scripts/db-bootstrap.sh
infra/scripts/restore.sh ~/private/maa-<date>.dump && infra/scripts/db-access.sh close
```

Expected (SC-009): `SELECT count(*) FROM detection_runs` matches the count taken before
decommission, and the next execution reports the last stored day as "already stored".

## 6. Local workflow still works

```bash
podman compose up -d db
uv run detect --date 2026-11-10 --migrate     # password DATABASE_URL from .env, as in 001
uv run detect --latest                         # new mode, locally
uv run pytest && uv run pytest -m integration
```
