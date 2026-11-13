#!/usr/bin/env bash
# Opens or closes the owner-access firewall rule of the PostgreSQL server (research R14).
#
#   infra/scripts/db-access.sh open    # allow this host's public IPv4 address
#   infra/scripts/db-access.sh close   # set the rule back to 192.0.2.1 (TEST-NET-1, never routed)
#
# The rule is updated, never deleted: the CanNotDelete lock on rg-maa-data forbids deleting it.
# Every deployment also resets it to closed.

# shellcheck source=infra/scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

CLOSED_IP=192.0.2.1

usage() {
    echo "usage: $0 open|close" >&2
    exit 2
}

[[ $# -eq 1 ]] || usage
require_az_login

case "$1" in
    open)
        # Firewall rules accept IPv4 only, so the lookup is forced onto IPv4 (-4).
        ip="$(curl -4 -fsS --max-time 10 https://api.ipify.org || true)"
        if [[ -z $ip ]]; then
            echo "no public IPv4 address came back from api.ipify.org (does this host have IPv4" >&2
            echo "connectivity?); PostgreSQL firewall rules accept IPv4 only, so nothing was changed" >&2
            exit 1
        fi
        if ! [[ $ip =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]]; then
            echo "could not determine this host's public IPv4 address" >&2
            exit 1
        fi
        for octet in "${BASH_REMATCH[@]:1}"; do
            if ((10#$octet > 255)); then
                echo "could not determine this host's public IPv4 address" >&2
                exit 1
            fi
        done
        ;;
    close)
        ip="$CLOSED_IP"
        ;;
    *)
        usage
        ;;
esac

server="$(server_name)"
if [[ -z $server ]]; then
    echo "no PostgreSQL server found in rg-maa-data" >&2
    exit 1
fi

# Long forms only: the current CLI takes the server as --server-name and the rule as --name
# (--rule-name/-r is gone, and -n now means the rule, not the server).
az postgres flexible-server firewall-rule update \
    --resource-group rg-maa-data --server-name "$server" --name owner-access \
    --start-ip-address "$ip" --end-ip-address "$ip" \
    --output none

if [[ $1 == open ]]; then
    echo "owner-access is open for this host; run '$0 close' when you are done"
else
    echo "owner-access is closed"
fi
