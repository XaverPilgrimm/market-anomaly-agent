#!/usr/bin/env bash
# Ensures the Entra security group maa-db-admins exists and has exactly one member: the signed-in
# account (contracts/deployment-contract.md §2a, research R16). Idempotent. Run by deploy.sh as its
# first step, and runnable on its own.
#
# The repository is public: object IDs are read at run time and never printed.

# shellcheck source=infra/scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

GROUP=maa-db-admins

require_az_login

uid="$(az ad signed-in-user show --query id -o tsv)"
if [[ -z $uid ]]; then
    echo "could not read the signed-in user" >&2
    exit 1
fi

if ! az ad group show --group "$GROUP" --output none 2>/dev/null; then
    az ad group create --display-name "$GROUP" --mail-nickname "$GROUP" --output none
    echo "created security group $GROUP"
fi

# Check for other members before changing anything, so a violation leaves the group untouched.
while IFS= read -r member; do
    if [[ -n $member && $member != "$uid" ]]; then
        echo "$GROUP must have exactly one member, the signed-in account; remove the others first" >&2
        exit 1
    fi
done < <(az ad group member list --group "$GROUP" --query "[].id" -o tsv)

if [[ "$(az ad group member check --group "$GROUP" --member-id "$uid" --query value -o tsv)" != true ]]; then
    az ad group member add --group "$GROUP" --member-id "$uid" --output none
    echo "added the signed-in account to $GROUP"
fi

echo "admin group ok"
