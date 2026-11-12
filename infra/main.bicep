// Unattended daily run on Azure: subscription-scope entry point.
// Design: specs/002-azure-daily-run/ (plan.md, data-model.md §3, contracts/deployment-contract.md).
//
// Deploy only from the owner's host, through infra/scripts/deploy.sh, which always shows a what-if
// first (research R13). The repository is public: no tenant ID, subscription ID, object ID, email
// address or key is ever written into this file or into main.bicepparam (research R16).

targetScope = 'subscription'

@description('Azure region for every resource of this feature (FR-015).')
param location string

@description('GitHub repository allowed to sign in through OIDC, as owner/name.')
param githubRepository string

@description('Display name of the Entra security group that is the PostgreSQL Entra administrator.')
param dbAdminGroupName string = 'maa-db-admins'

@description('Object ID of that group. Read at run time by deploy.sh; never stored in a file.')
param dbAdminGroupObjectId string

@description('Address for failure alerts and budget notifications. Supplied at deploy time only (FR-022).')
param alertEmail string

@secure()
@description('Twelve Data API key. Empty keeps the secret already in Key Vault (research R5).')
param twelveDataApiKey string = ''

@description('Image the job runs. deploy.sh passes the image currently on the job (research R7).')
param jobImage string

@description('IP allowed by the owner-access firewall rule. 192.0.2.1 (TEST-NET-1) means closed.')
param ownerAccessIp string = '192.0.2.1'

@description('Monthly Azure budget in the billing currency, EUR (constitution Principle V).')
param budgetAmountEur int = 30

@description('Job replica timeout in seconds (research R1, quickstart check Q3).')
param jobReplicaTimeoutSeconds int = 3600

@description('First day of the budget period; must be the first of a month.')
param budgetStartDate string = '2026-11-01'

resource dataRg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-maa-data'
  location: location
}

resource envRg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-maa-env'
  location: location
}

// The pipeline identity may read and update the job, and nothing else: no start (which could
// reference the job's secrets through a template override), no delete (research R7).
resource jobImageUpdater 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(subscription().id, 'maa-job-image-updater')
  properties: {
    roleName: 'Market Anomaly Agent job image updater'
    description: 'Read and update the image of the Container Apps job job-maa-detect.'
    type: 'CustomRole'
    permissions: [
      {
        actions: [
          'Microsoft.App/jobs/read'
          'Microsoft.App/jobs/write'
        ]
        notActions: []
      }
    ]
    assignableScopes: [
      subscription().id
    ]
  }
}

module data 'modules/data.bicep' = {
  name: 'maa-data'
  scope: dataRg
  params: {
    location: location
    dbAdminGroupName: dbAdminGroupName
    dbAdminGroupObjectId: dbAdminGroupObjectId
    ownerAccessIp: ownerAccessIp
  }
}

module env 'modules/env.bicep' = {
  name: 'maa-env'
  scope: envRg
  params: {
    location: location
    githubRepository: githubRepository
    dbAdminGroupObjectId: dbAdminGroupObjectId
    alertEmail: alertEmail
    twelveDataApiKey: twelveDataApiKey
    jobImage: jobImage
    jobReplicaTimeoutSeconds: jobReplicaTimeoutSeconds
    serverFqdn: data.outputs.serverFqdn
    jobIdentityId: data.outputs.jobIdentityId
    jobIdentityClientId: data.outputs.jobIdentityClientId
    jobIdentityPrincipalId: data.outputs.jobIdentityPrincipalId
    jobImageUpdaterRoleId: jobImageUpdater.id
  }
}

// Printed by deploy.sh and stored as GitHub repository variables, never written to a file.
output AZURE_CLIENT_ID string = env.outputs.githubIdentityClientId
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_SUBSCRIPTION_ID string = subscription().subscriptionId
output serverFqdn string = data.outputs.serverFqdn
output jobName string = env.outputs.jobName
