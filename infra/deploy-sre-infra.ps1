<#
.SYNOPSIS
    SAP SRE Agent — One-Step Infrastructure Deployment

.DESCRIPTION
    Creates RG_SRE_OPS with: Storage Account, Managed Identity, Function App (unified proxy).
    Builds and deploys function code via Run From Package, locks down storage, and prints next steps.

    Uses Run From Package deployment because enterprise policies often disable storage shared-key
    access, which breaks all other deployment methods (func publish, OneDeploy, SCM/Kudu).

.PARAMETER SubscriptionId
    Subscription where RG_SRE_OPS will be created.

.PARAMETER StorageAccountName
    Globally unique storage account name (3-24 chars, lowercase + numbers only).

.PARAMETER IntegrationSubnetId
    Full resource ID of the subnet for function app VNet integration.
    Must be delegated to Microsoft.Web/serverFarms.
    Format: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>

.PARAMETER ProxyName
    Function app name for the SRE proxy. Must be globally unique.
    Default: sap-sre-proxy-<8-char-sub-prefix>

.EXAMPLE
    .\deploy-sre-infra.ps1 `
        -SubscriptionId "12345678-..." `
        -StorageAccountName "stsreconfigs001" `
        -IntegrationSubnetId "/subscriptions/.../subnets/IntegrationSubnet"
#>

param(
    [Parameter(Mandatory)] [string] $SubscriptionId,
    [Parameter(Mandatory)] [string] $StorageAccountName,
    [Parameter(Mandatory)] [string] $IntegrationSubnetId,
    [string] $ProxyName
)

$ErrorActionPreference = "Stop"
$RG         = "RG_SRE_OPS"
$UmiName    = "sre-ops-umi"
$Plan       = "sre-ops-plan"
$Container  = "sap-configs"
$DeployContainer = "deploys"
$Location   = "centralus"
$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ProxyDir   = Join-Path $RepoRoot "proxy"
$CollectorScript = Join-Path $RepoRoot "collector\collect-sap-configs.sh"

# Auto-generate globally unique function app name
$suffix = $SubscriptionId.Substring(0,8)
if (-not $ProxyName) { $ProxyName = "sap-sre-proxy-$suffix" }

function Write-Step  { param($msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "   OK: $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "   WARN: $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "   ERROR: $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " SAP SRE Agent — Infrastructure Deployment" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Subscription:  $SubscriptionId"
Write-Host "  Storage:       $StorageAccountName"
Write-Host "  SRE Proxy:     $ProxyName"
Write-Host "  Subnet:        $($IntegrationSubnetId.Split('/')[-1])"

# ── Prerequisites ──
Write-Step "Prerequisites"

if ($IntegrationSubnetId -notmatch '^/subscriptions/.+/subnets/.+$') {
    throw "IntegrationSubnetId must be a full resource ID: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>"
}
if ($StorageAccountName -notmatch '^[a-z0-9]{3,24}$') {
    throw "StorageAccountName must be 3-24 characters, lowercase letters and numbers only."
}
if (-not (Test-Path (Join-Path $ProxyDir "function_app.py"))) {
    throw "proxy/function_app.py not found. Run this script from the repo root or infra/ directory."
}

$azVersion = (az version -o json 2>$null | ConvertFrom-Json).'azure-cli'
if (-not $azVersion) { throw "az CLI not found. Install: https://aka.ms/installazurecli" }
Write-OK "az CLI $azVersion"

# Python is required for pip install of Linux wheels
$pythonCmd = $null
foreach ($cmd in @("python3", "python")) {
    try { $v = & $cmd --version 2>&1; if ($v -match "Python 3") { $pythonCmd = $cmd; break } } catch {}
}
if (-not $pythonCmd) { throw "Python 3 not found. Install Python 3.11+ from https://python.org" }
Write-OK "$pythonCmd ($( & $pythonCmd --version 2>&1))"

az account set --subscription $SubscriptionId
if ($LASTEXITCODE -ne 0) { throw "Failed to set subscription. Run 'az login' first." }
$acctJson = az account show -o json 2>$null
if (-not $acctJson) { throw "Not logged in. Run 'az login' first." }
$acct = $acctJson | ConvertFrom-Json
$deployerUpn = $acct.user.name
Write-OK "Logged in as $deployerUpn"

# ── Step 1: Resource Group ──
Write-Step "Step 1/9 — Resource Group"
az group create --name $RG --location $Location --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to create resource group $RG" }
Write-OK "$RG ($Location)"

# ── Step 2: Managed Identity ──
Write-Step "Step 2/9 — Managed Identity"
az identity create --name $UmiName -g $RG --location $Location --output none 2>$null
$umiJson = az identity show -n $UmiName -g $RG -o json | ConvertFrom-Json
$UMI_ID = $umiJson.id
$UMI_CLIENT_ID = $umiJson.clientId
$UMI_PRINCIPAL_ID = $umiJson.principalId
if (-not $UMI_CLIENT_ID) { throw "Failed to create managed identity" }
Write-OK "$UmiName (Client: $UMI_CLIENT_ID)"

# ── Step 3: Storage Account ──
Write-Step "Step 3/9 — Storage Account"

az storage account create --name $StorageAccountName -g $RG -l $Location `
    --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 `
    --allow-shared-key-access false --default-action Allow --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to create storage account '$StorageAccountName'. Name may already be taken globally." }
Write-OK "$StorageAccountName created"

# Assign storage RBAC to UMI
$stScope = "/subscriptions/$SubscriptionId/resourceGroups/$RG/providers/Microsoft.Storage/storageAccounts/$StorageAccountName"
foreach ($role in @("Storage Blob Data Owner", "Storage Blob Data Contributor", "Storage Queue Data Contributor", "Storage Table Data Contributor")) {
    az role assignment create --assignee-object-id $UMI_PRINCIPAL_ID --assignee-principal-type ServicePrincipal `
        --role $role --scope $stScope --output none 2>$null
}
Write-OK "UMI storage roles assigned"

# Assign deployer blob access — do NOT swallow stderr (2>$null)
# This is critical: if this fails silently, Run From Package upload will fail later
az role assignment create --assignee $deployerUpn --role "Storage Blob Data Owner" --scope $stScope --output none
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Failed to assign Storage Blob Data Owner to deployer ($deployerUpn)."
    Write-Warn "You may need to assign this role manually before the deploy step."
} else {
    Write-OK "Deployer ($deployerUpn) storage role assigned"
}

# Create containers via ARM control plane (no data-plane auth needed)
foreach ($ctr in @($Container, $DeployContainer)) {
    $ctrUrl = "https://management.azure.com${stScope}/blobServices/default/containers/${ctr}?api-version=2023-01-01"
    az rest --method PUT --url $ctrUrl --body '{}' --output none 2>$null
}
Write-OK "Containers: $Container, $DeployContainer"

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

# ── Step 4: App Service Plan ──
Write-Step "Step 4/9 — App Service Plan"
az appservice plan create --name $Plan -g $RG -l $Location --sku B1 --is-linux --output none
if ($LASTEXITCODE -ne 0) { throw "Failed to create App Service Plan" }
Write-OK "$Plan (B1 Linux)"

# ── Step 5: Function App ──
Write-Step "Step 5/9 — Function App"
$API_KEY = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N").Substring(0,16)

az functionapp create --name $ProxyName -g $RG --plan $Plan `
    --runtime python --runtime-version 3.11 --functions-version 4 `
    --os-type Linux --storage-account $StorageAccountName --output none
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create function app '$ProxyName'. Name must be globally unique.`nTry: -ProxyName 'myorg-sre-proxy'"
}
$check = az functionapp show -n $ProxyName -g $RG --query name -o tsv 2>$null
if ($check -ne $ProxyName) { throw "Function app '$ProxyName' not found after creation." }
Write-OK "$ProxyName"

# ── Step 6: Configure Function App ──
Write-Step "Step 6/9 — Configure Function App"

# Assign UMI FIRST (required before identity-based storage or Run From Package)
az functionapp identity assign -n $ProxyName -g $RG --identities $UMI_ID -o none
if ($LASTEXITCODE -ne 0) { throw "Failed to assign managed identity to $ProxyName" }
Write-OK "UMI assigned"

# Switch to identity-based storage
az functionapp config appsettings delete -n $ProxyName -g $RG --setting-names AzureWebJobsStorage -o none 2>$null
az functionapp config appsettings set -n $ProxyName -g $RG --output none --settings `
    AzureWebJobsStorage__accountName=$StorageAccountName `
    AzureWebJobsStorage__blobServiceUri="https://$StorageAccountName.blob.core.windows.net" `
    AzureWebJobsStorage__queueServiceUri="https://$StorageAccountName.queue.core.windows.net" `
    AzureWebJobsStorage__tableServiceUri="https://$StorageAccountName.table.core.windows.net" `
    AzureWebJobsStorage__credential=managedidentity `
    AzureWebJobsStorage__clientId=$UMI_CLIENT_ID `
    AzureWebJobsSecretStorageType=files `
    AZURE_CLIENT_ID=$UMI_CLIENT_ID `
    STORAGE_ACCOUNT_NAME=$StorageAccountName `
    CONTAINER_NAME=$Container `
    SUBSCRIPTION_ID=$SubscriptionId `
    AGENT_KEY_sre1=$API_KEY
if ($LASTEXITCODE -ne 0) { throw "Failed to configure app settings for $ProxyName" }

az functionapp config set -n $ProxyName -g $RG --always-on true --output none
Write-OK "App settings configured"

# ── Step 7: Build & Deploy via Run From Package ──
Write-Step "Step 7/9 — Build & Deploy (Run From Package)"
Write-Host "   Enterprise environments often disable storage shared-key access," -ForegroundColor Gray
Write-Host "   which breaks func publish and OneDeploy. Run From Package is the" -ForegroundColor Gray
Write-Host "   only reliable deployment method in these environments." -ForegroundColor Gray

$buildDir = Join-Path ([System.IO.Path]::GetTempPath()) "sre-proxy-build"
$zipPath  = Join-Path ([System.IO.Path]::GetTempPath()) "sre-proxy.zip"

# Clean previous build
if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
New-Item -ItemType Directory -Path $buildDir -Force | Out-Null

# Copy proxy source files
Copy-Item (Join-Path $ProxyDir "function_app.py") $buildDir
Copy-Item (Join-Path $ProxyDir "host.json") $buildDir
Copy-Item (Join-Path $ProxyDir "requirements.txt") $buildDir

# Install Python dependencies (Linux wheels for Azure Functions)
Write-Host "   Installing Python dependencies (Linux wheels)..." -ForegroundColor Gray
$pkgDir = Join-Path $buildDir ".python_packages" "lib" "site-packages"
& $pythonCmd -m pip install -r (Join-Path $ProxyDir "requirements.txt") `
    --target $pkgDir --platform manylinux2014_x86_64 --only-binary=:all: `
    --python-version 3.11 --quiet 2>&1 | Out-Null
if (-not (Test-Path (Join-Path $pkgDir "azure"))) {
    throw "pip install failed — azure packages not found in $pkgDir"
}
Write-OK "Dependencies installed"

# Create zip
if (Test-Path $zipPath) { Remove-Item $zipPath }
Push-Location $buildDir
Compress-Archive -Path * -DestinationPath $zipPath -Force
Pop-Location
$zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 2)
Write-OK "Package built: $zipSize MB"

# Upload zip to deploys container (retry for RBAC propagation)
$blobName = "sre-proxy.zip"
$uploaded = $false
for ($retry = 1; $retry -le 6; $retry++) {
    az storage blob upload --account-name $StorageAccountName --container-name $DeployContainer `
        --name $blobName --file $zipPath --auth-mode login --overwrite --output none 2>$null
    if ($LASTEXITCODE -eq 0) { $uploaded = $true; break }
    Write-Host "   Waiting for deployer RBAC propagation ($retry/6)..." -ForegroundColor Gray
    Start-Sleep -Seconds 10
}
if (-not $uploaded) {
    throw "Failed to upload deployment zip. Ensure you have Storage Blob Data Owner on $StorageAccountName."
}
Write-OK "Package uploaded to $DeployContainer/$blobName"

# Set Run From Package app settings
$blobUrl = "https://$StorageAccountName.blob.core.windows.net/$DeployContainer/$blobName"
az functionapp config appsettings set -n $ProxyName -g $RG --output none --settings `
    WEBSITE_RUN_FROM_PACKAGE=$blobUrl `
    WEBSITE_RUN_FROM_PACKAGE_BLOB_MI_RESOURCE_ID=$UMI_ID
if ($LASTEXITCODE -ne 0) { throw "Failed to set Run From Package settings" }
Write-OK "Run From Package configured"

# Restart to pick up new package
az functionapp restart -n $ProxyName -g $RG --output none
Write-Host "   Waiting for function app to start..." -ForegroundColor Gray
Start-Sleep -Seconds 30

# Verify functions loaded
$funcs = az functionapp function list -n $ProxyName -g $RG --query "[].name" -o tsv 2>$null
$funcCount = ($funcs -split "`n" | Where-Object { $_.Trim() }).Count
if ($funcCount -ge 7) {
    Write-OK "$funcCount functions loaded"
} else {
    Write-Warn "Only $funcCount functions detected (expected 7). May need more startup time."
    Write-Warn "Verify: az functionapp function list -n $ProxyName -g RG_SRE_OPS --query `"[].name`" -o tsv"
}

# Clean up temp files
Remove-Item -Recurse -Force $buildDir -ErrorAction SilentlyContinue
Remove-Item $zipPath -ErrorAction SilentlyContinue

# ── Step 8: VNet Integration & Storage Lockdown ──
Write-Step "Step 8/9 — VNet Integration & Storage Lockdown"

# Use full subnet resource ID (VNet may be in a different RG than the function app)
az functionapp vnet-integration add -n $ProxyName -g $RG `
    --vnet $IntegrationSubnetId.Split("/subnets/")[0] `
    --subnet $IntegrationSubnetId.Split("/")[-1] --output none 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "VNet integration may have failed. Verify manually."
} else {
    Write-OK "VNet integration added"
}

az storage account update --name $StorageAccountName -g $RG --default-action Deny --output none
if ($LASTEXITCODE -ne 0) { Write-Warn "Failed to set storage firewall to Deny" }
else { Write-OK "Storage firewall set to Deny" }

az storage account network-rule add --account-name $StorageAccountName `
    --subnet $IntegrationSubnetId --output none 2>$null
Write-OK "Firewall: IntegrationSubnet allowed"

# Allow Azure trusted services (needed for Run From Package blob download)
az storage account update --name $StorageAccountName -g $RG --bypass AzureServices --output none 2>$null
Write-OK "Firewall: Azure trusted services bypass enabled"

# ── Step 9: Summary ──
Write-Step "Step 9/9 — Complete"
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host " DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  SRE Proxy:      https://$ProxyName.azurewebsites.net/api"
Write-Host "  API Key:        $API_KEY"
Write-Host "  UMI Client ID:  $UMI_CLIENT_ID"
Write-Host "  UMI Principal:  $UMI_PRINCIPAL_ID"
Write-Host "  UMI Resource:   $UMI_ID"
Write-Host "  Storage:        $StorageAccountName"
Write-Host ""
Write-Host "API Endpoints:" -ForegroundColor Yellow
Write-Host "  GET  /api/registry                          Landscape inventory"
Write-Host "  GET  /api/config/{sid}/{hostname}/{path}    Single config file"
Write-Host "  GET  /api/configs/{sid}/{hostname}          All configs for a VM"
Write-Host "  GET  /api/configs/{sid}                     All configs for a system"
Write-Host "  GET  /api/commands                          List allowed commands"
Write-Host "  GET  /api/diag                              MI + ARM connectivity test"
Write-Host "  POST /api/command                           Execute command on VM"
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Assign RBAC on each SAP resource group:" -ForegroundColor Yellow
Write-Host "     az role assignment create --assignee-object-id $UMI_PRINCIPAL_ID --assignee-principal-type ServicePrincipal --role Reader --scope /subscriptions/<sap-sub>/resourceGroups/<sap-rg>"
Write-Host "     az role assignment create --assignee-object-id $UMI_PRINCIPAL_ID --assignee-principal-type ServicePrincipal --role `"Virtual Machine Contributor`" --scope /subscriptions/<sap-sub>/resourceGroups/<sap-rg>"
Write-Host ""
Write-Host "  2. Add SAP VM subnet(s) to storage firewall:" -ForegroundColor Yellow
Write-Host "     az storage account network-rule add --account-name $StorageAccountName --subnet <sap-vm-subnet-resource-id>"
Write-Host ""
Write-Host "  3. SRE Agent portal (sre.azure.com):" -ForegroundColor Yellow
Write-Host "     - Import skills via Plugin Marketplace"
Write-Host "     - Add managed resources (SAP RGs + AMS RG)"
Write-Host "     - Upload sap-landscape-inventory.json as Knowledge Source"
Write-Host "     - Paste team onboarding content"
Write-Host ""
Write-Host "  4. Deploy collector to SAP VMs (see README Step 4)" -ForegroundColor Yellow
Write-Host ""
