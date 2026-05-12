<#
.SYNOPSIS
    SAP SRE Agent — Infrastructure Deployment

.DESCRIPTION
    Creates RG_SRE_OPS with: Storage Account, Managed Identity, 2 Function Apps.
    After running: deploy function code, assign RBAC on SAP RGs, deploy collector.

.PARAMETER SubscriptionId
    Subscription where RG_SRE_OPS will be created.

.PARAMETER StorageAccountName
    Globally unique storage account name for SAP config snapshots.

.PARAMETER IntegrationSubnetId
    Full resource ID of the subnet for function app VNet integration.
    Must be delegated to Microsoft.Web/serverFarms.
    Example: /subscriptions/.../resourceGroups/.../providers/Microsoft.Network/virtualNetworks/.../subnets/IntegrationSubnet

.PARAMETER ConfigProxyName
    Function app name for config proxy. Must be globally unique. Default: sap-config-proxy

.PARAMETER CommandProxyName
    Function app name for command proxy. Must be globally unique. Default: sap-command-proxy

.EXAMPLE
    .\deploy-sre-infra.ps1 `
        -SubscriptionId "12345678-..." `
        -StorageAccountName "stsreconfigs001" `
        -IntegrationSubnetId "/subscriptions/.../resourceGroups/RG_Network/providers/Microsoft.Network/virtualNetworks/VNET_SAP/subnets/IntegrationSubnet"
#>

param(
    [Parameter(Mandatory)] [string] $SubscriptionId,
    [Parameter(Mandatory)] [string] $StorageAccountName,
    [Parameter(Mandatory)] [string] $IntegrationSubnetId,
    [string] $ConfigProxyName = "sap-config-proxy",
    [string] $CommandProxyName = "sap-command-proxy"
)

$ErrorActionPreference = "Stop"
$RG         = "RG_SRE_OPS"
$UmiName    = "sre-ops-umi"
$Plan       = "sre-ops-plan"
$Container  = "sap-configs"
$Location   = "centralus"
$CollectorScript = Join-Path $PSScriptRoot "..\collector\collect-sap-configs.sh"
$VNetId     = $IntegrationSubnetId -replace '/subnets/.*$', ''
$SubnetName = $IntegrationSubnetId.Split("/")[-1]

function Write-Step  { param($msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "   OK: $msg" -ForegroundColor Green }

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " SAP SRE Agent — Infrastructure Deployment" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Subscription: $SubscriptionId"
Write-Host "  Storage:      $StorageAccountName"
Write-Host "  Subnet:       $IntegrationSubnetId"
Write-Host ""

$azVersion = (az version -o json 2>$null | ConvertFrom-Json).'azure-cli'
if (-not $azVersion) { throw "az CLI not found" }
az account set --subscription $SubscriptionId

# Step 1: Resource Group
Write-Step "Step 1/7 — Resource Group"
az group create --name $RG --location $Location --output none 2>$null
Write-OK "$RG"

# Step 2: Managed Identity
Write-Step "Step 2/7 — Managed Identity"
az identity create --name $UmiName -g $RG --location $Location --output none 2>$null
$UMI_ID = az identity show -n $UmiName -g $RG --query id -o tsv
$UMI_CLIENT_ID = az identity show -n $UmiName -g $RG --query clientId -o tsv
$UMI_PRINCIPAL_ID = az identity show -n $UmiName -g $RG --query principalId -o tsv
Write-OK "$UmiName (Client ID: $UMI_CLIENT_ID)"

# Step 3: Storage Account + Collector Upload
Write-Step "Step 3/7 — Storage Account"
az storage account create --name $StorageAccountName -g $RG -l $Location --sku Standard_LRS --kind StorageV2 --allow-shared-key-access false --default-action Deny --min-tls-version TLS1_2 --output none 2>$null
az storage container create --name $Container --account-name $StorageAccountName --auth-mode login --output none 2>$null
Write-OK "$StorageAccountName (container: $Container)"

# Storage RBAC for UMI
$stScope = "/subscriptions/$SubscriptionId/resourceGroups/$RG/providers/Microsoft.Storage/storageAccounts/$StorageAccountName"
foreach ($role in @("Storage Blob Data Owner", "Storage Blob Data Contributor", "Storage Queue Data Contributor", "Storage Table Data Contributor")) {
    az role assignment create --assignee-object-id $UMI_PRINCIPAL_ID --assignee-principal-type ServicePrincipal --role $role --scope $stScope --output none 2>$null
}
Write-OK "Storage roles assigned"

# Storage firewall
az storage account network-rule add --account-name $StorageAccountName --subnet $IntegrationSubnetId --output none 2>$null
Write-OK "Storage firewall: IntegrationSubnet allowed"

# Upload collector script
if (Test-Path $CollectorScript) {
    az storage blob upload --account-name $StorageAccountName --container-name $Container --name scripts/collect-sap-configs.sh --file $CollectorScript --auth-mode login --overwrite --output none 2>$null
    Write-OK "Collector script uploaded to $Container/scripts/"
}

# Step 4: App Service Plan
Write-Step "Step 4/7 — App Service Plan"
az appservice plan create --name $Plan -g $RG -l $Location --sku B1 --is-linux --output none 2>$null
Write-OK "$Plan (B1 Linux)"

# Step 5: Function Apps
Write-Step "Step 5/7 — Function Apps"
$API_KEY = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N").Substring(0,16)
foreach ($fn in @($ConfigProxyName, $CommandProxyName)) {
    az functionapp create --name $fn -g $RG --plan $Plan --runtime python --runtime-version 3.11 --functions-version 4 --os-type Linux --storage-account $StorageAccountName --output none 2>$null
    Write-OK "Created $fn"
}

# Step 6: Configure
Write-Step "Step 6/7 — Configuration"
foreach ($fn in @($ConfigProxyName, $CommandProxyName)) {
    az functionapp identity assign -n $fn -g $RG --identities $UMI_ID -o none 2>$null
    az functionapp config appsettings delete -n $fn -g $RG --setting-names AzureWebJobsStorage -o none 2>$null
    az functionapp config appsettings set -n $fn -g $RG --output none --settings `
        AzureWebJobsStorage__accountName=$StorageAccountName `
        AzureWebJobsStorage__blobServiceUri=https://$StorageAccountName.blob.core.windows.net `
        AzureWebJobsStorage__queueServiceUri=https://$StorageAccountName.queue.core.windows.net `
        AzureWebJobsStorage__tableServiceUri=https://$StorageAccountName.table.core.windows.net `
        AzureWebJobsStorage__credential=managedidentity `
        AzureWebJobsStorage__clientId=$UMI_CLIENT_ID `
        AzureWebJobsSecretStorageType=files
    az functionapp config set -n $fn -g $RG --always-on true --output none
    az functionapp vnet-integration add -n $fn -g $RG --vnet $VNetId --subnet $SubnetName --output none 2>$null
}

az functionapp config appsettings set -n $ConfigProxyName -g $RG --output none --settings AZURE_CLIENT_ID=$UMI_CLIENT_ID STORAGE_ACCOUNT_NAME=$StorageAccountName CONTAINER_NAME=$Container AGENT_KEY_sre1=$API_KEY
az functionapp config appsettings set -n $CommandProxyName -g $RG --output none --settings AZURE_CLIENT_ID=$UMI_CLIENT_ID AGENT_KEY_sre1=$API_KEY
Write-OK "Both function apps configured"

# Step 7: Summary
Write-Step "Step 7/7 — Done"
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Config Proxy:   https://$ConfigProxyName.azurewebsites.net/api"
Write-Host "  Command Proxy:  https://$CommandProxyName.azurewebsites.net/api"
Write-Host "  API Key:        $API_KEY"
Write-Host "  UMI Client ID:  $UMI_CLIENT_ID"
Write-Host "  UMI Principal:  $UMI_PRINCIPAL_ID"
Write-Host "  Storage:        $StorageAccountName"
Write-Host ""
Write-Host "Next:" -ForegroundColor Yellow
Write-Host "  1. func azure functionapp publish $ConfigProxyName --python"
Write-Host "  2. func azure functionapp publish $CommandProxyName --python"
Write-Host "  3. Assign RBAC: Reader + VM Contributor on SAP RGs for UMI ($UMI_PRINCIPAL_ID)"
Write-Host "  4. SRE Agent: import skills, add landscape, paste team onboarding"
Write-Host "  5. Collector: see README Step 4 for VM deployment instructions"
Write-Host ""
