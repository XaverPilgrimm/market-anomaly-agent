#!/usr/bin/env bash
# Quickstart check Q1: does <region> offer everything this feature needs, for this subscription?
#
#   infra/scripts/preflight.sh germanywestcentral
#
# Fallback order if it fails (research R2): westeurope, northeurope, swedencentral. Set the
# region that passes as `location` in infra/main.bicepparam.

# shellcheck source=infra/scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <region>" >&2
    exit 2
fi
region=$1
require_az_login
if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required (for example: sudo apt install jq)" >&2
    exit 1
fi

echo "subscription: $(az account show --query name -o tsv)"
echo "region:       $region"

failed=0
check() {
    local label=$1 ok=$2
    if [[ $ok == true ]]; then
        echo "PASS  $label"
    else
        echo "FAIL  $label"
        failed=1
    fi
}

caps="$(az postgres flexible-server list-skus -l "$region" -o json)"

# Shape of the capabilities response: a list of capability sets, each with server editions
# (tiers) holding SKUs, and supported server versions either at the top level or per edition.
b1ms="$(jq -r '[.[].supportedServerEditions[]? | select(.name == "Burstable") | .supportedServerSkus[]?.name] | index("Standard_B1ms") != null' <<<"$caps" 2>/dev/null || echo false)"
check "PostgreSQL Flexible Server: Standard_B1ms in tier Burstable" "$b1ms"

pg17="$(jq -r '[.[] | (.supportedServerVersions[]?.name), (.supportedServerEditions[]?.supportedServerVersions[]?.name)] | index("17") != null' <<<"$caps" 2>/dev/null || echo false)"
check "PostgreSQL Flexible Server: version 17" "$pg17"

open="$(jq -r '[.[].restricted? // "Disabled"] | all(. != "Enabled")' <<<"$caps" 2>/dev/null || echo false)"
check "PostgreSQL Flexible Server: provisioning not restricted for this subscription" "$open"

display="$(az account list-locations --query "[?name=='$region'].displayName | [0]" -o tsv)"
jobs="$(az provider show -n Microsoft.App --query "resourceTypes[?resourceType=='jobs'].locations | [0]" -o json)"
# $d below is a jq variable, bound by --arg, not a shell variable.
# shellcheck disable=SC2016
has_jobs="$(jq -r --arg d "$display" 'index($d) != null' <<<"$jobs" 2>/dev/null || echo false)"
check "Container Apps jobs available in $display" "$has_jobs"

if ((failed)); then
    echo
    echo "Region $region does not pass. Try the next fallback (research R2)."
    exit 1
fi
echo
echo "Region $region passes check Q1."
