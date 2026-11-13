// rg-maa-env: everything that can be deleted and recreated (logs, environment, Key Vault, job,
// pipeline identity, alerts). Design: specs/002-azure-daily-run/data-model.md §3,
// contracts/deployment-contract.md §4 and research.md R1, R5, R7, R9.
// Alerts and the action group are added by tasks.md T034–T036.

@description('Azure region.')
param location string

@description('GitHub repository allowed to sign in through OIDC, as owner/name.')
param githubRepository string

@description('Numeric GitHub ID of the repository owner.')
@minLength(1)
param githubOwnerId string

@description('Numeric GitHub ID of the repository.')
@minLength(1)
param githubRepositoryId string

@description('Object ID of the Entra administrator group (Key Vault Secrets Officer).')
param dbAdminGroupObjectId string

@description('Address for failure alerts.')
param alertEmail string

@secure()
@description('Twelve Data API key; empty keeps the stored secret.')
param twelveDataApiKey string

@description('Image the job runs.')
param jobImage string

@description('Job replica timeout in seconds (research R1, quickstart check Q3).')
param jobReplicaTimeoutSeconds int

@description('Fully qualified domain name of the PostgreSQL server.')
param serverFqdn string

@description('Resource ID of the job identity id-maa-job.')
param jobIdentityId string

@description('Client ID of the job identity.')
param jobIdentityClientId string

@description('Principal (object) ID of the job identity.')
param jobIdentityPrincipalId string

@description('Resource ID of the custom role "Market Anomaly Agent job image updater".')
param jobImageUpdaterRoleId string

var suffix = take(uniqueString(subscription().id), 6)
var secretName = 'twelve-data-api-key'
// Built-in role definition IDs, as documented by Microsoft.
var keyVaultSecretsUserRole = '4633458b-17de-408a-b874-0445c86b69e6'
var keyVaultSecretsOfficerRole = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

// --- Logs and environment ---------------------------------------------------------------------

resource logs 'Microsoft.OperationalInsights/workspaces@2026-03-01' = {
  name: 'log-maa'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// Consumption only: no workloadProfiles, no VNet (research R10, Principle II).
resource environment 'Microsoft.App/managedEnvironments@2026-07-01' = {
  name: 'cae-maa'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// --- Key Vault: the only secret of this feature (FR-018, research R5) -------------------------

resource vault 'Microsoft.KeyVault/vaults@2026-05-15' = {
  name: 'kv-maa-${suffix}'
  location: location
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    // Purge protection stays off so teardown.sh can purge and an immediate redeploy can reuse
    // the name (research R5, FR-027).
  }
}

// Written only when deploy.sh was given a key; an empty key keeps the stored value.
resource newSecret 'Microsoft.KeyVault/vaults/secrets@2026-05-15' = if (!empty(twelveDataApiKey)) {
  parent: vault
  name: secretName
  properties: {
    value: twelveDataApiKey
  }
}

// The same secret, whether written above or by an earlier deployment. deploy.sh refuses an empty
// key while the secret does not exist yet, so this reference always resolves.
resource secret 'Microsoft.KeyVault/vaults/secrets@2026-05-15' existing = {
  parent: vault
  name: secretName
}

// The job may read this one secret, nothing else in the vault (FR-019).
resource jobReadsSecret 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: secret
  name: guid(vault.id, secretName, jobIdentityPrincipalId, keyVaultSecretsUserRole)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      keyVaultSecretsUserRole
    )
    principalId: jobIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [
    newSecret
  ]
}

// The owner, through the admin group, can rotate the key with az keyvault secret set.
resource adminsManageSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, dbAdminGroupObjectId, keyVaultSecretsOfficerRole)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      keyVaultSecretsOfficerRole
    )
    principalId: dbAdminGroupObjectId
    principalType: 'Group'
  }
}

// --- The daily job (contracts/deployment-contract.md §4) ---------------------------------------

resource job 'Microsoft.App/jobs@2026-07-01' = {
  name: 'job-maa-detect'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${jobIdentityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      // Must fit 3 attempts of ~13 minutes if the timer covers the whole execution (research R1).
      replicaTimeout: jobReplicaTimeoutSeconds
      replicaRetryLimit: 2
      scheduleTriggerConfig: {
        // UTC, Tuesday to Saturday: after every US close, before every US open (FR-010).
        cronExpression: '0 11 * * 2-6'
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: [
        {
          name: 'price-data-api-key'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/${secretName}'
          identity: jobIdentityId
        }
      ]
      // No registries block: the image is a public GHCR package (research R6).
    }
    template: {
      containers: [
        {
          name: 'detect'
          image: jobImage
          args: [
            '--latest'
            '--migrate'
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              // No password: the token from AZURE_CLIENT_ID's identity is used (research R3, R8).
              name: 'DATABASE_URL'
              value: 'postgresql://id-maa-job@${serverFqdn}:5432/market_anomaly_agent?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt'
            }
            {
              name: 'DATABASE_AUTH'
              value: 'entra'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: jobIdentityClientId
            }
            {
              name: 'PRICE_DATA_API_KEY'
              secretRef: 'price-data-api-key'
            }
          ]
        }
      ]
    }
  }
  dependsOn: [
    jobReadsSecret
  ]
}

// --- Pipeline identity: OIDC from main only, may update this one job (FR-008, FR-009, R7) -------

resource githubIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-maa-github'
  location: location
}

// GitHub issues immutable subject claims for repositories created since 2026-07-15: each name is
// followed by its numeric ID, repo:<owner>@<owner_id>/<repo>@<repo_id>:ref:refs/heads/main
// (research R7). Names keep GitHub's exact case; deploy.sh reads names and IDs with gh api.
var githubOwner = split(githubRepository, '/')[0]
var githubRepoName = split(githubRepository, '/')[1]

resource githubMain 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2024-11-30' = {
  parent: githubIdentity
  name: 'github-main'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    audiences: [
      'api://AzureADTokenExchange'
    ]
    subject: 'repo:${githubOwner}@${githubOwnerId}/${githubRepoName}@${githubRepositoryId}:ref:refs/heads/main'
  }
}

resource githubUpdatesJob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: job
  name: guid(job.id, githubIdentity.id, jobImageUpdaterRoleId)
  properties: {
    roleDefinitionId: jobImageUpdaterRoleId
    principalId: githubIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output githubIdentityClientId string = githubIdentity.properties.clientId
output jobName string = job.name
