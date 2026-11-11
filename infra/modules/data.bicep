// rg-maa-data: the database and the job's identity. Locked and kept by teardown (FR-030).
// Stub from tasks.md T005. Resources are added in T017 (server, identity) and T040 (lock).

@description('Azure region.')
param location string

@description('Display name of the Entra administrator group.')
param dbAdminGroupName string

@description('Object ID of the Entra administrator group.')
param dbAdminGroupObjectId string

@description('IP allowed by the owner-access firewall rule; 192.0.2.1 means closed.')
param ownerAccessIp string

output serverFqdn string = ''
output serverName string = ''
output jobIdentityId string = ''
output jobIdentityClientId string = ''
output jobIdentityPrincipalId string = ''
