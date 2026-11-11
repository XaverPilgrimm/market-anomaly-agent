# Feature Specification: Unattended Daily Run on Azure

**Feature Branch**: `002-azure-daily-run`

**Created**: 2026-11-11

**Status**: Draft

**Input**: User description: "Feature: unattended daily run on Azure. Run the existing detect CLI from feature 001 once per US trading day on Azure without manual steps, storing results in a managed PostgreSQL. Requirements: a container image of the app, built by GitHub Actions on every push to main and pushed to Azure Container Registry, with GitHub authenticating to Azure through OpenID Connect so no Azure secret is stored in GitHub; an Azure Container Apps Job on a UTC cron schedule (Tuesday to Saturday, about 11:00 UTC, between the US close and the next open in every season); a new CLI mode that resolves the most recent completed US trading day in New York time by itself, and exits 0 without fetching anything when that day is already stored, so holidays and repeated starts cost no vendor credits and cannot trigger a false exit 3; Azure Database for PostgreSQL Flexible Server (Burstable B1ms, PostgreSQL 17) in an EU region, reached over TLS, with migrations applied before detection; the Twelve Data key and the database password in Azure Key Vault, read through a managed identity, never in the image or the repository; at most 2 retries per failed run (each attempt uses about 101 of the 800 daily vendor credits) and a job timeout of 30 minutes for the roughly 13-minute run; logs in Log Analytics and an alert when a run fails; all Azure resources defined as code with Bicep in the repository, so the environment can be torn down and recreated; the constitution's spend caps enforced with Azure budget alerts. Out of scope: the agent, X, the website and Azure OpenAI. Success: a scheduled run stores a trading day in Azure PostgreSQL without manual steps, a repeated start does nothing, and the whole environment can be deleted and recreated from the repository."

**Depends on**: [001-nasdaq100-anomaly-detection](../001-nasdaq100-anomaly-detection/spec.md)
(the `detect` CLI, its exit codes and its storage). This feature adds no new detection rules and
no new stored entities.

## Clarifications

### Session 2026-11-11

- Q: Should tearing down the environment delete the stored detection results, keep the database
  out of the teardown, or export a backup first? → A: Keep the database out of the normal
  teardown. It lives in its own resource group with a CanNotDelete lock, so rebuilding everything
  else never touches the results. Pausing or ending the project is a separate, documented step:
  export with `pg_dump` to a private local file outside the repository, then remove the lock and
  delete the database. A later deployment can restore from that file.
- Q: Should a successful push to `main` also switch the scheduled job to the new image? → A: Yes.
  After the checks pass, the pipeline updates the job to the image tagged with that commit. The
  pipeline identity may push to the registry and update only this job (least privilege). Every
  image carries the commit SHA it was built from, and each run logs it at start.
- Q: How is the database reachable over the network? → A: Public endpoint with TLS and
  certificate verification. The firewall allows Azure services, plus the owner's own IP only
  while the owner needs it. A rule for the job's outbound address is not used: consumption
  Container Apps outbound IPs can change, and a static one needs a NAT gateway that costs more
  than the monthly cap. Authentication is Microsoft Entra only, with password authentication
  disabled: the job connects with its managed identity, the owner with their own Entra account.
  No database password exists, so Key Vault holds only the Twelve Data key. Local development
  keeps the password-based `DATABASE_URL` against the local PostgreSQL.

## User Scenarios & Testing *(mandatory)*

The single user is the repository owner, who operates the project alone and is not watching when
it runs.

### User Story 1 - The day's detection runs and is stored without anyone starting it (Priority: P1)

Every US trading day's detection result appears in the cloud database by the next morning, before
the US market opens, without the owner doing anything. The run uses the same `detect` program,
rules and exit codes as feature 001; only where and when it runs changes.

**Why this priority**: This is the feature. Every later step (evidence, agent, signals) needs
flagged cases to be there every morning, and the constitution requires every decision to be
stored before its outcome is known, which a forgotten manual run would break.

**Independent Test**: Deploy the environment, wait for (or manually trigger) the scheduled
execution on a morning after a trading day that is not yet stored, and confirm that the cloud
database now holds that day's run with a completed summary, and that the execution ended
successfully.

**Acceptance Scenarios**:

1. **Given** a deployed environment and a cloud database that does not yet hold the most recent
   completed trading day, **When** the scheduled execution starts, **Then** the database schema is
   brought up to date, that day is evaluated and stored exactly as feature 001 stores it, and the
   execution ends successfully.
2. **Given** a fresh, empty cloud database, **When** the first execution runs, **Then** the schema
   is created and the frozen universe is loaded from the file inside the image before detection
   starts (feature 001, FR-003).
3. **Given** a push to `main` whose checks pass, **When** the build pipeline finishes, **Then** a
   new image tagged with that commit is in the registry, the scheduled job is set to that image
   so the next execution runs it and logs that commit, and no Azure credential was stored in
   GitHub to get it there.
4. **Given** a push to `main` whose checks fail, including the integration tests against
   PostgreSQL 17, **When** the pipeline finishes, **Then** no new image is published and the
   scheduled job keeps running the previous image.
5. **Given** a pull request to `main`, **When** the pipeline runs, **Then** the same checks run,
   including the integration tests against a PostgreSQL 17 service container, their result is
   shown on the pull request, and no image is published, no job is updated and no Azure sign-in
   is attempted.

---

### User Story 2 - Repeated starts and non-trading days cost nothing (Priority: P1)

The program works out which day to evaluate by itself. If that day is already stored, it ends
successfully at once, without asking the price vendor for anything. A start after a holiday, a
second start on the same morning, or a manual start "just to be sure" is harmless: it uses no
vendor credits and can never be rejected because the vendor has since adjusted prices.

**Why this priority**: Without it, the five-day schedule would re-fetch an already stored day
after every market holiday. That spends a day's credits for nothing, and because adjusted prices
are restated after dividends and splits, it would sometimes end with a false "data changed"
rejection (feature 001, exit 3) and a false alert.

**Independent Test**: Locally, with a database that already holds the most recent completed
trading day, run the new mode and confirm that it exits 0, prints the stored summary, makes no
vendor request (the fake source records no call) and writes nothing. Then remove that day and
confirm that the same command evaluates and stores it.

**Acceptance Scenarios**:

1. **Given** the most recent completed trading day is already stored, **When** the new mode runs,
   **Then** it exits 0, logs that the day is already stored, prints that day's stored summary as
   its last stdout line, makes no vendor request and writes nothing.
2. **Given** the most recent completed trading day is not stored, **When** the new mode runs,
   **Then** it behaves exactly like `detect --date <that day>`, with the same output and exit
   codes.
3. **Given** Monday was a market holiday and Friday is stored, **When** the Tuesday-morning
   execution runs, **Then** it resolves Friday, finds it stored and exits 0 without a vendor
   request.
4. **Given** the vendor has restated adjusted prices for a stored day, **When** the new mode runs
   on any later morning, **Then** it never re-fetches that day, so it cannot end with exit 3.
5. **Given** the explicit `--date` mode, **When** it is used, **Then** it behaves exactly as in
   feature 001, including exit 3 on changed data.

---

### User Story 3 - A failed run is retried a bounded number of times and reported (Priority: P2)

When a run fails (a vendor outage, a database restart, a timeout), the platform tries again a
limited number of times. If every attempt fails, the owner is told without having to look.
Nothing half-finished is ever stored.

**Why this priority**: An unattended job that fails silently is worse than no job, because the
owner believes the data is there. Bounding the retries keeps a broken day from eating the vendor's
daily allowance.

**Independent Test**: Deploy with a deliberately invalid vendor key (or a stopped database),
trigger an execution, and confirm that it is attempted at most 3 times in total, stores nothing,
ends as failed, and that the owner receives a failure notification.

**Acceptance Scenarios**:

1. **Given** an attempt exits non-zero, **When** the platform handles it, **Then** it starts at
   most 2 further attempts for the same execution, and stops early as soon as one succeeds.
2. **Given** an attempt hangs, **When** the job's time limit is reached, **Then** the attempt is
   stopped, nothing from it is stored, it counts as a failed attempt, and the execution is
   reported as failed if no attempt remains or the limit covers the whole execution.
3. **Given** the first two attempts each fail after running for about 13 minutes, **When** the
   third attempt starts, **Then** the time limit does not stop it before it can complete, and if
   it succeeds the day is stored and the execution ends successfully.
4. **Given** every attempt of an execution failed, **When** the execution ends, **Then** the owner
   receives a notification naming the job and the failure, and the run's logs, which contain no
   secrets, can be read in the central log store.
5. **Given** the container cannot start at all (for example, the image cannot be pulled), **When**
   the execution ends, **Then** this is reported the same way as a failed run.

---

### User Story 4 - The whole environment can be deleted and recreated from the repository (Priority: P2)

Every cloud resource the daily run needs is described in files in the repository. The owner can
delete the environment and build it again with one documented procedure, supplying only the
values that must never be in a public repository. The database that holds the stored results is
deliberately kept out of this cycle: it lives apart and is protected against deletion, so a
rebuild never touches the decision history.

**Why this priority**: A one-person project cannot afford an environment that only exists as
clicks in a portal. Being able to rebuild it is also what makes it safe to tear down when it is
not needed.

**Independent Test**: In an empty resource group (or after a teardown), follow the documented
procedure, supplying only the vendor key and the alert address, then trigger an execution and
confirm that a trading day is stored.

**Acceptance Scenarios**:

1. **Given** an empty target, **When** the owner runs the documented deployment, **Then** every
   resource, identity, permission, secret slot, schedule, alert and budget exists without any
   portal step.
2. **Given** a deployed environment, **When** the deployment is run again unchanged, **Then**
   nothing changes and no data is lost.
3. **Given** a deployed environment, **When** the owner runs the documented teardown and then
   the deployment again straight away, **Then** the deployment succeeds even though some services
   keep deleted resources for a while (for example, a soft-deleted secret store).
4. **Given** the teardown is run, **When** it finishes, **Then** the database server and every
   stored detection result still exist, untouched, and the next deployment connects the new job
   to them.
5. **Given** the database is protected against deletion, **When** anyone tries to delete it
   without first removing that protection, **Then** the deletion is refused.
6. **Given** the owner pauses or ends the project, **When** they follow the documented
   decommission procedure, **Then** a full export of the database is written to a private local
   file outside the repository before the protection is removed and the database deleted, and a
   later deployment can restore every stored day from that file.

---

### User Story 5 - Spending stays within the constitution's caps and is visible (Priority: P3)

The owner is warned before the Azure bill passes the constitution's monthly cap, and the chosen
resources are expected to stay well under it.

**Why this priority**: The resources are small, so overspending is unlikely, but the constitution
requires the caps (Principle V), and an alert that only arrives with the invoice is too late.

**Independent Test**: After deployment, confirm that a monthly budget of USD 30 exists for the
project's Azure spending, with notifications at 50%, 80% and 100% addressed to the owner, and that
the documented cost estimate of the deployed resources is below USD 30 per month.

**Acceptance Scenarios**:

1. **Given** a deployed environment, **When** the project's actual Azure spending in a month
   reaches 50%, 80% or 100% of USD 30, **Then** the owner is notified at each threshold.
2. **Given** the documentation, **When** the owner reads it, **Then** it states that Azure budgets
   only notify and do not stop spending.

---

### Edge Cases

- **Monday holiday**: the Tuesday execution resolves the previous Friday, finds it stored and
  exits 0 without a vendor request.
- **Friday holiday** (e.g. Good Friday): the Saturday execution resolves Thursday, finds it stored
  and exits 0.
- **Early-close days** (e.g. the day after Thanksgiving) are trading days, are resolved like any
  other day, and are stored by the next morning's execution.
- **Daylight-saving changes**: the schedule is fixed in UTC. 11:00 UTC is 06:00 New York time in
  winter and 07:00 in summer, so it is always after the previous close (21:00 or 20:00 UTC) and
  before the next open (14:30 or 13:30 UTC).
- **A day fails every attempt**: it stays unstored and the owner is notified. The next morning's
  execution evaluates only the newer day; the missed day is not caught up automatically and has
  to be run by hand with `--date` (feature 001: catching up is outside detection).
- **Vendor data not yet available, or too much missing**: the run exits 4 (feature 001, FR-022
  and FR-023) and stores nothing. The retries start straight away and will probably fail the same
  way, so the owner is notified and runs the day later with `--date`.
- **Overlapping executions** (a manual start while the scheduled one is running): at most one of
  them stores the day. The other ends without storing anything; if it fails, it is reported like
  any failure. There is never a second stored result for a day.
- **Shared vendor allowance**: the 800 daily credits belong to the key, not to the environment.
  Local runs with the same key on the same day count against the same allowance, so a cloud run
  can then fail with exit 1 when the allowance runs out.
- **Universe file changed in a newer image**: the run fails with exit 1 before any vendor request
  (feature 001, FR-003) and is reported like any failure.
- **Database unavailable, or the job's identity cannot get a token or has lost its database
  access**: the attempt exits 1, is retried and, if it keeps failing, is reported.
- **Owner's IP firewall rule left in place or forgotten**: it grants network reach only. Without
  an Entra account that has database access, a connection is still refused.
- **Push to `main` that breaks the build**: no image is published; the job keeps running the last
  good image.

## Requirements *(mandatory)*

### Functional Requirements

**Resolving the day (new CLI mode)**

- **FR-001**: The `detect` command MUST offer a second mode that takes no date and resolves the
  evaluated day itself: the most recent NASDAQ trading day strictly before the current calendar
  date in New York (America/New_York). This amends feature 001's FR-021 and its CLI contract
  ("no other mode of operation exists"). Each run still evaluates exactly one day, and the
  explicit `--date` mode stays unchanged.
- **FR-002**: In that mode, when the resolved day is already stored, the command MUST exit 0
  without any vendor request and without writing anything. It MUST log at INFO that the day is
  already stored, and print that day's stored summary as the last stdout line (the feature 001
  stdout contract).
- **FR-003**: In that mode, when the resolved day is not stored, the command MUST behave exactly
  like `detect --date <resolved day>`: same validation, output, storage and exit codes.
- **FR-004**: Every run MUST log at its start which mode it runs in, the resolved or given
  evaluated day, and the version of the application (the commit it was built from).

**Packaging and delivery**

- **FR-005**: The application MUST be packaged as one container image that contains the
  application, its locked dependencies and the committed universe file. The image MUST NOT
  contain any secret, environment file or credential. It MUST record the commit SHA it was built
  from, so that a run can log it (FR-004).
- **FR-006**: Every unattended run MUST apply pending database migrations before detection
  starts. Migrations that are already applied MUST be a no-op (feature 001's migration runner).
- **FR-007**: On every pull request to `main` and on every push to `main`, the pipeline MUST run
  the project's checks: lint, format check, type check, the unit test suite, and the integration
  test suite against a PostgreSQL 17 service container started by the pipeline. Only a push to
  `main` MUST build and publish the image to the project's container registry and deploy it
  (FR-009), and only when every check passes. A pull request MUST NOT publish, deploy or sign in
  to Azure. Each image MUST be tagged with the commit it was built from.
- **FR-008**: The pipeline MUST authenticate to Azure through OpenID Connect federation. No Azure
  password, client secret or certificate may be stored in GitHub. The federated credential MUST
  be restricted to the `main` branch of this repository, so that no pull request, other branch
  or tag can obtain an Azure token. The pipeline's identity MUST have only the permissions it
  needs.
- **FR-009**: After the checks pass and the image is published, the pipeline MUST update the
  scheduled job to the image tagged with that commit, so the next execution runs it. When any
  check, the build or the publish fails, the job MUST keep its current image. The pipeline's
  identity MUST be allowed only to push to the project's registry and to update this one job.

**Schedule and execution**

- **FR-010**: The daily run MUST be an Azure Container Apps Job on a cron schedule in UTC:
  Tuesday to Saturday at 11:00 UTC, running the new mode (FR-001).
- **FR-011**: Each execution MUST run one container at a time. A failed attempt MUST be retried
  at most 2 times (at most 3 attempts per execution). The job's time limit MUST be long enough
  for all 3 attempts of about 13 minutes each to complete, and MUST still stop a hung attempt.
  Microsoft's documentation for Container Apps jobs says the replica timeout takes precedence if
  it expires before all retries have occurred, so the limit may apply to the whole execution
  rather than to each attempt. If it applies per execution, it is about 60 minutes. If it applies
  per attempt, it is about 30 minutes. The plan MUST confirm which applies and set the value
  accordingly. Either way, an execution started at 11:00 UTC ends by about 12:00 UTC, before the
  earliest US open (13:30 UTC).
- **FR-012**: A failed, stopped or timed-out attempt MUST leave nothing stored for the day. This
  already holds through feature 001's single-transaction storage and MUST be preserved.
- **FR-013**: The owner MUST be able to start an execution by hand, outside the schedule, with
  the same configuration.
- **FR-014**: Vendor use MUST stay within the free plan. Each attempt makes about 101 requests
  (100 stocks plus the benchmark), so an execution makes at most about 303 of the 800 daily
  requests, even when all 3 attempts run.

**Database**

- **FR-015**: Results MUST be stored in Azure Database for PostgreSQL Flexible Server (Burstable
  B1ms, PostgreSQL 17), in the same EU region as every other resource of this feature.
- **FR-016**: Every connection to the database MUST be encrypted with TLS, and the client MUST
  verify the server's certificate. The server MUST refuse unencrypted connections.
- **FR-017**: The database MUST use its public endpoint. Its firewall MUST allow connections from
  Azure services, so the job can reach it from changing outbound addresses, and MUST allow the
  owner's own IP address only while the owner needs it. Adding and removing that rule MUST be a
  documented step. No private endpoint, NAT gateway or other paid network component is used
  (FR-029).
- **FR-017a**: The database MUST accept only Microsoft Entra authentication. Password
  authentication MUST be disabled. The job MUST connect as its managed identity, using a
  short-lived token. The owner MUST connect with their own Entra account, which is the server's
  Entra administrator. The job's identity MUST have only the database privileges needed to apply
  migrations and read and write the project's tables.

**Secrets and identities**

- **FR-018**: The vendor API key MUST be stored only in Azure Key Vault. It is the only secret of
  this feature. The job MUST read it at run time through its managed identity. It MUST NOT appear
  in the image, the repository, the pipeline's configuration or logs, deployment parameter files
  or any log.
- **FR-019**: The job's managed identity MUST be able to read only the vendor key secret, pull from
  the project's registry and sign in to the database (FR-017a). It needs no other permission.
- **FR-020**: There MUST be no database password in Azure. In Azure, the application MUST connect
  with the managed identity's token (FR-017a). Locally, it MUST keep connecting with the
  password-based `DATABASE_URL` against the local PostgreSQL, unchanged from feature 001.

**Observability**

- **FR-021**: All output of every attempt (stdout and stderr) and the platform's execution events
  MUST be collected in a Log Analytics workspace and kept for at least 30 days.
- **FR-022**: The owner MUST be notified when an execution ends as failed, after its retries.
  That includes a non-zero exit, a timeout, and a container that could not be started. The
  notification address MUST be supplied at deployment and MUST NOT be stored in the repository,
  which is public.
- **FR-023**: Secrets MUST NOT appear in any collected log. The application's existing redaction
  (feature 001) MUST cover the new mode.

**Infrastructure as code**

- **FR-024**: Every Azure resource, identity, role assignment, federated credential, schedule,
  alert and budget of this feature MUST be defined in Bicep files in the repository. Nothing may
  be created by hand in the portal, apart from the subscription itself and the permissions of the
  person who deploys.
- **FR-025**: The repository MUST document one deployment procedure that builds the environment
  from an empty target. The owner's only inputs are the vendor key, the notification address and
  non-secret identifiers (subscription, region, GitHub repository, the owner's Entra account).
- **FR-026**: Running the deployment again against an existing environment MUST converge to the
  same state without losing stored data. It MUST reuse an existing database rather than replace
  it.
- **FR-027**: The repository MUST document a teardown procedure that removes the environment, so
  that the deployment can run again right afterwards. That includes services that keep deleted
  resources for a retention period, such as Key Vault soft delete and globally unique resource
  names. The teardown MUST NOT touch the database (FR-030).

**Stored results**

- **FR-030**: The database server MUST live in its own resource group, separate from the rest of
  the environment, and that group MUST carry a CanNotDelete lock defined in the Bicep files. The
  lock MUST be in place after every deployment.
- **FR-031**: The repository MUST document a separate decommission procedure for pausing or
  ending the project: export the whole database with `pg_dump` to a private local file outside
  the repository, verify that the file exists and is not empty, then remove the lock and delete
  the database resource group. The export file MUST NOT be committed or uploaded anywhere.
- **FR-032**: The repository MUST document how a later deployment restores the database from such
  an export before the first scheduled execution, so that every previously stored day is kept and
  the new mode (FR-002) does not evaluate those days again.

**Cost**

- **FR-028**: A monthly Azure budget of USD 30 MUST cover all of the project's Azure spending, with
  notifications to the owner at 50%, 80% and 100% of actual spending (constitution Principle V).
  The documentation MUST state that Azure budgets only notify and do not stop spending.
- **FR-029**: The chosen resources MUST have a documented expected monthly cost below USD 30. The
  vendor cost stays USD 0 (free plan).

### Key Entities

- **Scheduled execution**: one start of the daily job, by the schedule or by hand. It has a start
  time, up to 3 attempts, a final status (succeeded or failed), and the evaluated day it resolved
  to. Its record lives in the platform's logs, not in the detection database.
- **Environment**: the set of cloud resources for the daily run (registry, job, secret store, log
  workspace, alerts, budget, identities). It is defined entirely in the repository and can be
  deleted and recreated.
- **Database**: the PostgreSQL server holding the stored results. It is defined in the
  repository too, but lives in its own locked resource group and survives every teardown of the
  environment. It is deleted only by the decommission procedure, after an export.
- **Database export**: a `pg_dump` file written to a private local location outside the
  repository during decommission, and the only input of a restore.
- **Deployment inputs**: the values supplied by the owner at deployment and never committed: the
  vendor key and the notification address. Non-secret identifiers, the owner's Entra account
  among them, can be kept as repository or pipeline variables.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For 5 consecutive US trading days after deployment, each day is stored in the cloud
  database before the next market open, with no manual action.
- **SC-002**: A start on a morning whose resolved day is already stored ends successfully in under
  2 minutes, with 0 vendor requests and 0 new stored rows.
- **SC-003**: Across a week that contains a market holiday, the number of evaluated days stored
  equals the number of trading days, and no execution ends with exit 3.
- **SC-004**: When an execution fails, the owner is notified within 15 minutes of its final
  attempt ending.
- **SC-005**: Starting from nothing (or right after a teardown), the owner can recreate the
  environment within 1 hour, supplying only the vendor key and the notification address, and the
  next execution stores a trading day. Every day stored before the teardown is still stored
  afterwards.
- **SC-006**: The documented expected Azure cost is below USD 30 per month, and the vendor cost is
  USD 0.
- **SC-007**: A search of the image, the repository, the pipeline's logs and the collected run logs
  does not find the vendor key, and an attempt to connect to the cloud database with a password
  is refused. A token request from a workflow run on any ref other than `main` is refused.
- **SC-008**: No single day uses more than about 303 vendor requests (under 40% of the daily
  allowance), even when every attempt fails.
- **SC-009**: After the decommission procedure and a restore into a new deployment, the database
  holds the same set of stored days as before, and the next execution does not evaluate any of
  them again.
- **SC-010**: Every pull request to `main` and every push to `main` shows a pass or fail result
  for all checks, the integration tests against PostgreSQL 17 among them. In the registry and the
  job's history, 0 images or deployments come from a pull request or from a commit whose checks
  failed.
- **SC-011**: When an execution's first two attempts each fail after running for a typical length
  (about 13 minutes), the third attempt still runs to completion, and the execution ends before
  12:30 UTC.

## Assumptions

- The owner has an Azure subscription used for this project, with the rights to create resources,
  managed identities and role assignments. The USD 30 budget therefore covers the project's whole
  Azure spending, including Azure OpenAI once a later feature adds it.
- The region is one EU region where every service of this feature is available, including
  Burstable B1ms with PostgreSQL 17. It is chosen in the plan.
- "Most recent completed trading day" means the latest NASDAQ trading day strictly before today's
  date in New York. A manual start in the evening of a trading day therefore evaluates the
  previous trading day, so a day is never stored from bars that may still be provisional, and
  stored days are immutable (feature 001, FR-018).
- The platform retries any failed attempt, whatever its exit code, and immediately. A retry after
  exit 3 or exit 4 will usually fail again. Its cost is bounded by FR-014, and the failure
  notification follows.
- Logs are kept for 30 days. The database keeps the service's default automated backups.
- The storage schema, detection rules and exit codes of feature 001 are unchanged. Feature 001 is
  merged into `main` before this feature's pipeline publishes an image.
- The local workflow (`podman compose` and `detect --date`) keeps working unchanged (constitution
  Technology Constraints). The new mode also runs locally, with the password-based
  `DATABASE_URL`.
- The owner has a Microsoft Entra account in the subscription's tenant that can be made the
  database's Entra administrator.
- Non-secret identifiers (tenant, subscription, client and resource IDs) are not credentials. They
  may be kept as GitHub repository variables.
- Out of scope: the agent, X, the website, Azure OpenAI, automatic catch-up of missed days, more
  than one environment (no staging), and detecting a scheduler that never starts an execution at
  all.
