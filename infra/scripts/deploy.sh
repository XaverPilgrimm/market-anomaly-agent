#!/usr/bin/env bash
# Deploys (or converges) the whole environment from infra/main.bicep, always behind a what-if and
# an interactive confirmation (contracts/deployment-contract.md §3, research R13).
#
#   infra/scripts/deploy.sh
#   MAA_ALERT_EMAIL=... infra/scripts/deploy.sh      # skip the email prompt
#
# The repository is public: the alert email, the Twelve Data key and every tenant, subscription
# and object ID are read at run time and never printed or written to a file (research R16).

# shellcheck source=infra/scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

ROOT="$(repo_root)"
TEMPLATE="$ROOT/infra/main.bicep"
PARAMS="$ROOT/infra/main.bicepparam"
PLACEHOLDER_IMAGE=mcr.microsoft.com/k8se/quickstart-jobs:latest

require_az_login
if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required (for example: sudo apt install jq)" >&2
    exit 1
fi

# 1. The Entra admin group must exist with exactly one member before Bicep references it.
"$ROOT/infra/scripts/entra-bootstrap.sh"

# 2. Inputs, read at run time.
location="$(sed -nE "s/^param location = '([^']+)'.*/\1/p" "$PARAMS")"
if [[ -z $location ]]; then
    echo "could not read the location from $PARAMS" >&2
    exit 1
fi

group_id="$(az ad group show --group maa-db-admins --query id -o tsv)"

# The federated credential's subject must equal the token's sub claim exactly, including case, so
# the repository is named as GitHub itself spells it, not as typed in main.bicepparam. (The GHCR
# image name in ci.yml stays lowercase, as GHCR requires.)
if ! repository="$(cd "$ROOT" && gh repo view --json nameWithOwner -q .nameWithOwner)" ||
    [[ -z $repository ]]; then
    echo "could not read the GitHub repository with 'gh repo view': is gh signed in?" >&2
    exit 1
fi

# GitHub issues immutable OIDC subjects for repositories created since 2026-07-15:
# repo:<owner>@<owner_id>/<repo>@<repo_id>:ref:refs/heads/main (research R7). The numeric IDs are
# read here and passed to Bicep, never hard-coded in a file.
if ! ids="$(gh api "repos/$repository" --jq '[(.owner.id | tostring), (.id | tostring)] | @tsv')"; then
    echo "could not read the repository's owner and repository IDs with 'gh api repos/$repository'" >&2
    exit 1
fi
IFS=$'\t' read -r owner_id repo_id <<<"$ids"
if ! [[ $owner_id =~ ^[0-9]+$ && $repo_id =~ ^[0-9]+$ ]]; then
    echo "gh api returned a non-numeric owner or repository ID; nothing deployed" >&2
    exit 1
fi

alert_email="${MAA_ALERT_EMAIL:-}"
if [[ -z $alert_email ]]; then
    read -r -p "Alert email address: " alert_email
fi

# main.bicepparam sets both to '' only because a parameter file must give every required
# parameter a value; an empty value here means the lookup or the prompt failed.
if [[ -z $group_id ]]; then
    echo "could not read the object ID of maa-db-admins; nothing deployed" >&2
    exit 1
fi
if [[ -z $alert_email ]]; then
    echo "an alert email address is required; nothing deployed" >&2
    exit 1
fi

# IFS= keeps leading and trailing whitespace, so a stray space or tab from a paste is caught below
# instead of being trimmed silently.
IFS= read -r -s -p "Twelve Data API key (empty keeps the stored key): " api_key
echo
if [[ -n $api_key ]]; then
    if [[ $api_key =~ [[:space:]] ]]; then
        echo "the key contains whitespace (a space, tab or line break); nothing deployed" >&2
        exit 1
    fi
    # Enough to compare with .env, never more: the length and the last 4 characters.
    echo "key read: ${#api_key} characters, ending in ...${api_key: -4}"
fi

vault="$(az keyvault list -g rg-maa-env --query "[0].name" -o tsv 2>/dev/null || true)"
secret_exists=false
if [[ -n $vault ]] &&
    az keyvault secret show --vault-name "$vault" -n twelve-data-api-key --query id -o tsv \
        >/dev/null 2>&1; then
    secret_exists=true
fi
# The job's Key Vault role is scoped to the secret itself, so the secret must exist.
if [[ -z $api_key && $secret_exists != true ]]; then
    echo "no Twelve Data key is stored yet (first deployment, or after teardown): enter it" >&2
    exit 1
fi

# Keep the image the pipeline last deployed, so a redeploy never rolls the job back (research R7).
# The placeholder is used only when the job provably does not exist (first deployment, or after
# teardown). Any failed lookup stops the script instead of silently falling back to it. Generic
# `az resource` calls are used, so the containerapp CLI extension is not needed here.
job_image=""
if [[ "$(az group exists -n rg-maa-env)" == true ]]; then
    if ! job_id="$(az resource list -g rg-maa-env --resource-type Microsoft.App/jobs \
        --query "[?name=='job-maa-detect'].id | [0]" -o tsv)"; then
        echo "could not check whether job-maa-detect exists; nothing deployed" >&2
        exit 1
    fi
    if [[ -n $job_id ]]; then
        if ! job_image="$(az resource show --ids "$job_id" \
            --query "properties.template.containers[0].image" -o tsv)" || [[ -z $job_image ]]; then
            echo "job-maa-detect exists but its current image could not be read; nothing deployed" >&2
            exit 1
        fi
    fi
fi
job_image="${job_image:-$PLACEHOLDER_IMAGE}"

name="maa-$(date -u +%Y%m%dT%H%M%SZ)"
args=(
    -l "$location" -n "$name" -f "$TEMPLATE"
    -p "$PARAMS"
    -p dbAdminGroupObjectId="$group_id" alertEmail="$alert_email" jobImage="$job_image"
    githubRepository="$repository" githubOwnerId="$owner_id" githubRepositoryId="$repo_id"
    twelveDataApiKey="$api_key"
)

# 3. What-if first, then create only after an interactive "y".
echo "what-if for deployment $name in $location (repository: $repository, job image: $job_image)"
az deployment sub what-if "${args[@]}"
az deployment sub create "${args[@]}" --confirm-with-what-if --output none

state="$(az deployment sub show -n "$name" --query properties.provisioningState -o tsv 2>/dev/null || true)"
if [[ $state != Succeeded ]]; then
    echo "deployment $name was not applied (state: ${state:-not created})" >&2
    exit 1
fi
unset api_key

# Output names are matched case-insensitively: ARM can return them with a changed case (for
# example azurE_CLIENT_ID), and a JMESPath query for the exact name then yields nothing.
outputs="$(az deployment sub show -n "$name" --query properties.outputs -o json)"
output() {
    jq -r --arg k "$1" \
        'to_entries | map(select((.key | ascii_downcase) == ($k | ascii_downcase))) | .[0].value.value // empty' \
        <<<"$outputs"
}

echo
echo "deployed: server $(output serverFqdn), job $(output jobName)"

# 4. GitHub repository secrets for the pipeline's OIDC sign-in (research R7). This repository is
#    public and so are its Actions logs; azure/login prints its inputs, and only secrets are masked
#    there. Each value goes from the deployment outputs to gh on stdin: never printed, never on a
#    command line, never interactive (gh reads the value from stdin when stdin is not a terminal).
for var in AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_SUBSCRIPTION_ID; do
    read -r -p "Set GitHub repository secret $var from this deployment? [y/N] " answer
    if [[ $answer == [yY] ]]; then
        value="$(output "$var")"
        if [[ -z $value ]]; then
            echo "deployment output $var is empty; $var not set" >&2
            continue
        fi
        printf '%s' "$value" | gh secret set "$var" --repo "$repository"
        echo "set secret $var"
    fi
done
unset value

# The secrets context is not available in a job-level `if`, so publish-deploy is gated on this
# non-sensitive variable instead.
read -r -p "Enable publish-deploy (repository variable AZURE_DEPLOY_ENABLED=true)? [y/N] " answer
if [[ $answer == [yY] ]]; then
    gh variable set AZURE_DEPLOY_ENABLED --body true --repo "$repository" </dev/null
    echo "set variable AZURE_DEPLOY_ENABLED=true"
fi

# The three values used to be repository variables (before 2026-11-13); offer to remove them.
# The names come from the REST API (gh api, as for the repository IDs above), not from
# `gh variable get`, and a failed listing is reported instead of being taken to mean "none left":
# the earlier `if gh variable get … >/dev/null 2>&1` silently skipped every offer (tasks.md T025).
# Only names are read; the values are never fetched.
if ! existing_vars="$(gh api "repos/$repository/actions/variables" --paginate --jq '.variables[].name')"; then
    echo "could not list the repository variables; check by hand for old AZURE_CLIENT_ID," >&2
    echo "AZURE_TENANT_ID and AZURE_SUBSCRIPTION_ID variables (gh variable list) and delete them" >&2
    existing_vars=""
fi
for var in AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_SUBSCRIPTION_ID; do
    if grep -qxF "$var" <<<"$existing_vars"; then
        read -r -p "Delete the old repository variable $var (now a secret)? [y/N] " answer
        if [[ $answer == [yY] ]]; then
            gh variable delete "$var" --repo "$repository"
            echo "deleted variable $var"
        fi
    fi
done

cat <<EOF

Next steps (quickstart.md §2):
  1. infra/scripts/db-access.sh open
  2. infra/scripts/db-bootstrap.sh          (new server only; check Q7 part 1)
  3. infra/scripts/db-access.sh close
  4. gh workflow run ci.yml --ref main      (while the job still runs $PLACEHOLDER_IMAGE)
  5. Set the GHCR package market-anomaly-agent to public (once, after its first push)
EOF
