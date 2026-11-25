#!/usr/bin/env bash
# Ends the deployment for good or for a pause: tears down the environment, exports the database
# to a private local file, verifies it, and only then deletes the locked database group
# (FR-031, contracts/deployment-contract.md §6).
#
#   infra/scripts/decommission.sh ~/private/maa-$(date +%F).dump
#
# The dump path must be outside the repository and must not exist yet. The file holds stored
# results only, no credentials, but it is private: it is never uploaded or committed. Every step
# stops the script on failure, always before the database group is touched. Restore later with
# deploy.sh, db-access.sh open, db-bootstrap.sh and restore.sh (quickstart.md §5).
#
# The Entra group maa-db-admins is kept (it costs nothing and a later deployment reuses it).

# shellcheck source=infra/scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

DATA_RG=rg-maa-data
SCRIPTS="$(dirname "${BASH_SOURCE[0]}")"

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <dump-path outside the repository>" >&2
    exit 2
fi

# 1. The dump path: absolute, outside the repository, new, in an existing directory.
dump="$(realpath -m -- "$1")"
root="$(realpath -- "$(repo_root)")"
if [[ $dump == "$root" || $dump == "$root"/* ]]; then
    echo "refusing $dump: it is inside the repository ($root); choose a private path outside it" >&2
    exit 1
fi
if [[ -e $dump ]]; then
    echo "refusing $dump: the file already exists" >&2
    exit 1
fi
dump_dir="$(dirname -- "$dump")"
if [[ ! -d $dump_dir ]]; then
    echo "refusing $dump: the directory $dump_dir does not exist" >&2
    exit 1
fi
dump_name="$(basename -- "$dump")"

require_az_login

# 2. The environment first, so no job is left referencing a deleted identity. teardown.sh asks
#    for its own confirmation and refuses during the scheduled window.
"$SCRIPTS/teardown.sh"

# 3. Owner access to the server, closed again however the script ends while the group exists.
fqdn="$(server_fqdn)"
if [[ -z $fqdn ]]; then
    echo "no PostgreSQL server found in $DATA_RG" >&2
    exit 1
fi
"$SCRIPTS/db-access.sh" open
close_access() {
    "$SCRIPTS/db-access.sh" close || echo "could not close owner-access; run db-access.sh close" >&2
}
trap close_access EXIT

# 4. The export, written by pg_dump straight into the file (not through a pipe, so the custom
#    format keeps its data offsets), signed in as maa-db-admins with the owner's own token.
export_pg_token
conninfo="$(admin_conninfo "$fqdn")"
runs="$(pg_client psql "$conninfo" -X -A -t -v ON_ERROR_STOP=1 \
    -c 'SELECT count(*) FROM detection_runs')"
echo "detection_runs before the export: $runs row(s)"
PG_CLIENT_DIR="$dump_dir" PG_CLIENT_DIR_MODE=rw \
    pg_client pg_dump -Fc -f "/work-dump/$dump_name" "$conninfo"

# 5. Verify: non-empty, and readable as an archive.
if [[ ! -s $dump ]]; then
    echo "the export $dump is missing or empty; $DATA_RG is not deleted" >&2
    exit 1
fi
if ! PG_CLIENT_DIR="$dump_dir" pg_client pg_restore --list "/work-dump/$dump_name" >/dev/null; then
    echo "pg_restore --list cannot read $dump; $DATA_RG is not deleted" >&2
    exit 1
fi
unset PGPASSWORD
echo "export verified: $dump ($(stat -c %s -- "$dump") bytes, $runs detection run(s))"

# 6. Last confirmation before anything irreversible.
cat <<EOF

Next: remove the lock lock-maa-data and delete $DATA_RG with the database server and the job
identity. The only copy of the stored results is then $dump.
EOF
read -r -p "Type $DATA_RG to confirm: " answer
if [[ $answer != "$DATA_RG" ]]; then
    echo "not confirmed; $DATA_RG is kept (the environment is already torn down)" >&2
    exit 1
fi

# 7. The group goes, and with it the firewall rule, so there is nothing left to close.
trap - EXIT
"$SCRIPTS/db-access.sh" close
az lock delete -n lock-maa-data -g "$DATA_RG"
echo "deleting $DATA_RG (this can take several minutes) ..."
az group delete -n "$DATA_RG" --yes

cat <<EOF

Decommissioned. Keep $dump private. To come back (quickstart.md §5):
  infra/scripts/deploy.sh && infra/scripts/db-access.sh open && infra/scripts/db-bootstrap.sh
  infra/scripts/restore.sh $dump
The Entra group maa-db-admins is kept; remove it only when the project ends for good:
  az ad group delete --group maa-db-admins
EOF
