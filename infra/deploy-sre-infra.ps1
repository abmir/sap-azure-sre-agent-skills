<#
.SYNOPSIS
    SAP SRE Agent — Config Store deployment (storage + collector identity).

.DESCRIPTION
    Deploys the OPTIONAL config store that unlocks STAF config validation and the
    config-enriched skills. It creates:
      • a resource group,
      • a collector managed identity (assigned to SAP VMs to upload configs),
      • a private storage account with a `sap-configs` container.
    It grants the SRE Agent's OWN managed identity Storage Blob Data Reader so the
    agent reads configs DIRECTLY — no proxy is involved.

    Live VM commands are a separate, optional add-on: deploy the MCP command proxy
    with infra/deploy-mcp-proxy.ps1 and register it as an SRE Agent connector.

.PARAMETER SubscriptionId
    Subscription to deploy into. Required.

.PARAMETER StorageAccountName
    Globally-unique storage account name (3-24 chars, lowercase + digits). Required.

.PARAMETER SreAgentUmiPrincipalId
    Object/Principal ID of the SRE Agent's managed identity (sre.azure.com → Identity).
    Granted Storage Blob Data Reader for direct config access. Strongly recommended.

.PARAMETER ResourceGroupName
    Resource group for the config store. Default: rg-sre-agent

.PARAMETER Location
    Azure region. Default: centralus

.PARAMETER SapSubnetIds
    Optional SAP VM subnet resource IDs to allow on the storage firewall (so the
    collector can upload from private VMs).

.EXAMPLE
    ./deploy-sre-infra.ps1 -SubscriptionId <sub> -StorageAccountName stsreconfigs001 -SreAgentUmiPrincipalId <agent-mi-object-id>
#>
param(
    [Parameter(Mandatory)] [string]   $SubscriptionId,
    [Parameter(Mandatory)] [string]   $StorageAccountName,
    [string]   $SreAgentUmiPrincipalId,
    [string]   $ResourceGroupName = "rg-sre-agent",
    [string]   $Location          = "centralus",
    [string[]] $SapSubnetIds       = @()
)

$ErrorActionPreference = "Stop"
$RG               = $ResourceGroupName
$CollectorUmiName = "sre-collector-umi"
$Container        = "sap-configs"
$RepoRoot         = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CollectorScript  = Join-Path $RepoRoot "collector\collect-sap-configs.sh"

function Write-Step { param($m) Write-Host "`n>> $m" -ForegroundColor Cyan }
function Write-OK   { param($m) Write-Host "   OK: $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "   WARN: $m" -ForegroundColor Yellow }

az account set --subscription $SubscriptionId

# ── Step 1: Resource group ──
Write-Step "Step 1 — Resource Group"
az group create --name $RG --location $Location --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to create resource group $RG" }
Write-OK "$RG ($Location)"

# ── Step 2: Collector managed identity ──
Write-Step "Step 2 — Collector Managed Identity"
az identity create --name $CollectorUmiName -g $RG --location $Location --output none 2>$null
$collectorUmi = az identity show -n $CollectorUmiName -g $RG -o json | ConvertFrom-Json
$COLLECTOR_UMI_ID           = $collectorUmi.id
$COLLECTOR_UMI_CLIENT_ID    = $collectorUmi.clientId
$COLLECTOR_UMI_PRINCIPAL_ID = $collectorUmi.principalId
if (-not $COLLECTOR_UMI_CLIENT_ID) { throw "Failed to create collector managed identity" }
Write-OK "$CollectorUmiName (Client: $COLLECTOR_UMI_CLIENT_ID)"

# ── Step 3: Storage account (private, Entra-only) ──
Write-Step "Step 3 — Storage Account"
az storage account create --name $StorageAccountName -g $RG -l $Location `
    --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 `
    --allow-shared-key-access false --default-action Deny `
    --bypass AzureServices --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to create storage account '$StorageAccountName' (name may be taken globally)." }
Write-OK "$StorageAccountName created (firewall: Deny + AzureServices bypass, shared keys disabled)"

# Temporarily allow the deployer IP so we can upload the collector script.
$deployerIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 10) 2>$null
if ($deployerIp) {
    az storage account network-rule add --account-name $StorageAccountName --ip-address $deployerIp --output none 2>$null
    Write-OK "Deployer IP ($deployerIp) added to firewall (temporary)"
}
foreach ($sn in $SapSubnetIds) {
    az storage account network-rule add --account-name $StorageAccountName --subnet $sn --output none 2>$null
    Write-OK "SAP subnet allowed on firewall: $sn"
}

$stScope = "/subscriptions/$SubscriptionId/resourceGroups/$RG/providers/Microsoft.Storage/storageAccounts/$StorageAccountName"

# ── Access model (no proxy): collector writes, SRE Agent MI reads ──
az role assignment create --assignee-object-id $COLLECTOR_UMI_PRINCIPAL_ID --assignee-principal-type ServicePrincipal `
    --role "Storage Blob Data Contributor" --scope $stScope --output none 2>$null
Write-OK "Collector UMI -> Storage Blob Data Contributor (write)"

if ($SreAgentUmiPrincipalId) {
    az role assignment create --assignee-object-id $SreAgentUmiPrincipalId --assignee-principal-type ServicePrincipal `
        --role "Storage Blob Data Reader" --scope $stScope --output none 2>$null
    Write-OK "SRE Agent MI -> Storage Blob Data Reader (direct config access, no proxy)"
} else {
    Write-Warn "-SreAgentUmiPrincipalId not provided. Grant the SRE Agent MI Storage Blob Data Reader so"
    Write-Warn "  skills can read configs directly:"
    Write-Host  "     az role assignment create --assignee-object-id <agent-mi> --assignee-principal-type ServicePrincipal --role 'Storage Blob Data Reader' --scope $stScope" -ForegroundColor Gray
}

# Grant the deployer blob access so we can upload the collector script.
$deployerUpn = az account show --query user.name -o tsv
az role assignment create --assignee $deployerUpn --role "Storage Blob Data Owner" --scope $stScope --output none 2>$null

# Create the container via the control plane (no data-plane auth needed).
$ctrUrl = "https://management.azure.com${stScope}/blobServices/default/containers/${Container}?api-version=2023-01-01"
az rest --method PUT --url $ctrUrl --body '{}' --output none 2>$null
Write-OK "Container: $Container"

# Upload the collector script (retry for RBAC propagation, up to ~60s).
if (Test-Path $CollectorScript) {
    for ($retry = 1; $retry -le 6; $retry++) {
        az storage blob upload --account-name $StorageAccountName --container-name $Container `
            --name scripts/collect-sap-configs.sh --file $CollectorScript --auth-mode login --overwrite --output none 2>$null
        if ($LASTEXITCODE -eq 0) { Write-OK "Collector script uploaded"; break }
        Write-Host "   Waiting for RBAC propagation ($retry/6)..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
    }
}

# ── Summary ──
Write-Step "Done — Config Store deployed"
Write-Host ""
Write-Host "  Storage:          $StorageAccountName / container $Container"
Write-Host "  Collector UMI:    $CollectorUmiName (Client: $COLLECTOR_UMI_CLIENT_ID)"
Write-Host "                    Resource ID: $COLLECTOR_UMI_ID"
if ($SreAgentUmiPrincipalId) { Write-Host "  Agent MI reader:  $SreAgentUmiPrincipalId (direct config access)" }
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Assign the collector UMI to your SAP VMs:" -ForegroundColor Yellow
Write-Host "     az vm identity assign -g <SAP-RG> -n <VM> --identities $COLLECTOR_UMI_ID" -ForegroundColor Gray
Write-Host "  2. Run the collector on each VM (az vm run-command) to upload configs." -ForegroundColor Yellow
Write-Host "  3. VNet-integrate the SRE Agent and allow its subnet on this storage firewall." -ForegroundColor Yellow
Write-Host "  4. Install the sap-sre-config plugin. For live commands (optional), deploy the MCP proxy:" -ForegroundColor Yellow
Write-Host "     ./deploy-mcp-proxy.ps1 -SubscriptionId $SubscriptionId -SapResourceGroups <RG,...>" -ForegroundColor Gray
Write-Host ""

# Remove the temporary deployer IP from the storage firewall.
if ($deployerIp) {
    az storage account network-rule remove --account-name $StorageAccountName --ip-address $deployerIp --output none 2>$null
    Write-OK "Deployer IP removed from storage firewall"
}
