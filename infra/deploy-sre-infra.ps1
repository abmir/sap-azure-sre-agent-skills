<#
.SYNOPSIS
    SAP SRE Agent — Automated Infrastructure Deployment
    Deploys all Azure resources needed for the SRE Agent in a single run.

.DESCRIPTION
    Creates: Resource Group, Storage Account, Managed Identity, Function Apps,
    RBAC assignments, VNet integration, custom VM Run Command role.
    
    After running this script, you only need to:
    1. Deploy function code (func azure functionapp publish)
    2. Deploy collector script to SAP VMs
    3. Create the SRE Agent at sre.azure.com and upload skills

.PARAMETER SubscriptionId
    Azure subscription ID where SAP workloads run.

.PARAMETER Location
    Azure region (e.g., centralus, eastus2). Should match SAP VM region.

.PARAMETER SreResourceGroup
    Resource group name for SRE operations components.

.PARAMETER StorageAccountName
    Globally unique storage account name for SAP configs.

.PARAMETER VNetName
    Name of the VNet where SAP VMs run.

.PARAMETER VNetResourceGroup
    Resource group containing the VNet.

.PARAMETER IntegrationSubnet
    Subnet for function app VNet integration (created if it doesn't exist).

.PARAMETER IntegrationSubnetCidr
    CIDR for the integration subnet if it needs to be created (e.g., 10.40.1.0/26).

.PARAMETER SapResourceGroups
    Comma-separated list of SAP resource groups (e.g., "RG_SAP_ECP,RG_SAP_QAS").

.EXAMPLE
    .\deploy-sre-infra.ps1 `
        -SubscriptionId "12345678-1234-1234-1234-123456789012" `
        -Location "centralus" `
        -SreResourceGroup "RG_SRE_OPS" `
        -StorageAccountName "stsreconfigs001" `
        -VNetName "VNET_SAP" `
        -VNetResourceGroup "RG_Network" `
        -IntegrationSubnet "IntegrationSubnet" `
        -IntegrationSubnetCidr "10.40.1.0/26" `
        -SapResourceGroups "RG_SAP_ECP,RG_SAP_QAS"
#>

param(
    [Parameter(Mandatory)] [string] $SubscriptionId,
    [Parameter(Mandatory)] [string] $Location,
    [string] $SreResourceGroup = "RG_SRE_OPS",
    [Parameter(Mandatory)] [string] $StorageAccountName,
    [Parameter(Mandatory)] [string] $VNetName,
    [Parameter(Mandatory)] [string] $VNetResourceGroup,
    [string] $IntegrationSubnet = "IntegrationSubnet",
    [string] $IntegrationSubnetCidr,
    [Parameter(Mandatory)] [string] $SapResourceGroups
)

$ErrorActionPreference = "Stop"
$UmiName         = "sre-ops-mi"
$FuncPlan        = "sre-ops-plan"
$FuncConfig      = "sap-config-proxy"
$FuncCommand     = "sap-command-proxy"
$ContainerName   = "sap-configs"
$SapRGs          = $SapResourceGroups -split ","

# --- Colors ---
function Write-Step  { param($msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "   OK: $msg" -ForegroundColor Green }
function Write-Skip  { param($msg) Write-Host "   SKIP: $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "   FAIL: $msg" -ForegroundColor Red }

# ============================================
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " SAP SRE Agent — Infrastructure Deployment" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Subscription:    $SubscriptionId"
Write-Host "Location:        $Location"
Write-Host "SRE RG:          $SreResourceGroup"
Write-Host "Storage:         $StorageAccountName"
Write-Host "VNet:            $VNetName ($VNetResourceGroup)"
Write-Host "Subnet:          $IntegrationSubnet"
Write-Host "SAP RGs:         $($SapRGs -join ', ')"
Write-Host ""

# ============================================
# Pre-flight checks
# ============================================
Write-Step "Pre-flight checks"

$azVersion = az version --query '"azure-cli"' -o tsv 2>$null
if (-not $azVersion) { throw "az CLI not found. Install from https://aka.ms/installazurecli" }
Write-OK "az CLI $azVersion"

az account set --subscription $SubscriptionId
Write-OK "Subscription set to $SubscriptionId"

# Check if func CLI is available (needed later for code deployment)
$funcAvail = Get-Command func -ErrorAction SilentlyContinue
if (-not $funcAvail) { Write-Skip "Azure Functions Core Tools not found — you'll need them for code deployment" }
else { Write-OK "func CLI available" }

# ============================================
# Step 1: Resource Group
# ============================================
Write-Step "Step 1/10 — Resource Group"
$rgExists = az group exists --name $SreResourceGroup 2>$null
if ($rgExists -eq "true") {
    Write-Skip "$SreResourceGroup already exists"
} else {
    az group create --name $SreResourceGroup --location $Location --output none
    Write-OK "Created $SreResourceGroup"
}

# ============================================
# Step 2: User-Assigned Managed Identity
# ============================================
Write-Step "Step 2/10 — Managed Identity"
$umiExists = az identity show -n $UmiName -g $SreResourceGroup --query id -o tsv 2>$null
if ($umiExists) {
    Write-Skip "$UmiName already exists"
} else {
    az identity create --name $UmiName --resource-group $SreResourceGroup --location $Location --output none
    Write-OK "Created $UmiName"
}

$UMI_ID = az identity show -n $UmiName -g $SreResourceGroup --query id -o tsv
$UMI_CLIENT_ID = az identity show -n $UmiName -g $SreResourceGroup --query clientId -o tsv
$UMI_PRINCIPAL_ID = az identity show -n $UmiName -g $SreResourceGroup --query principalId -o tsv
Write-OK "UMI Client ID: $UMI_CLIENT_ID"

# ============================================
# Step 3: Storage Account
# ============================================
Write-Step "Step 3/10 — Storage Account"
$stExists = az storage account show --name $StorageAccountName -g $SreResourceGroup --query id -o tsv 2>$null
if ($stExists) {
    Write-Skip "$StorageAccountName already exists"
} else {
    az storage account create `
        --name $StorageAccountName `
        --resource-group $SreResourceGroup `
        --location $Location `
        --sku Standard_LRS `
        --kind StorageV2 `
        --allow-shared-key-access false `
        --default-action Deny `
        --min-tls-version TLS1_2 `
        --output none
    Write-OK "Created $StorageAccountName (firewall: Deny, shared key: disabled)"
}

# Create container
$containerExists = az storage container exists --name $ContainerName --account-name $StorageAccountName --auth-mode login --query exists -o tsv 2>$null
if ($containerExists -eq "true") {
    Write-Skip "Container $ContainerName already exists"
} else {
    az storage container create --name $ContainerName --account-name $StorageAccountName --auth-mode login --output none
    Write-OK "Created container $ContainerName"
}

# ============================================
# Step 4: Storage RBAC
# ============================================
Write-Step "Step 4/10 — Storage RBAC"
$stScope = "/subscriptions/$SubscriptionId/resourceGroups/$SreResourceGroup/providers/Microsoft.Storage/storageAccounts/$StorageAccountName"

# Blob Data Reader (for config proxy to read)
az role assignment create `
    --assignee-object-id $UMI_PRINCIPAL_ID `
    --assignee-principal-type ServicePrincipal `
    --role "Storage Blob Data Reader" `
    --scope $stScope `
    --output none 2>$null
Write-OK "Storage Blob Data Reader assigned"

# Blob Data Contributor (for collector VMs to upload)
az role assignment create `
    --assignee-object-id $UMI_PRINCIPAL_ID `
    --assignee-principal-type ServicePrincipal `
    --role "Storage Blob Data Contributor" `
    --scope $stScope `
    --output none 2>$null
Write-OK "Storage Blob Data Contributor assigned"

# ============================================
# Step 5: Integration Subnet
# ============================================
Write-Step "Step 5/10 — Integration Subnet"
$subnetExists = az network vnet subnet show --vnet-name $VNetName -g $VNetResourceGroup -n $IntegrationSubnet --query id -o tsv 2>$null
if ($subnetExists) {
    Write-Skip "$IntegrationSubnet already exists"
} else {
    if (-not $IntegrationSubnetCidr) {
        throw "IntegrationSubnetCidr is required when the subnet doesn't exist. Specify a free /26 CIDR range."
    }
    az network vnet subnet create `
        --vnet-name $VNetName `
        --resource-group $VNetResourceGroup `
        --name $IntegrationSubnet `
        --address-prefixes $IntegrationSubnetCidr `
        --delegations "Microsoft.Web/serverFarms" `
        --output none
    Write-OK "Created $IntegrationSubnet ($IntegrationSubnetCidr)"
}

$SUBNET_ID = az network vnet subnet show --vnet-name $VNetName -g $VNetResourceGroup -n $IntegrationSubnet --query id -o tsv

# Add subnet to storage firewall
az storage account network-rule add --account-name $StorageAccountName --subnet $SUBNET_ID --output none 2>$null
Write-OK "Storage firewall: added $IntegrationSubnet"

# ============================================
# Step 6: App Service Plan
# ============================================
Write-Step "Step 6/10 — App Service Plan"
$planExists = az appservice plan show -n $FuncPlan -g $SreResourceGroup --query id -o tsv 2>$null
if ($planExists) {
    Write-Skip "$FuncPlan already exists"
} else {
    az appservice plan create `
        --name $FuncPlan `
        --resource-group $SreResourceGroup `
        --location $Location `
        --sku B1 `
        --is-linux `
        --output none
    Write-OK "Created $FuncPlan (B1 Linux)"
}

# ============================================
# Step 7: Function Apps
# ============================================
Write-Step "Step 7/10 — Function Apps"

# Generate API key
$API_KEY = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N").Substring(0,16)

foreach ($funcName in @($FuncConfig, $FuncCommand)) {
    $funcExists = az functionapp show -n $funcName -g $SreResourceGroup --query id -o tsv 2>$null
    if ($funcExists) {
        Write-Skip "$funcName already exists"
    } else {
        az functionapp create `
            --name $funcName `
            --resource-group $SreResourceGroup `
            --plan $FuncPlan `
            --runtime python `
            --runtime-version 3.11 `
            --functions-version 4 `
            --os-type Linux `
            --assign-identity $UMI_ID `
            --storage-account $StorageAccountName `
            --output none
        Write-OK "Created $funcName"
    }
}

# ============================================
# Step 8: Function App Settings + VNet Integration
# ============================================
Write-Step "Step 8/10 — Function App Configuration"

# Config proxy
az functionapp config appsettings set -n $FuncConfig -g $SreResourceGroup --output none --settings `
    AZURE_CLIENT_ID=$UMI_CLIENT_ID `
    STORAGE_ACCOUNT_NAME=$StorageAccountName `
    CONTAINER_NAME=$ContainerName `
    AGENT_KEY_sre1=$API_KEY

az functionapp config set -n $FuncConfig -g $SreResourceGroup --always-on true --output none
az functionapp vnet-integration add -n $FuncConfig -g $SreResourceGroup --vnet $VNetName --subnet $IntegrationSubnet --output none 2>$null
Write-OK "$FuncConfig configured (VNet + AlwaysOn + app settings)"

# Command proxy
az functionapp config appsettings set -n $FuncCommand -g $SreResourceGroup --output none --settings `
    AZURE_CLIENT_ID=$UMI_CLIENT_ID `
    SUBSCRIPTION_ID=$SubscriptionId `
    AGENT_KEY_sre1=$API_KEY

az functionapp config set -n $FuncCommand -g $SreResourceGroup --always-on true --output none
az functionapp vnet-integration add -n $FuncCommand -g $SreResourceGroup --vnet $VNetName --subnet $IntegrationSubnet --output none 2>$null
Write-OK "$FuncCommand configured (VNet + AlwaysOn + app settings)"

# ============================================
# Step 9: Custom VM Run Command Role + RBAC
# ============================================
Write-Step "Step 9/10 — VM Run Command Role + SAP RG RBAC"

$roleExists = az role definition list --name "Custom - VM Run Command Operator" --query "[0].id" -o tsv 2>$null
if ($roleExists) {
    Write-Skip "Custom role already exists"
} else {
    $roleDef = @{
        Name = "Custom - VM Run Command Operator"
        Description = "Run allowlisted read-only commands on SAP VMs via Azure VM Run Command"
        Actions = @(
            "Microsoft.Compute/virtualMachines/runCommand/action"
            "Microsoft.Compute/virtualMachines/read"
        )
        AssignableScopes = @("/subscriptions/$SubscriptionId")
    } | ConvertTo-Json -Depth 3

    $tempFile = [System.IO.Path]::GetTempFileName()
    $roleDef | Out-File -FilePath $tempFile -Encoding UTF8
    az role definition create --role-definition $tempFile --output none
    Remove-Item $tempFile
    Write-OK "Created custom role: VM Run Command Operator"
}

# Assign roles on each SAP RG
foreach ($rg in $SapRGs) {
    $rgTrimmed = $rg.Trim()
    $rgScope = "/subscriptions/$SubscriptionId/resourceGroups/$rgTrimmed"
    
    az role assignment create `
        --assignee-object-id $UMI_PRINCIPAL_ID `
        --assignee-principal-type ServicePrincipal `
        --role "Custom - VM Run Command Operator" `
        --scope $rgScope `
        --output none 2>$null
    Write-OK "VM Run Command role on $rgTrimmed"
}

# ============================================
# Step 10: Summary
# ============================================
Write-Step "Step 10/10 — Deployment Summary"

$configUrl = "https://$FuncConfig.azurewebsites.net/api"
$commandUrl = "https://$FuncCommand.azurewebsites.net/api"

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Save these values for agent setup:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  UMI Client ID:      $UMI_CLIENT_ID"
Write-Host "  UMI Principal ID:   $UMI_PRINCIPAL_ID"
Write-Host "  Storage Account:    $StorageAccountName"
Write-Host "  Config Proxy URL:   $configUrl"
Write-Host "  Command Proxy URL:  $commandUrl"
Write-Host "  API Key:            $API_KEY"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Deploy function code:"
Write-Host "     cd proxy/sre-config-proxy && func azure functionapp publish $FuncConfig --python"
Write-Host "     cd proxy/sre-command-proxy && func azure functionapp publish $FuncCommand --python"
Write-Host ""
Write-Host "  2. Deploy collector to SAP VMs (see docs/deployment-guide.md Phase 2)"
Write-Host "     Set these env vars on each VM in /opt/sre/sre.env:"
Write-Host "       SRE_STORAGE_ACCOUNT=$StorageAccountName"
Write-Host "       SRE_UMI_CLIENT_ID=$UMI_CLIENT_ID"
Write-Host ""
Write-Host "  3. Create SRE Agent at https://sre.azure.com"
Write-Host "     - Connect this repo as Knowledge Source"
Write-Host "     - Upload 15 skills from skills/ folder"
Write-Host "     - Fill in Team Onboarding with values above"
Write-Host ""
