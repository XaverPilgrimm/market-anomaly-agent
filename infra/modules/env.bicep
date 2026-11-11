// rg-maa-env: everything that can be deleted and recreated (logs, environment, Key Vault, job,
// pipeline identity, alerts). Stub from tasks.md T005. Resources are added in T018, T019 and
// T034–T036.

@description('Azure region.')
param location string

@description('GitHub repository allowed to sign in through OIDC, as owner/name.')
param githubRepository string

@description('Object ID of the Entra administrator group (Key Vault Secrets Officer).')
param dbAdminGroupObjectId string

@description('Address for failure alerts.')
param alertEmail string

@secure()
@description('Twelve Data API key; empty keeps the stored secret.')
param twelveDataApiKey string

@description('Image the job runs.')
param jobImage string

@description('Job replica timeout in seconds.')
param jobReplicaTimeoutSeconds int

@description('Fully qualified domain name of the PostgreSQL server.')
param serverFqdn string

@description('Resource ID of the job identity id-maa-job.')
param jobIdentityId string

@description('Client ID of the job identity.')
param jobIdentityClientId string

@description('Principal (object) ID of the job identity.')
param jobIdentityPrincipalId string

output githubIdentityClientId string = ''
output jobName string = 'job-maa-detect'
