// rg-maa-data: the database and the job's identity. Locked and kept by teardown (FR-030).
// Design: specs/002-azure-daily-run/data-model.md §3 and research.md R3, R4, R8, R14, R16.
// The database `market_anomaly_agent` itself is created by infra/sql/bootstrap.sql, not here
// (research R3). The CanNotDelete lock is added by tasks.md T040.

@description('Azure region.')
param location string

@description('Display name of the Entra administrator group.')
param dbAdminGroupName string

@description('Object ID of the Entra administrator group; supplied at deploy time, never in a file.')
param dbAdminGroupObjectId string

@description('IP allowed by the owner-access firewall rule; 192.0.2.1 (TEST-NET-1) means closed.')
param ownerAccessIp string

var suffix = take(uniqueString(subscription().id), 6)

// Lives with the data so its object ID, which the PostgreSQL role is bound to, survives a
// teardown of rg-maa-env (research R4).
resource jobIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-maa-job'
  location: location
}

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2026-10-01' = {
  name: 'psql-maa-${suffix}'
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '17'
    storage: {
      storageSizeGB: 32
      autoGrow: 'Disabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    // Entra only; no password exists anywhere (FR-017a, FR-020). No administratorLogin.
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: tenant().tenantId
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

// The Entra administrator is the security group, not a user (research R16).
resource admin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2026-10-01' = {
  parent: server
  name: dbAdminGroupObjectId
  properties: {
    principalType: 'Group'
    principalName: dbAdminGroupName
    tenantId: tenant().tenantId
  }
}

// Server parameters are updated one after the other: concurrent updates conflict.
resource requireTls 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2026-10-01' = {
  parent: server
  name: 'require_secure_transport'
  properties: {
    value: 'on'
    source: 'user-override'
  }
  dependsOn: [
    admin
  ]
}

resource minTls 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2026-10-01' = {
  parent: server
  name: 'ssl_min_protocol_version'
  properties: {
    value: 'TLSv1.2'
    source: 'user-override'
  }
  dependsOn: [
    requireTls
  ]
}

// Microsoft's convention for "allow public access from Azure services" (research R14). The job's
// outbound addresses change, so they cannot be listed; Entra-only login is the real gate.
resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2026-10-01' = {
  parent: server
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
  dependsOn: [
    minTls
  ]
}

// Never deleted (the lock forbids it): db-access.sh moves it between the owner's IP and
// 192.0.2.1, and every deployment resets it to closed (research R14).
resource ownerAccess 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2026-10-01' = {
  parent: server
  name: 'owner-access'
  properties: {
    startIpAddress: ownerAccessIp
    endIpAddress: ownerAccessIp
  }
  dependsOn: [
    allowAzure
  ]
}

output serverFqdn string = server.properties.fullyQualifiedDomainName
output serverName string = server.name
output jobIdentityId string = jobIdentity.id
output jobIdentityClientId string = jobIdentity.properties.clientId
output jobIdentityPrincipalId string = jobIdentity.properties.principalId
