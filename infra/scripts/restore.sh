#!/usr/bin/env bash
# Restores a decommission export into a freshly deployed and bootstrapped server (FR-032,
# contracts/deployment-contract.md §6, quickstart.md §5).
#
#   infra/scripts/deploy.sh
#   infra/scripts/db-access.sh open && infra/scripts/db-bootstrap.sh
#   infra/scripts/restore.sh ~/private/maa-<date>.dump
#
# Run it in the same sitting as the bootstrap, before the next scheduled execution: once the job
# has run --migrate, the tables exist and this script refuses (it never overwrites or merges).
# The objects are created as id-maa-job (--no-owner --role), so the job owns them as after its own
# migrations; schema_migrations comes back with them, so the next --migrate applies nothing.

# shellcheck source=infra/scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SCRIPTS="$(dirname "${BASH_SOURCE[0]}")"
JOB_ROLE=id-maa-job

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <dump-path>" >&2
    exit 2
fi
dump="$(realpath -e -- "$1" 2>/dev/null || true)"
if [[ -z $dump || ! -f $dump || ! -s $dump ]]; then
    echo "refusing: $1 is not an existing, non-empty file" >&2
    exit 1
fi
dump_dir="$(dirname -- "$dump")"
dump_name="$(basename -- "$dump")"

require_az_login

fqdn="$(server_fqdn)"
if [[ -z $fqdn ]]; then
    echo "no PostgreSQL server found in rg-maa-data: run deploy.sh first" >&2
    exit 1
fi
"$SCRIPTS/db-access.sh" open
trap '"$SCRIPTS/db-access.sh" close || echo "could not close owner-access; run db-access.sh close" >&2' EXIT

export_pg_token
conninfo="$(admin_conninfo "$fqdn")"
query() {
    pg_client psql "$conninfo" -X -A -t -v ON_ERROR_STOP=1 -c "$1"
}

# Refuse anything but an empty database: stored results are never overwritten or merged.
if [[ -n "$(query "SELECT to_regclass('public.detection_runs')")" ]]; then
    rows="$(query 'SELECT count(*) FROM detection_runs')"
    if ((rows > 0)); then
        echo "refusing: detection_runs already holds $rows row(s); nothing restored" >&2
    else
        echo "refusing: the tables already exist (the job has run its migrations), so the" >&2
        echo "restore would collide with them; nothing restored. Restore right after" >&2
        echo "db-bootstrap.sh, before the next execution." >&2
    fi
    exit 1
fi

# One transaction: either everything is restored or nothing is.
PG_CLIENT_DIR="$dump_dir" pg_client pg_restore --no-owner --role="$JOB_ROLE" --exit-on-error \
    --single-transaction -d "$conninfo" "/work-dump/$dump_name"

echo "restored: detection_runs has $(query 'SELECT count(*) FROM detection_runs') row(s);"
echo "compare with the count decommission.sh printed before the export (SC-009)"
unset PGPASSWORD
