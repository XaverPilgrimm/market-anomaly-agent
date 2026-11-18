#!/usr/bin/env bash
# Deletes the environment group rg-maa-env and purges its soft-deleted Key Vault, so deploy.sh can
# recreate everything at once under the same names (FR-027, contracts/deployment-contract.md §6).
#
#   infra/scripts/teardown.sh
#
# This script never touches rg-maa-data: the database server, the job identity id-maa-job and
# their lock stay (FR-027, FR-030). It does not name that group anywhere.
#
# Timing (quickstart.md §4, "Teardown and rebuild"): the job runs at 11:00 UTC, Tuesday to
# Saturday, and alert-maa-no-completed-run checks between 12:45 and 13:45 UTC that it completed.
# Between 10:30 and 14:00 UTC on those days a teardown would kill a running execution or leave the
# rebuilt, empty workspace without the completed line, so the script refuses then unless
# MAA_TEARDOWN_ANYTIME=1 is set.
#
# After a teardown the vault is gone for good: deploy.sh then asks for the Twelve Data key again.

# shellcheck source=infra/scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

ENV_RG=rg-maa-env

if [[ $# -ne 0 ]]; then
    echo "usage: $0" >&2
    exit 2
fi

# 1..7 = Monday..Sunday; the schedule runs on 2..6 (Tuesday to Saturday).
weekday="$(date -u +%u)"
hhmm="$((10#$(date -u +%H%M)))"
if [[ ${MAA_TEARDOWN_ANYTIME:-} != 1 ]] && ((weekday >= 2 && weekday <= 6 && hhmm >= 1030 && hhmm < 1400)); then
    echo "refusing: it is $(date -u +%H:%M) UTC on a scheduled day (Tuesday to Saturday)." >&2
    echo "Between 10:30 and 14:00 UTC the 11:00 run or its completion check could be cut off." >&2
    echo "Run it outside that window, or set MAA_TEARDOWN_ANYTIME=1 if you accept that." >&2
    exit 1
fi

require_az_login

if [[ "$(az group exists -n "$ENV_RG")" != true ]]; then
    echo "$ENV_RG does not exist; nothing to delete"
    exit 0
fi

# Read before the delete: afterwards only the deleted-vault listing knows it.
vault="$(az keyvault list -g "$ENV_RG" --query "[0].name" -o tsv)"

cat <<EOF
This deletes resource group $ENV_RG and everything in it:
  log-maa (logs), cae-maa, job-maa-detect, ${vault:-the Key Vault} (then purged, with the
  Twelve Data key), id-maa-github (its client ID changes on redeploy), ag-maa-owner and both alerts.
The database group, the database, the job identity and the budget are not touched.
EOF
read -r -p "Type $ENV_RG to confirm: " answer
if [[ $answer != "$ENV_RG" ]]; then
    echo "not confirmed; nothing deleted" >&2
    exit 1
fi

echo "deleting $ENV_RG (this can take several minutes) ..."
az group delete -n "$ENV_RG" --yes

if [[ -z $vault ]]; then
    echo "no Key Vault was found in $ENV_RG; nothing to purge"
else
    location="$(az keyvault list-deleted --resource-type vault \
        --query "[?name=='$vault'].properties.location | [0]" -o tsv)"
    if [[ -z $location ]]; then
        echo "$vault is not in the deleted-vault list; nothing to purge" >&2
    else
        echo "purging $vault ..."
        az keyvault purge -n "$vault" -l "$location"
    fi
fi

cat <<EOF

$ENV_RG is deleted and the vault purged. To rebuild (quickstart.md §4, "Teardown and rebuild"):
  1. infra/scripts/deploy.sh            enter the Twelve Data key again; answer y to set the
                                        secret AZURE_CLIENT_ID (the pipeline identity is new)
  2. gh workflow run ci.yml --ref main  replaces the placeholder image before the next 11:00 run
No database bootstrap is needed: the server and id-maa-job are unchanged.
EOF
