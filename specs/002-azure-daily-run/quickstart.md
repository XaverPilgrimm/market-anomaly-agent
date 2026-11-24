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
| Q1 | Region offers everything | Two steps. (a) `infra/scripts/preflight.sh francecentral` (wraps `az postgres flexible-server list-skus -l …` and `az provider show -n Microsoft.App …`): a first filter only, because the listing can pass while provisioning is blocked (it passed `germanywestcentral`, which then refused the probe server; research R2). (b) **The decisive check, a probe**: create a B1ms server in a throwaway resource group, then delete the group (commands below). | (a) `Standard_B1ms` (Burstable) and version `17` listed; `jobs` lists the region. (b) The probe server is created. If either fails, repeat both with the fallback `italynorth` and set `location` in `infra/main.bicepparam` (research R2). |
| Q2 | Cost in EUR | Azure pricing calculator, chosen region, EUR: B1ms 730 h, 32 GiB storage, two log alert rules | Total ≤ EUR 24 (the 80% notification threshold of the EUR 30 budget, so a normal month never triggers it); update research R10 with the real figures. *Result 2026-11-13 (France Central): PostgreSQL B1ms, 730 h, Premium SSD 32 GiB about EUR 16.30; Container Apps, Log Analytics and Key Vault within free grants or a few cents. Passed. The two log alert rules are not priced yet: add them to the estimate when they arrive with US3 (research R10).* |
| Q6 | What-if is clean | `infra/scripts/deploy.sh` first runs `entra-bootstrap.sh` (prints `admin group ok`), then stops at the what-if prompt; answer **n** the first time and read it | The group `maa-db-admins` exists with you as its only member (`az ad group member list --group maa-db-admins --query "[].displayName"`). Only the resources in data-model.md §3 are created; nothing outside `rg-maa-data`, `rg-maa-env` and the subscription-scope budget and role is touched. The Entra administrator in the what-if is a `Group`. When you later answer **y** (T028), the first real deployment can fail while the new Key Vault role assignment for `id-maa-job` propagates, for example with an error that the job cannot read its secret reference. `deploy.sh` is idempotent: wait a few minutes and run it again. *Result 2026-11-13: passed during the first deployment.* |

**Q1 probe** (host; a few cents at most for the minutes the server exists). The
`rg-maa-probe` group is separate from the feature's groups and has no lock, so deleting it removes
everything the probe made:

```bash
region=francecentral
az group create -n rg-maa-probe -l "$region" --output none
az postgres flexible-server create -g rg-maa-probe -n psql-maa-probe-$(date +%s) -l "$region" --tier Burstable --sku-name Standard_B1ms --storage-size 32 --version 17 --public-access None --yes
az group delete -n rg-maa-probe --yes
```

Pass: the `create` finishes without an error and prints the new server. A refusal ("The location
is restricted from performing this operation", "not accepting new customers", `SkuNotAvailable`)
fails Q1 for that region. Always run the `az group delete`, whether the create succeeded or not.

## 2. Deploy and bootstrap

```bash
infra/scripts/deploy.sh            # entra-bootstrap → what-if → confirm "y" → create; prompts for key and email
# deploy.sh then offers, y/N each, to set the repository secrets AZURE_CLIENT_ID, AZURE_TENANT_ID
# and AZURE_SUBSCRIPTION_ID (values from the deployment outputs, on stdin, never printed), to set
# the repository variable AZURE_DEPLOY_ENABLED=true, and to delete old variables of those names.
infra/scripts/db-access.sh open
infra/scripts/db-bootstrap.sh      # signs in as maa-db-admins with your token (check Q7); role, database, grants
infra/scripts/db-access.sh close
gh workflow run ci.yml --ref main  # first real image → job; only once main has this ci.yml (see below)
```

*Changed 2026-11-13.* The first `publish-deploy` run failed at `azure/login` with `AADSTS700213`
("No matching federated identity record found for presented assertion subject
'repo:XaverPilgrimm@<owner_id>/market-anomaly-agent@<repo_id>:ref:refs/heads/main'"). GitHub issues
immutable subjects, with numeric owner and repository IDs, by default for repositories created
since 2026-07-15, and the credential expected the name-based one. `deploy.sh` now reads both IDs
with `gh api` and the credential uses the immutable subject. The same run printed the three sign-in
values in its public log, so they are now repository secrets, and `publish-deploy` runs only once
`AZURE_DEPLOY_ENABLED` is `true` (research R7).

To recover, in this order:

1. Rerun `deploy.sh` from branch `002-azure-daily-run` and answer **y** to the secrets, the gate
   and the deletion of the old variables. This updates the federated credential's subject.
2. Open a pull request from `002-azure-daily-run` to `main` and merge it once its checks pass.
3. The merge's push to `main` runs `publish-deploy` with the new workflow.

Do not run `gh workflow run ci.yml --ref main` before the merge: it runs the `ci.yml` on `main`,
which still has the old job that reads repository variables.

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

*Result 2026-11-13: passed.* The bundle run printed `(True, 'TLSv1.3')`; the `/dev/null` run failed
with "could not read root certificate file". The R8 fallback is not needed. Part 1 (the first
sign-in through `maa-db-admins`, `bootstrap ok`) also passed.

If the first run fails with a certificate error, switch to the fallback in research R8 (ship
Microsoft's two roots as `/app/certs/azure-postgres-roots.pem`). The first scheduled or manual
execution of the job (§4, US1-1) then confirms the same path end to end with the job's own
identity.

After the first push, set the GHCR package `market-anomaly-agent` to **public** once (package
settings on GitHub). Without that step, the job cannot pull the image.

## 3. Platform behaviour checks (before relying on the schedule)

| # | Check | How | Pass condition |
|---|---|---|---|
| Q3 | Timeout scope (research R1) | Start one execution of a temporary copy of the job with `replicaTimeout 120`, `replicaRetryLimit 2`, command `sh -c 'sleep 50; exit 1'`, image `docker.io/library/busybox:1.37`, in `rg-maa-env`. Create the copy with `az containerapp job create --yaml` from a file outside the repository (see "Q3 job copy" below), not with `--command`/`--args`. Count the replicas in `az containerapp job execution show`. Delete the copy afterwards. | **3 replicas** ⇒ the timer is per attempt: set `replicaTimeout` to `1800`. **2 replicas** (the third cut off at 120 s) ⇒ the timer is per execution: keep `3600`. Record the result in research R1. *Result 2026-11-13: 2 replicas (started 11 s and 79 s after the execution start; the second cut off at 120 s; no third). Per execution: `3600` stays (research R1).* |
| Q4 | Failure alert fires | After the US3 redeploy (tasks.md T038), start one execution of `job-maa-detect` with an overridden argument that fails fast: `--date=2026-11-08`, a Sunday, so every attempt exits 2 before touching the database or the vendor. Use the commands under "Q4 failing start" below, not `--args "--date" …`. | Within 15 minutes an email arrives from `alert-maa-execution-failed`. Read the actual `Reason_s` and columns in `log-maa`, and pin them in the rule (research R9). *Result 2026-11-14: the `--args=` form worked; the execution failed after 3 attempts, each with the non-trading-day error. The first "Fired" email arrived about 8 minutes after the start (passed); a duplicate followed 5 minutes later from the overlapping window. Columns `JobName_s` and `ContainerJobName_s` confirmed; reasons `Error` per attempt and `BackoffLimitExceeded` per execution (`DeadlineExceeded` for the Q3 timeout). The rule now matches the execution-level reasons only, one row per execution, with actions muted for 1 hour (research R9). Retest 2026-11-16 after the redeploy: a deliberate failing start at 21:22 UTC sent exactly one email. The scheduled run on 2026-11-17 succeeded in about 13 minutes and `alert-maa-no-completed-run` sent no email.* |
| Q5 | Pipeline least privilege | First `push` to `main` after deployment | `publish-deploy` succeeds with the custom role only. If it fails with an authorization error, add exactly the missing action and record it in research R7. The likeliest one is `Microsoft.App/managedEnvironments/join/action`: updating a job can require it on the environment `cae-maa`. If so, grant it on the environment's scope only (a second, narrow custom role or an extra assignment), not by widening the role on the job. *Result 2026-11-13: passed with the custom role only; `managedEnvironments/join/action` was not needed (research R7).* |

**Q4 failing start.** `--args "--date" "2026-11-08"` would be rejected like Q3's `--args "-c" …`
("unrecognized arguments"). Writing the option with `=` passes the whole value as one argument, so
the CLI does not read it as an option of its own, and `detect` accepts `--date=2026-11-08`. The
override most likely replaces the container definition rather than merging into it, so the image,
container name, CPU and memory are given explicitly; the job's environment variables may be
missing in that attempt, which does not matter because the date check fails first. Check
`az containerapp job start --help` on the host before running it. If `--args=…` is rejected too,
use `--yaml` with a template file kept outside the repository, as in Q3.

```bash
image=$(az containerapp job show -n job-maa-detect -g rg-maa-env \
  --query "properties.template.containers[0].image" -o tsv)
az containerapp job start -n job-maa-detect -g rg-maa-env \
  --image "$image" --container-name detect --cpu 0.5 --memory 1.0Gi \
  --args=--date=2026-11-08
az containerapp job execution list -n job-maa-detect -g rg-maa-env -o table
```

Then, in `log-maa` (portal, Logs), read the failed attempts' rows and columns:
`ContainerAppSystemLogs_CL | where TimeGenerated > ago(1h) | take 50` and
`ContainerAppConsoleLogs_CL | where TimeGenerated > ago(1h) | take 50`.
Note the column holding the job name in each table and every `Reason_s` value of the failed
attempts and the execution. Do not copy execution names, IDs or the alert address into the
repository.

**Q3 job copy.** `az containerapp job create … --args "-c" "sleep 50; exit 1"` is rejected with
"unrecognized arguments": the CLI parses a value that starts with `-` as an option of its own. So
the copy is described in a YAML file and created with `--yaml`. The file holds the environment's
resource ID, so keep it **outside the repository** (for example in your home directory) and delete
it afterwards. Read the ID at run time with
`az containerapp env show -n cae-maa -g rg-maa-env --query id -o tsv` and put it in place of
`<environment-id>`:

```yaml
location: francecentral
properties:
  environmentId: <environment-id>
  configuration:
    triggerType: Manual
    replicaTimeout: 120
    replicaRetryLimit: 2
    manualTriggerConfig:
      parallelism: 1
      replicaCompletionCount: 1
  template:
    containers:
      - name: q3
        image: docker.io/library/busybox:1.37
        command: ["sh", "-c", "sleep 50; exit 1"]
        resources:
          cpu: 0.25
          memory: 0.5Gi
```

```bash
az containerapp job create -n job-maa-q3 -g rg-maa-env --yaml ~/maa-q3.yaml --output none
az containerapp job start -n job-maa-q3 -g rg-maa-env
# a few minutes later; count the replicas and their start times
az containerapp job execution list -n job-maa-q3 -g rg-maa-env -o table
az containerapp job execution show -n job-maa-q3 -g rg-maa-env --job-execution-name <execution-name>
az containerapp job delete -n job-maa-q3 -g rg-maa-env --yes
rm ~/maa-q3.yaml
```

## 4. Feature scenarios

| Spec | Scenario | How | Expected |
|---|---|---|---|
| US1-1, US1-2 | First execution fills an empty database | `az containerapp job start -n job-maa-detect -g rg-maa-env` | Execution `Succeeded` in ~13 min. Logs show `start mode=latest evaluated_date=<prev trading day> version=<sha>`, the applied migration `0001_init.sql`, and the last line `… status=completed …`. The owner query (contract §6) shows that day in `detection_runs`. |
| US2, SC-002 | Repeated start is free | Start again straight away | `Succeeded` in < 2 min. Log says "already stored". The Twelve Data dashboard shows 0 new credits. Row counts unchanged. *Result 2026-11-14 (image 3e756e8f): a manual start at 13:36 UTC succeeded in 39 s. The log shows `start mode=latest` for the previous trading day, `applied migration(s): none`, `<day> already stored; nothing fetched` and the stored summary, with no `fetched` lines. Twelve Data credits were the same before and after (0 new). The owner query (deployment-contract.md §6) after the start shows the `detection_runs` row count unchanged and its latest `computed_at` still from the 11:00 UTC scheduled run, so nothing was written.* |
| US2 / SC-003 | Holiday | The morning after a market holiday (or a unit test with an injected clock) | Resolves to the trading day before the holiday: already stored, exit 0, no exit 3 *Covered by the T030 unit test (Fri 2026-11-27 resolves to Wed 2026-11-25). Azure confirmation possible with the scheduled run on 2026-11-27, after Thanksgiving.* |
| US1-3, US1-5, SC-010 | Pipeline | Open a pull request: checks run, `publish-deploy` is skipped, no Azure login in the log. Merge: a new `:<sha>` tag appears and the job shows that image. | As described |
| US1-4 | Failing checks block delivery | Open a pull request with a deliberate `ruff` violation and confirm `checks` fails. Read `ci.yml` to confirm `publish-deploy` has `needs: checks` (so a failing push to `main` cannot reach it). Close the pull request unmerged. | `checks` red, `publish-deploy` not run, no new tag, job image unchanged |
| US3, SC-008 | Bounded retries | Temporarily set the vault secret to an invalid key: `vault=$(az keyvault list -g rg-maa-env --query "[0].name" -o tsv)`, then `az keyvault secret set --vault-name "$vault" --name twelve-data-api-key --value invalid-sc008 --output none`. The job picks up a new secret version only after a delay (Container Apps refreshes Key Vault references periodically, up to about 30 minutes), so wait before `az containerapp job start -n job-maa-detect -g rg-maa-env`, and check the log shows an authentication error, not a normal fetch. Restore the real key afterwards with `deploy.sh` (type it at the key prompt) and wait again before the next scheduled run. | ≤ 3 attempts, about 303 vendor requests at most, nothing stored, failure email |
| US4-2, FR-026 | Idempotent deploy | `deploy.sh` again, no changes | What-if shows no changes (except a rule left open) *Result 2026-11-18: no Create and no Delete. The 7 Modify entries are what-if noise, not drift: server-managed defaults shown as removals (PostgreSQL `dataEncryption`, `replica`, `replicationRole`, storage `iops`/`tier`/`type`; `cae-maa` `environmentMode`, `peerAuthentication`, `peerTrafficConfiguration`, `publicNetworkAccess`; `isolationScope` on both identities), `reference()` values what-if cannot evaluate (the job's `keyVaultUrl` and env values, a role assignment's `principalId`, `cae-maa`'s `customerId`), and `ssl_min_protocol_version` shown as system-default → user-override at the same value. The empty key prompt skipped the vault secret and the job image stayed the current one. Answered N; nothing applied. Passed.* |
| US4-3/4, SC-005 | Teardown and rebuild | Follow "Teardown and rebuild" below: `teardown.sh`, then at once `deploy.sh` (enter the key again) and `gh workflow run ci.yml --ref main`, then a manual start | Redeploy succeeds; no bootstrap needed (identity kept); the next execution says "already stored" for the last day; all earlier days are still present *Result 2026-11-18 (about 20:30–21:10 UTC): `teardown.sh` deleted `rg-maa-env` and purged the vault (the deleted-vault location lookup worked); `rg-maa-data` and its lock were untouched. The first `deploy.sh` failed on `alert-maa-no-completed-run` only (`BadRequest`, "The workspace could not be found"); a second run a few minutes later succeeded. `AZURE_CLIENT_ID` was set afterwards by hand (see the procedure below), then `gh workflow run ci.yml --ref main` passed, including `azure/login` with the new identity, and replaced the placeholder image. A manual start succeeded in 29 s; `detection_runs` held the same days with unchanged `computed_at`, so nothing was fetched or written. `log-maa` was not emptied: it came back with its earlier logs (procedure below). Confirmed 2026-11-19: that start's console output never reached `ContainerAppConsoleLogs_CL`, even hours later, but its system logs did: the real image was pulled, and the container ran for 2 s and exited 0. With `detection_runs` unchanged, that proves the "already stored" path. The next scheduled execution logged normally. Passed. After a rebuild, confirm the already-stored start through its system logs (container runtime and exit code) and `detection_runs`, because the console output of a very short first execution can be missing.* |
| US4-5, FR-030 | Lock | `az group delete -n rg-maa-data` | Refused by the lock *Result 2026-11-18: refused with `ScopeLocked`. Passed.* |
| SC-007 | No secret leaks | Search for the first 6 characters of the key, held in a shell variable and never printed: in `podman image inspect <image>`, in `podman run --rm --entrypoint sh <image> -c 'env; ls -la /app'`, in the logs of the last 10 Actions runs, and in `log-maa` (Logs, `search` operator). Check the server's `authConfig` (`activeDirectoryAuth` and `passwordAuth`). Try a `psql` login as `maa-db-admins` with a plain password. | 0 hits everywhere. `activeDirectoryAuth` Enabled, `passwordAuth` Disabled. The password login is refused; the server reads the password field only as an Entra token (`FATAL: The access token has invalid format …`). *Result 2026-11-18: all as expected. Passed.* |
| SC-007 | Branch-restricted OIDC | A `workflow_dispatch` on another branch cannot test this: `publish-deploy` is gated on `refs/heads/main` and is skipped, so `azure/login` never runs. Instead, read the federated credential's subject on `id-maa-github` (it must end with `:ref:refs/heads/main`), and rely on the mismatch already observed live: any other subject is refused. | The subject ends with `:ref:refs/heads/main`. A subject that does not match is refused with `AADSTS700213` ("No matching federated identity record found for presented assertion subject"), as observed on 2026-11-13 (research R7). *Result 2026-11-18: passed by this evidence.* |
| US5, FR-028 | Budget | `az consumption budget show --budget-name budget-maa-monthly` | EUR 30; notifications at 50/80/100% (EUR 15 / 24 / 30) on actual cost *Result 2026-11-18: the `deploy.sh` what-if showed `budget-maa-monthly` as the only Create (amount 30, Monthly, start 2026-11-01, actual-cost notifications at 50/80/100) plus the known noise (US4-2); API version 2024-08-01 was accepted. After the deployment, `az consumption budget show` returned amount 30.0, Monthly, start 2026-11-01 and thresholds 50/80/100. Passed.* |
| SC-001 | Five days unattended | Leave it running for a week | 5 trading days stored, no manual action *Result 2026-11-24: the scheduled executions on Tuesday 2026-11-17 through Saturday 2026-11-21 each logged `status=completed` at about 11:13 UTC, for the previous trading day (2026-11-16 to 2026-11-20). The teardown and rebuild on the evening of 2026-11-18 did not interrupt the schedule. Passed.* |

**Teardown and rebuild.** `teardown.sh` deletes `rg-maa-env` (Log Analytics workspace,
environment, job, vault, pipeline identity, action group and alerts) and purges the vault;
`rg-maa-data` (server, job identity, lock) is never touched.

*The logs survive a rebuild* (*corrected 2026-11-18*, T045). Deleting `log-maa` only soft-deletes
it. `deploy.sh` recreates it with the same name, resource group and region within the soft-delete
period, which recovers the old workspace with its data: after the rebuild, all logs since
2026-11-13 could still be queried. The 30-day retention still applies. What does start empty is the
job's execution list, because `job-maa-detect` is a new resource.

*When it is safe.* The job runs at **11:00 UTC, Tuesday to Saturday**, its retries can last
until about 12:00, and `alert-maa-no-completed-run` checks between 12:45 and 13:45 UTC that
the day's run completed. A teardown in that span can kill a running execution, so the day has no
`status=completed` line and the rebuilt check sends a "no completed run" email; a rebuild that is
still in progress at 12:45 can do the same. The guard does not rely on `log-maa` being recovered
with its data (see above). So:
- **Safe:** any time on Sunday or Monday (UTC); on Tuesday to Saturday, from 14:00 UTC until
  10:30 UTC the next morning.
- **Finish before 10:30 UTC** on a scheduled day: the rebuild is complete only once
  `gh workflow run` has replaced the placeholder image. Otherwise the 11:00 run starts the
  placeholder, which completes without storing anything, and the 12:45 check alerts.
- `teardown.sh` refuses to start between 10:30 and 14:00 UTC on Tuesday to Saturday unless
  `MAA_TEARDOWN_ANYTIME=1` is set.
- Don't merge to `main` between teardown and rebuild: `publish-deploy` would fail, because the
  job and the pipeline identity don't exist.

*The vendor key must be entered again.* The vault is purged, so the stored Twelve Data key is
gone for good. `deploy.sh` refuses an empty key prompt when no secret exists; have the key at
hand (for example from `.env`) before starting.

```bash
infra/scripts/teardown.sh            # type rg-maa-env to confirm; waits for the delete and purge
infra/scripts/deploy.sh              # enter the Twelve Data key; answer y for AZURE_CLIENT_ID
gh workflow run ci.yml --ref main    # publish-deploy puts the current image on the new job
az containerapp job start -n job-maa-detect -g rg-maa-env
```

- `deploy.sh` creates a new `id-maa-github` with a new client ID, so answer **y** when it offers
  to set the repository secret `AZURE_CLIENT_ID` (the tenant and subscription secrets are
  unchanged; answering y for them is harmless). `AZURE_DEPLOY_ENABLED` stays set.
- If you answered n by mistake, set the secret afterwards from the repository directory, without
  printing the value:
  `az identity show -n id-maa-github -g rg-maa-env --query clientId -o tsv | gh secret set AZURE_CLIENT_ID`.
  Until it is set, `azure/login` in `publish-deploy` signs in with the old, deleted identity.
- *`deploy.sh` can fail once on an alert rule* (*added 2026-11-18*, T045). Right after the
  workspace is created, `alert-maa-no-completed-run` can fail with `BadRequest` "The workspace
  could not be found", while every other resource is created. Wait a few minutes and run
  `deploy.sh` again; the second run succeeds. Leaving the key prompt empty then is fine, because
  the first run already stored the secret.
- The new action group sends its confirmation email again.
- No database bootstrap: the server and `id-maa-job` are unchanged.
- Check before the manual start that the job shows the `:<sha>` image, not the placeholder
  (`az containerapp job show -n job-maa-detect -g rg-maa-env --query "properties.template.containers[0].image" -o tsv`).
  The start should then log `<day> already stored; nothing fetched` for the last stored day.
  That console line can be missing for this first, very short execution (*observed 2026-11-19*,
  T045): confirm it instead through its system logs (`ContainerAppSystemLogs_CL`: the container
  runtime and exit code 0) and an unchanged `detection_runs`.

## 5. Decommission and restore (only when pausing or ending the project)

```bash
infra/scripts/decommission.sh ~/private/maa-$(date +%F).dump   # path must be outside the repo
# … later …
infra/scripts/deploy.sh && infra/scripts/db-access.sh open && infra/scripts/db-bootstrap.sh
infra/scripts/restore.sh ~/private/maa-<date>.dump              # closes owner-access itself
```

`decommission.sh` runs `teardown.sh` first, so the same time window applies (it refuses between
10:30 and 14:00 UTC, Tuesday to Saturday). It prints the `detection_runs` count before the export;
note it for the comparison below. As with a teardown, the vault is purged, so `deploy.sh` asks
for the Twelve Data key again on the way back. Run the bootstrap and `restore.sh` in one sitting,
outside the scheduled window: `restore.sh` refuses once the job has created the tables, and until
the bootstrap has run, a scheduled execution fails and alerts instead of writing anything.

Expected (SC-009): `SELECT count(*) FROM detection_runs` matches the count taken before
decommission, and the next execution reports the last stored day as "already stored".

## 6. Local workflow still works

```bash
podman compose up -d db
uv run detect --date 2026-11-10 --migrate     # password DATABASE_URL from .env, as in 001
uv run detect --latest                         # new mode, locally
uv run pytest && uv run pytest -m integration
```

*Note (2026-11-18, T053):* a `--date` run on a day that is already stored locally exits 3 if the
vendor has adjusted that day's bars since the first run (for example after a dividend or a split).
That is the designed FR-018 rejection, and nothing is written. To see exit 0, use a date that is
not yet stored in the local database.
