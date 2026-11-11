// Non-secret defaults for infra/main.bicep.
//
// This repository is public. This file MUST NOT contain the alert email address, any tenant ID,
// subscription ID or object ID, any tenant domain, any IP address or the Twelve Data key
// (spec FR-018, FR-022, FR-025; research R16). deploy.sh reads those at run time and passes them
// on the command line: dbAdminGroupObjectId, alertEmail, jobImage and twelveDataApiKey.

using 'main.bicep'

param location = 'germanywestcentral'
param githubRepository = 'XaverPilgrimm/market-anomaly-agent'
param budgetAmountEur = 30
param budgetStartDate = '2026-11-01'

// Required parameters without a default here; deploy.sh always overrides them with -p.
param dbAdminGroupObjectId = ''
param alertEmail = ''
param jobImage = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'
