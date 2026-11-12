# shellcheck shell=bash
# Shared helpers for the host-side operation scripts in infra/scripts/.
#
# Sourced, never executed:  source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
#
# Every az, gh, podman and database-client command of this project runs on the owner's host,
# never inside the agent container (specs/002-azure-daily-run/research.md R15). The repository is
# public: these scripts read tenant, subscription and object IDs from az at run time and never
# write or print them (research R16).

set -euo pipefail

if [[ -e /run/.containerenv || -e /.dockerenv ]]; then
    echo "run this on the host, not inside a container (research R15)" >&2
    exit 1
fi

# Fails unless the Azure CLI is installed and signed in.
require_az_login() {
    if ! command -v az >/dev/null 2>&1; then
        echo "the Azure CLI (az) is not installed" >&2
        exit 1
    fi
    if ! az account show --output none 2>/dev/null; then
        echo "not signed in to Azure: run 'az login' first" >&2
        exit 1
    fi
}

# The repository's top-level directory.
repo_root() {
    git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel
}

# Runs a PostgreSQL 17 client tool (psql, pg_dump, pg_restore) in a throwaway container, so no
# local client installation is needed. PGPASSWORD (an Entra access token) is passed through from
# the caller's environment and never appears on the command line.
pg_client() {
    podman run --rm -i -e PGPASSWORD --network host docker.io/library/postgres:17 "$@"
}

# Name and host name of the PostgreSQL server in rg-maa-data, read at run time.
server_name() {
    az postgres flexible-server list -g rg-maa-data --query "[0].name" -o tsv
}

server_fqdn() {
    az postgres flexible-server list -g rg-maa-data --query "[0].fullyQualifiedDomainName" -o tsv
}

# The owner's own Entra access token for Azure Database for PostgreSQL, exported as PGPASSWORD for
# pg_client. Never printed.
export_pg_token() {
    PGPASSWORD="$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv)"
    export PGPASSWORD
}
