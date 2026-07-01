<#
.SYNOPSIS
    SAP SRE Agent — Infrastructure Deployment (mode-aware: AzureNative / ConfigStore / Full)

.DESCRIPTION
    Deploys the infrastructure required by the SAP SRE Agent. Three modes are
    supported:

      AzureNative  — Prints instructions and exits. No Azure infra is created.
                     The agent uses only Azure-native data sources (AMS, Azure
                     Monitor, Resource Graph, Activity Log, Cost Management,
                     ACSS, Advisor). 10 skills functional, no config validation,
                     no live VM commands.

      ConfigStore  — Creates: resource group, collector UMI, storage account
                     with sap-configs container, custom RBAC role definition.
                     Skips the proxy (ACR, VNet, Container Apps env,
                     Container App, Entra ID Easy Auth). 11 skills functional
                     (adds STAF config validation and config-enriched skills).
                     The SRE Agent's own MI is granted Storage Blob Data Reader
                     so ALL config-consuming skills read configs directly from
                     blob — no proxy involved. Pass -SreAgentUmiPrincipalId.

      Full         — Default. Everything in ConfigStore PLUS the OPTIONAL live-command
                     proxy: proxy UMI, ACR + image, VNet, Container Apps env,
                     Container App, Entra ID Easy Auth. The proxy is used ONLY for
                     live VM commands + self-healing — it is NEVER in the config-read
                     path (the agent MI still reads configs directly). All 13 skills.

.PARAMETER SubscriptionId
    Subscription where the resource group will be created. Required in all modes.

.PARAMETER Mode
    Adoption mode. One of: AzureNative, ConfigStore, Full. Default: Full.

.PARAMETER StorageAccountName
    Globally unique storage account name (3-24 chars, lowercase + numbers only).
    Required for ConfigStore and Full modes. Ignored for AzureNative.

.PARAMETER SreAgentUmiPrincipalId
    Object/Principal ID of the SRE Agent's Managed Identity (visible on
    sre.azure.com under Identity). Grant this identity Storage Blob Data Reader on
    the sap-configs container so EVERY config-consuming skill reads configs directly
    from blob. Strongly recommended for both ConfigStore and Full modes. The proxy
    is never used to read configs.

.PARAMETER ResourceGroupName
    Resource group name. Default: rg-sre-proxy

.PARAMETER ProxyName
    Container App name for the SRE proxy. Default: sap-sre-proxy. Full mode only.

.PARAMETER Location
    Azure region. Default: centralus

.PARAMETER VNetName
    Virtual network name. Default: vnet-sre-proxy. Full mode only.

.PARAMETER VNetAddressSpace
    VNet address space (CIDR). Choose a range that does not overlap with
    existing VNets if you plan to peer them later. Default: 10.60.0.0/16.
    Full mode only.

.PARAMETER SubnetName
    Subnet name for Container Apps. Default: sn-container-apps. Full mode only.

.PARAMETER SubnetPrefix
    Subnet address prefix (CIDR, minimum /23 for Container Apps).
    Default: 10.60.0.0/23. Full mode only.

.EXAMPLE
    # Mode 1 — Azure-Native (no infra; prints what to do manually):
    .\deploy-sre-infra.ps1 -Mode AzureNative -SubscriptionId "12345678-..."

.EXAMPLE
    # Mode 2 — Config Store only (no proxy):
    .\deploy-sre-infra.ps1 `
        -Mode ConfigStore `
        -SubscriptionId "12345678-..." `
        -StorageAccountName "stsreconfigs001" `
        -SreAgentUmiPrincipalId "<agent-mi-principal-id>"

.EXAMPLE
    # Mode 3 — Full (proxy + storage + everything) — DEFAULT:
    .\deploy-sre-infra.ps1 `
        -SubscriptionId "12345678-..." `
        -StorageAccountName "stsreconfigs001"
#>

param(
    [Parameter(Mandatory)] [string] $SubscriptionId,
    [ValidateSet('AzureNative','ConfigStore','Full')]
    [string] $Mode              = 'Full',
    [string] $StorageAccountName,
    [string] $SreAgentUmiPrincipalId,
    [string] $ResourceGroupName = "rg-sre-proxy",
    [string] $ProxyName         = "sap-sre-proxy",
    [string] $Location          = "centralus",
    [string] $VNetName          = "vnet-sre-proxy",
    [string] $VNetAddressSpace  = "10.60.0.0/16",
    [string] $SubnetName        = "sn-container-apps",
    [string] $SubnetPrefix      = "10.60.0.0/23",
    [string[]] $SapSubnetIds    = @()
)

$ErrorActionPreference = "Stop"
$RG              = $ResourceGroupName
$ProxyUmiName    = "sre-proxy-umi"
$CollectorUmiName = "sre-collector-umi"
$Container       = "sap-configs"
$EnvName         = "sre-proxy-env"
$AcrName         = "acrsreproxy$($SubscriptionId.Substring(0,8) -replace '-','')"
$RepoRoot        = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ProxyDir        = Join-Path $RepoRoot "proxy"
$CollectorScript = Join-Path $RepoRoot "collector\collect-sap-configs.sh"

function Write-Step  { param($msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "   OK: $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "   WARN: $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " SAP SRE Agent — Infrastructure Deployment" -ForegroundColor Cyan
Write-Host " Mode: $Mode" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Subscription:  $SubscriptionId"
if ($Mode -ne 'AzureNative') {
    Write-Host "  Resource Group: $RG"
    Write-Host "  Storage:       $StorageAccountName"
}
if ($Mode -eq 'Full') {
    Write-Host "  SRE Proxy:     $ProxyName"
    Write-Host "  VNet:          $VNetName ($VNetAddressSpace)"
    Write-Host "  Subnet:        $SubnetName ($SubnetPrefix)"
}
Write-Host "  Location:      $Location"

# ===========================================================================
# Mode 1 — Azure-Native: no infra needed. Print instructions and exit.
# ===========================================================================
if ($Mode -eq 'AzureNative') {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host " MODE 1 (AzureNative) — No infrastructure to deploy" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "In Azure-Native mode the SRE Agent uses only built-in Azure data sources:" -ForegroundColor Yellow
    Write-Host "  - AMS (Azure Monitor for SAP Solutions) telemetry" -ForegroundColor Gray
    Write-Host "  - Azure Monitor metrics and Activity Log" -ForegroundColor Gray
    Write-Host "  - Azure Resource Graph + ARM API" -ForegroundColor Gray
    Write-Host "  - Cost Management, Advisor, Resource Health, ACSS" -ForegroundColor Gray
    Write-Host ""
    Write-Host "There is nothing to deploy. Complete these manual steps instead:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. On sre.azure.com, copy the SRE Agent Managed Identity Object ID." -ForegroundColor Yellow
    Write-Host "  2. Grant the agent MI these built-in roles on each SAP resource group:" -ForegroundColor Yellow
    Write-Host "       - Reader" -ForegroundColor Gray
    Write-Host "       - Monitoring Reader" -ForegroundColor Gray
    Write-Host "       - Cost Management Reader  (subscription scope is fine)" -ForegroundColor Gray
    Write-Host "     Example:" -ForegroundColor Gray
    Write-Host "       az role assignment create --assignee-object-id <agent-mi-object-id> ``" -ForegroundColor Gray
    Write-Host "         --assignee-principal-type ServicePrincipal --role 'Monitoring Reader' ``" -ForegroundColor Gray
    Write-Host "         --scope /subscriptions/$SubscriptionId/resourceGroups/<SAP-RG>" -ForegroundColor Gray
    Write-Host "  3. Import the 10 Mode-1 skills (skip sap-config-validator, sap-command-runner," -ForegroundColor Yellow
    Write-Host "     and sap-self-healing). See README.md Adoption Modes table for the full list." -ForegroundColor Yellow
    Write-Host "  4. Paste onboarding/team-onboarding.template.md into Team Onboarding and set" -ForegroundColor Yellow
    Write-Host "     '## Deployment Mode' to 'Mode 1 (AzureNative)'." -ForegroundColor Yellow
    Write-Host "  5. Re-run this script with -Mode ConfigStore or -Mode Full to upgrade later." -ForegroundColor Yellow
    Write-Host ""
    return
}

# Validate StorageAccountName for modes that need it
if (-not $StorageAccountName) {
    throw "-StorageAccountName is required for -Mode $Mode. (Only -Mode AzureNative skips storage.)"
}

# ── Prerequisites ──
Write-Step "Prerequisites"

if ($StorageAccountName -notmatch '^[a-z0-9]{3,24}$') {
    throw "StorageAccountName must be 3-24 characters, lowercase letters and numbers only."
}
if (-not (Test-Path (Join-Path $ProxyDir "Dockerfile"))) {
    throw "proxy/Dockerfile not found. Run this script from the repo root or infra/ directory."
}

$azVersion = (az version -o json 2>$null | ConvertFrom-Json).'azure-cli'
if (-not $azVersion) { throw "az CLI not found. Install: https://aka.ms/installazurecli" }
Write-OK "az CLI $azVersion"

az account set --subscription $SubscriptionId
if ($LASTEXITCODE -ne 0) { throw "Failed to set subscription. Run 'az login' first." }
$acctJson = az account show -o json 2>$null
if (-not $acctJson) { throw "Not logged in. Run 'az login' first." }
$acct = $acctJson | ConvertFrom-Json
$deployerUpn = $acct.user.name
Write-OK "Logged in as $deployerUpn"

# ── Step 1: Resource Group ──
Write-Step "Step 1 — Resource Group"
az group create --name $RG --location $Location --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to create resource group $RG" }
Write-OK "$RG ($Location)"

# ── Step 2: Managed Identities ──
Write-Step "Step 2 — Managed Identities"

# Proxy UMI — used by Container App for ARM API calls and blob access (Full mode only)
if ($Mode -eq 'Full') {
    az identity create --name $ProxyUmiName -g $RG --location $Location --output none 2>$null
    $proxyUmiJson = az identity show -n $ProxyUmiName -g $RG -o json | ConvertFrom-Json
    $PROXY_UMI_ID = $proxyUmiJson.id
    $PROXY_UMI_CLIENT_ID = $proxyUmiJson.clientId
    $PROXY_UMI_PRINCIPAL_ID = $proxyUmiJson.principalId
    if (-not $PROXY_UMI_CLIENT_ID) { throw "Failed to create proxy managed identity" }
    Write-OK "$ProxyUmiName (Client: $PROXY_UMI_CLIENT_ID)"
} else {
    Write-OK "Skipping proxy UMI (Mode=$Mode)"
}

# Collector UMI — assigned to SAP VMs for config upload to blob storage (both ConfigStore and Full)
az identity create --name $CollectorUmiName -g $RG --location $Location --output none 2>$null
$collectorUmiJson = az identity show -n $CollectorUmiName -g $RG -o json | ConvertFrom-Json
$COLLECTOR_UMI_ID = $collectorUmiJson.id
$COLLECTOR_UMI_CLIENT_ID = $collectorUmiJson.clientId
$COLLECTOR_UMI_PRINCIPAL_ID = $collectorUmiJson.principalId
if (-not $COLLECTOR_UMI_CLIENT_ID) { throw "Failed to create collector managed identity" }
Write-OK "$CollectorUmiName (Client: $COLLECTOR_UMI_CLIENT_ID)"

# ── Step 3: Storage Account ──
Write-Step "Step 3 — Storage Account"

az storage account create --name $StorageAccountName -g $RG -l $Location `
    --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 `
    --allow-shared-key-access false --default-action Deny `
    --bypass AzureServices --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to create storage account '$StorageAccountName'. Name may already be taken globally." }
Write-OK "$StorageAccountName created (firewall: Deny + AzureServices bypass)"

# Temporarily allow deployer IP for uploads
$deployerIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 10) 2>$null
if ($deployerIp) {
    az storage account network-rule add --account-name $StorageAccountName --ip-address $deployerIp --output none 2>$null
    Write-OK "Deployer IP ($deployerIp) added to firewall (temporary)"
}

$stScope = "/subscriptions/$SubscriptionId/resourceGroups/$RG/providers/Microsoft.Storage/storageAccounts/$StorageAccountName"

# ── Config-store access model (decoupled from the proxy) ──
# The SRE Agent reads configs DIRECTLY from blob using its OWN managed identity.
# The proxy is NEVER in the config-read path (it is optional and only runs live VM
# commands). So on the storage account:
#   • Collector UMI  -> Storage Blob Data Contributor (writes configs from the VMs)
#   • SRE Agent MI   -> Storage Blob Data Reader       (reads configs directly)
#   • Proxy UMI      -> NO storage role (commands only)

# Assign storage RBAC to collector UMI (upload configs from SAP VMs)
az role assignment create --assignee-object-id $COLLECTOR_UMI_PRINCIPAL_ID --assignee-principal-type ServicePrincipal `
    --role "Storage Blob Data Contributor" --scope $stScope --output none 2>$null
Write-OK "Collector UMI storage role assigned (Storage Blob Data Contributor — write)"

# Grant the SRE Agent MI direct Storage Blob Data Reader. This is REQUIRED whenever a
# config store is deployed (both ConfigStore and Full) — every config-consuming skill
# reads blobs directly via the agent's own identity, not through the proxy.
if ($SreAgentUmiPrincipalId) {
    az role assignment create --assignee-object-id $SreAgentUmiPrincipalId --assignee-principal-type ServicePrincipal `
        --role "Storage Blob Data Reader" --scope $stScope --output none 2>$null
    Write-OK "SRE Agent MI granted Storage Blob Data Reader (direct config access — no proxy)"
} else {
    Write-Warn "-SreAgentUmiPrincipalId not provided. The SRE Agent MI needs Storage Blob Data"
    Write-Warn "  Reader on the sap-configs container to read configs directly. Re-run with"
    Write-Warn "  -SreAgentUmiPrincipalId <agent-mi-object-id>, or assign it manually:"
    Write-Host "     az role assignment create --assignee-object-id <agent-mi> ``" -ForegroundColor Gray
    Write-Host "       --assignee-principal-type ServicePrincipal ``" -ForegroundColor Gray
    Write-Host "       --role 'Storage Blob Data Reader' --scope $stScope" -ForegroundColor Gray
}

# Assign deployer blob access
az role assignment create --assignee $deployerUpn --role "Storage Blob Data Owner" --scope $stScope --output none
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Failed to assign Storage Blob Data Owner to deployer ($deployerUpn)."
    Write-Warn "You may need to assign this role manually."
} else {
    Write-OK "Deployer ($deployerUpn) storage role assigned"
}

# Create container via ARM control plane (no data-plane auth needed)
$ctrUrl = "https://management.azure.com${stScope}/blobServices/default/containers/${Container}?api-version=2023-01-01"
az rest --method PUT --url $ctrUrl --body '{}' --output none 2>$null
Write-OK "Container: $Container"

# Upload collector script (retry loop for RBAC propagation — up to 60s)
if (Test-Path $CollectorScript) {
    $uploaded = $false
    for ($retry = 1; $retry -le 6; $retry++) {
        az storage blob upload --account-name $StorageAccountName --container-name $Container `
            --name scripts/collect-sap-configs.sh --file $CollectorScript `
            --auth-mode login --overwrite --output none 2>$null
        if ($LASTEXITCODE -eq 0) { $uploaded = $true; break }
        Write-Host "   Waiting for RBAC propagation ($retry/6)..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
    }
    if ($uploaded) { Write-OK "Collector script uploaded" }
    else { Write-Warn "Collector upload deferred — run after RBAC propagates" }
}

# ===========================================================================
# Mode 3 — Full: proceed with proxy stack (ACR, VNet, Container Apps, Auth)
# Mode 2 — ConfigStore: skip Steps 4–8, jump to custom RBAC + summary
# ===========================================================================
if ($Mode -eq 'ConfigStore') {
    Write-Step "Skipping Steps 4–8 (proxy infrastructure) — Mode=ConfigStore"
    Write-Host "   Mode 2 does not deploy ACR, VNet, Container Apps env, Container App, or Entra ID." -ForegroundColor Gray
    Write-Host "   The Config Validator skill reads configs directly from blob via the SRE Agent UMI." -ForegroundColor Gray
}

if ($Mode -eq 'Full') {

# ── Step 4: Container Registry ──
Write-Step "Step 4 — Container Registry"
# Premium SKU required for private endpoint support in MCAP
az acr create --name $AcrName -g $RG -l $Location --sku Premium --admin-enabled false --output none 2>$null
if ($LASTEXITCODE -ne 0) { throw "Failed to create ACR '$AcrName'. Name may already be taken." }
Write-OK "$AcrName (Basic)"

# Grant UMI pull access to ACR
$acrScope = "/subscriptions/$SubscriptionId/resourceGroups/$RG/providers/Microsoft.ContainerRegistry/registries/$AcrName"
az role assignment create --assignee-object-id $PROXY_UMI_PRINCIPAL_ID --assignee-principal-type ServicePrincipal `
    --role AcrPull --scope $acrScope --output none 2>$null
Write-OK "UMI AcrPull role assigned"

# Build container image in ACR (copy collector script into build context first)
Write-Host "   Building container image in ACR..." -ForegroundColor Gray
if (Test-Path $CollectorScript) {
    Copy-Item $CollectorScript (Join-Path $ProxyDir "collect-sap-configs.sh") -Force
    Write-OK "Collector script copied to build context"
} else {
    Write-Warn "Collector script not found at $CollectorScript — deploy_collector will not work"
}
az acr build --registry $AcrName -t sre-proxy:latest $ProxyDir --no-logs --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to build container image" }
Write-OK "Image built: $AcrName.azurecr.io/sre-proxy:latest"

# ── Step 5: VNet + Subnet ──
Write-Step "Step 5 — VNet + Subnet"
az network vnet create -n $VNetName -g $RG -l $Location `
    --address-prefix $VNetAddressSpace `
    --subnet-name $SubnetName --subnet-prefix $SubnetPrefix `
    --output none 2>$null
if ($LASTEXITCODE -ne 0) { throw "Failed to create VNet '$VNetName'" }
Write-OK "$VNetName ($VNetAddressSpace)"

# Delegate subnet to Container Apps + add Storage service endpoint
az network vnet subnet update -n $SubnetName --vnet-name $VNetName -g $RG `
    --delegations Microsoft.App/environments `
    --service-endpoints Microsoft.Storage `
    --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to configure subnet '$SubnetName'" }
Write-OK "$SubnetName ($SubnetPrefix) — delegated + Storage endpoint"

# Add proxy subnet to storage firewall
$subnetId = az network vnet subnet show -n $SubnetName --vnet-name $VNetName -g $RG --query id -o tsv
az storage account network-rule add --account-name $StorageAccountName --subnet $subnetId --output none
Write-OK "Proxy subnet added to storage firewall"

# Add SAP VM subnets to storage firewall (so collector can upload configs)
if ($SapSubnetIds.Count -gt 0) {
    foreach ($sapSubnet in $SapSubnetIds) {
        az storage account network-rule add --account-name $StorageAccountName --subnet $sapSubnet --output none 2>$null
        Write-OK "SAP subnet added to storage firewall: $($sapSubnet.Split('/')[-1])"
    }
} else {
    Write-Warn "No -SapSubnetIds provided. Add SAP VM subnets manually:"
    Write-Host "     az storage account network-rule add --account-name $StorageAccountName --subnet <sap-subnet-id>" -ForegroundColor Gray
}

# ── Step 6: Container Apps Environment ──
Write-Step "Step 6 — Container Apps Environment"
az containerapp env create --name $EnvName -g $RG -l $Location `
    --infrastructure-subnet-resource-id $subnetId `
    --output none 2>$null
if ($LASTEXITCODE -ne 0) { throw "Failed to create Container Apps Environment" }
Write-OK "$EnvName (VNet-integrated)"

# ── Step 7: Container App ──
Write-Step "Step 7 — Deploy Container App"
$API_KEY = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N").Substring(0,16)

az containerapp create --name $ProxyName -g $RG `
    --environment $EnvName `
    --image "$AcrName.azurecr.io/sre-proxy:latest" `
    --registry-server "$AcrName.azurecr.io" `
    --registry-identity $PROXY_UMI_ID `
    --user-assigned $PROXY_UMI_ID `
    --ingress external --target-port 8000 `
    --min-replicas 0 --max-replicas 3 `
    --cpu 0.5 --memory 1.0Gi `
    --env-vars `
        AZURE_CLIENT_ID=$PROXY_UMI_CLIENT_ID `
        STORAGE_ACCOUNT_NAME=$StorageAccountName `
        CONTAINER_NAME=$Container `
        SUBSCRIPTION_ID=$SubscriptionId `
        AGENT_KEY_sre1=$API_KEY `
    --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to create Container App '$ProxyName'" }

$fqdn = az containerapp show -n $ProxyName -g $RG --query "properties.configuration.ingress.fqdn" -o tsv
Write-OK "$ProxyName deployed (https://$fqdn)"

# ── Step 8: Entra ID Authentication ──
Write-Step "Step 8 — Entra ID Authentication"

# Get tenant ID
$tenantId = az account show --query tenantId -o tsv

# Create App Registration for the proxy
$existingApp = az ad app list --display-name "SAP SRE Proxy - $RG" --query "[0].appId" -o tsv 2>$null
if ($existingApp) {
    $ProxyAppId = $existingApp
    Write-OK "App Registration exists: $ProxyAppId"
} else {
    $appJson = az ad app create --display-name "SAP SRE Proxy - $RG" `
        --sign-in-audience AzureADMyOrg `
        --identifier-uris "api://sap-sre-proxy-$($SubscriptionId.Substring(0,8))" `
        -o json
    $ProxyAppId = ($appJson | ConvertFrom-Json).appId
    if (-not $ProxyAppId) { throw "Failed to create App Registration" }
    # Create Service Principal for the App Registration
    az ad sp create --id $ProxyAppId --output none 2>$null
    Write-OK "App Registration created: $ProxyAppId"
}

# Enable Easy Auth on Container App
az containerapp auth microsoft update `
    --name $ProxyName -g $RG `
    --client-id $ProxyAppId `
    --issuer "https://login.microsoftonline.com/$tenantId/v2.0" `
    --yes --output none 2>$null

az containerapp auth update `
    --name $ProxyName -g $RG `
    --unauthenticated-client-action Return401 `
    --enabled true --output none 2>$null

Write-OK "Entra ID authentication enabled (unauthenticated requests return 401)"
Write-Host "   SRE Agent MI must acquire a token for audience: api://sap-sre-proxy-$($SubscriptionId.Substring(0,8))" -ForegroundColor Gray
Write-Host "   Health endpoint (/api/health) is excluded from auth for probes" -ForegroundColor Gray

} # end if ($Mode -eq 'Full')

# ── Step 9: Custom RBAC Role (Mode 3 only — proxy needs it) ──
if ($Mode -eq 'Full') {
Write-Step "Step 9 — Custom RBAC Role"

$roleName = "Custom - SAP SRE Agent Operator"
$existingRole = az role definition list --name $roleName --query "[0].id" -o tsv 2>$null
if ($existingRole) {
    Write-OK "Custom role '$roleName' already exists"
} else {
    $roleFile = Join-Path $RepoRoot "infra\sap-sre-agent-role.json"
    if (Test-Path $roleFile) {
        $roleContent = Get-Content $roleFile -Raw
        $roleContent = $roleContent.Replace("<YOUR-SUBSCRIPTION-ID>", $SubscriptionId)
        $roleContent | Set-Content $roleFile
        az role definition create --role-definition $roleFile --output none 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-OK "Custom role '$roleName' created"
        } else {
            Write-Warn "Custom role creation failed — create manually from infra/sap-sre-agent-role.json"
        }
    } else {
        Write-Warn "Role definition file not found: $roleFile"
    }
}
} # end if ($Mode -eq 'Full') for Step 9

# ── Step 10: Summary ──
Write-Step "Step 10 — Complete"
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " DEPLOYMENT COMPLETE — Mode: $Mode" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""

if ($Mode -eq 'Full') {
    Write-Host "  SRE Proxy:          https://$fqdn"
    Write-Host "  API Key:            $API_KEY"
    Write-Host "  Proxy App ID:       $ProxyAppId  (Entra ID Easy Auth audience: api://sap-sre-proxy-$($SubscriptionId.Substring(0,8)))"
    Write-Host "  Proxy UMI:          $ProxyUmiName (Client: $PROXY_UMI_CLIENT_ID, Principal: $PROXY_UMI_PRINCIPAL_ID)"
    Write-Host "  VNet:               $VNetName ($VNetAddressSpace)"
    Write-Host "  Custom RBAC:        $roleName"
}
Write-Host "  Collector UMI:      $CollectorUmiName (Client: $COLLECTOR_UMI_CLIENT_ID, Resource: $COLLECTOR_UMI_ID)"
Write-Host "  Storage:            $StorageAccountName"
if ($SreAgentUmiPrincipalId) {
    Write-Host "  Agent UMI direct:   $SreAgentUmiPrincipalId (Storage Blob Data Reader — direct config access)"
}
Write-Host ""

Write-Host "Next Steps:" -ForegroundColor Yellow
if ($Mode -eq 'Full') {
    Write-Host "  1. Grant proxy UMI access to SAP resource groups:" -ForegroundColor Yellow
    Write-Host "     az role assignment create --assignee-object-id $PROXY_UMI_PRINCIPAL_ID --assignee-principal-type ServicePrincipal --role `"$roleName`" --scope /subscriptions/$SubscriptionId/resourceGroups/<SAP-RG>" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  2. Assign collector UMI to SAP VMs:" -ForegroundColor Yellow
    Write-Host "     az vm identity assign -g <SAP-RG> -n <VM-NAME> --identities $COLLECTOR_UMI_ID" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  3. Deploy collector to SAP VMs via proxy:" -ForegroundColor Yellow
    Write-Host "     POST $fqdn/api/command with command_id=deploy_collector, storage_account=$StorageAccountName, umi_client_id=$COLLECTOR_UMI_CLIENT_ID" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  4. SRE Agent portal (sre.azure.com):" -ForegroundColor Yellow
    Write-Host "     - Import all 13 skills via Skill Builder" -ForegroundColor Yellow
    Write-Host "     - Add managed resources (SAP RGs + $RG)" -ForegroundColor Yellow
    Write-Host "     - Upload sap-landscape-inventory.json as Knowledge Source" -ForegroundColor Yellow
    Write-Host "     - Paste team onboarding with proxy URL + API key + Mode 3 declaration" -ForegroundColor Yellow
} else {
    # ConfigStore (Mode 2)
    Write-Host "  1. Assign collector UMI to SAP VMs:" -ForegroundColor Yellow
    Write-Host "     az vm identity assign -g <SAP-RG> -n <VM-NAME> --identities $COLLECTOR_UMI_ID" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  2. Deploy collector script to each SAP VM (use az vm run-command or your config-mgmt tool):" -ForegroundColor Yellow
    Write-Host "     The collector script is at collector/collect-sap-configs.sh and uploaded to" -ForegroundColor Gray
    Write-Host "     blob: $StorageAccountName/$Container/scripts/collect-sap-configs.sh" -ForegroundColor Gray
    Write-Host ""
    if (-not $SreAgentUmiPrincipalId) {
        Write-Host "  3. Grant SRE Agent UMI Storage Blob Data Reader (required for Config Validator):" -ForegroundColor Yellow
        Write-Host "     az role assignment create --assignee-object-id <agent-mi-object-id> --assignee-principal-type ServicePrincipal --role 'Storage Blob Data Reader' --scope $stScope" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  4. SRE Agent portal (sre.azure.com):" -ForegroundColor Yellow
    } else {
        Write-Host "  3. SRE Agent portal (sre.azure.com):" -ForegroundColor Yellow
    }
    Write-Host "     - Grant agent MI 'Reader' + 'Monitoring Reader' on each SAP RG" -ForegroundColor Yellow
    Write-Host "     - Import 11 skills (skip sap-command-runner and sap-self-healing — Mode 3 only)" -ForegroundColor Yellow
    Write-Host "     - Add managed resources (SAP RGs + $RG)" -ForegroundColor Yellow
    Write-Host "     - Upload sap-landscape-inventory.json as Knowledge Source" -ForegroundColor Yellow
    Write-Host "     - Paste team onboarding with Mode 2 declaration + storage account name" -ForegroundColor Yellow
}
Write-Host ""

# Clean up deployer IP from storage firewall
if ($deployerIp) {
    az storage account network-rule remove --account-name $StorageAccountName --ip-address $deployerIp --output none 2>$null
    Write-OK "Deployer IP removed from storage firewall"
}
