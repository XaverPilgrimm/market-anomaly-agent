#!/usr/bin/env bash
# Creates the job's PostgreSQL role, the database and the grants, signed in as the Entra
# administrator group maa-db-admins with the owner's own token (research R3, R16). Idempotent.
#
# Needs network access to the server: run 'infra/scripts/db-access.sh open' first and
# 'infra/scripts/db-access.sh close' afterwards. This is also quickstart check Q7, part 1: the
# first sign-in through the group.

# shellcheck source=infra/scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

ADMIN_ROLE=maa-db-admins
JOB_ROLE=id-maa-job
SQL="$(repo_root)/infra/sql/bootstrap.sql"

require_az_login

fqdn="$(server_fqdn)"
if [[ -z $fqdn ]]; then
    echo "no PostgreSQL server found in rg-maa-data" >&2
    exit 1
fi
export_pg_token

run_phase() {
    local dbname=$1
    shift
    pg_client psql \
        "host=$fqdn dbname=$dbname user=$ADMIN_ROLE sslmode=verify-full sslrootcert=$PG_CA_BUNDLE" \
        -v ON_ERROR_STOP=1 -v job_role="$JOB_ROLE" -v admin_role="$ADMIN_ROLE" "$@" \
        -f - <"$SQL"
}

# Capture only stderr (to recognise an authentication failure); stdout is discarded on purpose.
# shellcheck disable=SC2069
if ! err="$(run_phase postgres -v phase_db=postgres 2>&1 >/dev/null)"; then
    printf '%s\n' "$err" >&2
    if grep -qiE 'password authentication failed|authentication failed|invalid.*token|token.*invalid' <<<"$err"; then
        cat >&2 <<'EOF'

The server did not accept your account through the group maa-db-admins (quickstart check Q7,
part 1). Fallback (research R16): create a cloud-only work account in this tenant, add it to
maa-db-admins, remove the personal account, sign in to az with the work account, then rerun
infra/scripts/entra-bootstrap.sh and this script. The server itself does not change.
EOF
    fi
    exit 1
fi

run_phase market_anomaly_agent

unset PGPASSWORD
echo "bootstrap ok"
echo "remember: infra/scripts/db-access.sh close"
