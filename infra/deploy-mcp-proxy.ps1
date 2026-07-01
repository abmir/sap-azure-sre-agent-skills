<#
.SYNOPSIS
    Deploy the OPTIONAL SAP SRE command proxy (MCP server) as an Azure Container App.

.DESCRIPTION
    Deploys the MCP server in ../proxy-mcp as a VNet-integrated Azure Container App in
    its OWN resource group, gives it a managed identity with the custom "SAP SRE Agent
    Operator" role on your SAP resource groups (read + runCommand only), and prints the
    MCP endpoint URL + API key to register as an SRE Agent connector.

    This is the live-command path ONLY. It does NOT touch storage — config reads are done
    by the SRE Agent's own MI reading the sap-configs blob directly.

.PARAMETER SubscriptionId
    Subscription to deploy into (and where the SAP VMs live). Required.

.PARAMETER SapResourceGroups
    One or more SAP resource groups whose VMs the MCP server may run commands on. The
    MCP UMI is granted the custom "SAP SRE Agent Operator" role on each. Required.

.PARAMETER McpApiKey
    API key the connector must send in the x-api-key header. Auto-generated if omitted.

.PARAMETER ResourceGroupName   Default: rg-sre-proxy-mcp
.PARAMETER Location            Default: centralus
.PARAMETER AppName             Default: sap-sre-proxy-mcp

.EXAMPLE
    ./deploy-mcp-proxy.ps1 -SubscriptionId <sub> -SapResourceGroups RG_SAP_CUS_AB1,RG_SAP_AB2
#>
param(
    [Parameter(Mandatory)] [string]   $SubscriptionId,
    [Parameter(Mandatory)] [string[]] $SapResourceGroups,
    [string]   $McpApiKey,
    [string]   $ResourceGroupName = "rg-sre-proxy-mcp",
    [string]   $Location          = "centralus",
    [string]   $AppName           = "sap-sre-proxy-mcp"
)

$ErrorActionPreference = "Stop"
$RG        = $ResourceGroupName
$UmiName   = "sre-mcp-umi"
$EnvName   = "sre-mcp-env"
$VNetName  = "vnet-sre-mcp"
$Subnet    = "sn-container-apps"
$AcrName   = "acrsremcp$($SubscriptionId.Substring(0,8) -replace '-','')"
$RepoRoot  = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$McpDir    = Join-Path $RepoRoot "proxy-mcp"
$RoleFile  = Join-Path $PSScriptRoot "sap-sre-agent-role.json"
$RoleName  = "Custom - SAP SRE Agent Operator"
if (-not $McpApiKey) { $McpApiKey = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N").Substring(0,16) }

function Step($m){ Write-Host "`n>> $m" -ForegroundColor Cyan }
function OK($m){ Write-Host "   OK: $m" -ForegroundColor Green }

az account set --subscription $SubscriptionId
az extension add --name containerapp --upgrade --only-show-errors 2>$null

Step "1 — Resource group"
az group create -n $RG -l $Location -o none
OK $RG

Step "2 — Managed identity"
az identity create -n $UmiName -g $RG -l $Location -o none 2>$null
$umi = az identity show -n $UmiName -g $RG -o json | ConvertFrom-Json
OK "$UmiName (principal $($umi.principalId))"

Step "3 — Custom RBAC role on SAP resource groups"
# Ensure the custom role exists at subscription scope
$existing = az role definition list --name $RoleName -o json | ConvertFrom-Json
if (-not $existing) {
    $def = Get-Content $RoleFile -Raw | ConvertFrom-Json
    $def.AssignableScopes = @("/subscriptions/$SubscriptionId")
    $tmp = New-TemporaryFile
    ($def | ConvertTo-Json -Depth 10) | Set-Content $tmp
    az role definition create --role-definition "@$tmp" -o none
    Remove-Item $tmp
    OK "Created role '$RoleName'"
} else { OK "Role '$RoleName' already exists" }
foreach ($srg in $SapResourceGroups) {
    az role assignment create --assignee-object-id $umi.principalId --assignee-principal-type ServicePrincipal `
        --role $RoleName --scope "/subscriptions/$SubscriptionId/resourceGroups/$srg" -o none 2>$null
    OK "Granted on $srg"
}

Step "4 — Container registry + image"
az acr create -n $AcrName -g $RG -l $Location --sku Premium --admin-enabled false -o none 2>$null
az role assignment create --assignee-object-id $umi.principalId --assignee-principal-type ServicePrincipal `
    --role AcrPull --scope "/subscriptions/$SubscriptionId/resourceGroups/$RG/providers/Microsoft.ContainerRegistry/registries/$AcrName" -o none 2>$null
az acr build --registry $AcrName -t sap-sre-proxy-mcp:latest $McpDir --no-logs -o none
OK "$AcrName.azurecr.io/sap-sre-proxy-mcp:latest"

Step "5 — VNet + Container Apps environment"
az network vnet create -n $VNetName -g $RG -l $Location --address-prefix 10.70.0.0/16 `
    --subnet-name $Subnet --subnet-prefix 10.70.0.0/23 -o none 2>$null
az network vnet subnet update -n $Subnet --vnet-name $VNetName -g $RG `
    --delegations Microsoft.App/environments -o none 2>$null
$subnetId = az network vnet subnet show -n $Subnet --vnet-name $VNetName -g $RG --query id -o tsv
az containerapp env create -n $EnvName -g $RG -l $Location --infrastructure-subnet-resource-id $subnetId -o none 2>$null
OK $EnvName

Step "6 — Container App (MCP server)"
az containerapp create -n $AppName -g $RG --environment $EnvName `
    --image "$AcrName.azurecr.io/sap-sre-proxy-mcp:latest" `
    --user-assigned $umi.id --registry-server "$AcrName.azurecr.io" --registry-identity $umi.id `
    --ingress external --target-port 8000 --transport http `
    --secrets "mcp-api-key=$McpApiKey" `
    --env-vars "SUBSCRIPTION_ID=$SubscriptionId" "MCP_API_KEY=secretref:mcp-api-key" `
    --min-replicas 1 --max-replicas 2 -o none
$fqdn = az containerapp show -n $AppName -g $RG --query properties.configuration.ingress.fqdn -o tsv
OK "https://$fqdn"

Write-Host "`n==================== MCP server deployed ====================" -ForegroundColor Green
Write-Host "MCP URL (connector):  https://$fqdn/mcp"
Write-Host "API key (x-api-key):  $McpApiKey"
Write-Host "MCP UMI principal:    $($umi.principalId)"
Write-Host "`nRegister it: SRE Agent → Builder → Connectors → Add connector → MCP Server"
Write-Host "  URL = https://$fqdn/mcp   header x-api-key = <the key above>"
Write-Host "Then install the sap-sre-proxy-ops plugin (ships .mcp.json)."
