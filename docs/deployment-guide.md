# SAP Azure SRE Agent — Deployment Guide

Step-by-step guide to deploy the SAP SRE Agent in your Azure environment. Estimated time: **2–3 hours**.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  What you'll set up:                                            │
│                                                                 │
│  1. Resource Group         → RG for SRE operations components   │
│  2. Storage Account        → Stores SAP config snapshots        │
│  3. Managed Identity (UMI) → Auth for function apps + VMs       │
│  4. Config Proxy Function  → Agent reads SAP configs via API    │
│  5. Command Proxy Function → Agent runs allowlisted VM commands │
│  6. SAP VM Collector       → Cron job collects configs weekly   │
│  7. SRE Agent Instance     → The agent itself at sre.azure.com  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

- [ ] Azure subscription with SAP workloads (HANA, NetWeaver)
- [ ] Azure Monitor for SAP Solutions (AMS) configured with Log Analytics workspace
- [ ] `az` CLI installed locally (v2.60+)
- [ ] Contributor + User Access Administrator on the subscription
- [ ] Access to [sre.azure.com](https://sre.azure.com) to create the SRE Agent

---

## Phase 1: Azure Infrastructure

### Step 1.1 — Set Variables

Edit these values for your environment, then paste into your terminal:

```powershell
# === EDIT THESE ===
$SUB_ID         = "<your-subscription-id>"
$TENANT_ID      = "<your-tenant-id>"
$LOCATION       = "centralus"                    # Region where SAP runs
$RG_SRE         = "RG_SRE_OPS"                   # Resource group for SRE components
$STORAGE_NAME   = "stsreconfigs$(Get-Random -Max 999)"  # Must be globally unique
$UMI_NAME       = "sre-ops-mi"                   # Managed identity for proxies + collector
$FUNC_PLAN      = "sre-ops-plan"                  # App Service plan (shared by both functions)
$FUNC_CONFIG     = "sap-config-proxy"             # Config proxy function app name
$FUNC_COMMAND    = "sap-command-proxy"            # Command proxy function app name
$CONTAINER_NAME = "sap-configs"                   # Blob container for SAP configs
$VNET_NAME      = "<your-sap-vnet-name>"          # VNet where SAP VMs run
$VNET_RG        = "<your-vnet-resource-group>"    # RG containing the VNet
$SUBNET_INTEGRATION = "IntegrationSubnet"         # Subnet for function app VNet integration (create if needed)

# SAP resource groups (one per system)
$SAP_RGS = @("RG_SAP_ECP", "RG_SAP_QAS")         # Add all SAP resource groups
```

```powershell
az account set --subscription $SUB_ID
```

### Step 1.2 — Create Resource Group

```powershell
az group create --name $RG_SRE --location $LOCATION
```

### Step 1.3 — Create User-Assigned Managed Identity

```powershell
az identity create --name $UMI_NAME --resource-group $RG_SRE --location $LOCATION

# Save the outputs — you'll need these later
$UMI_ID = az identity show -n $UMI_NAME -g $RG_SRE --query id -o tsv
$UMI_CLIENT_ID = az identity show -n $UMI_NAME -g $RG_SRE --query clientId -o tsv
$UMI_PRINCIPAL_ID = az identity show -n $UMI_NAME -g $RG_SRE --query principalId -o tsv

Write-Host "UMI Client ID:    $UMI_CLIENT_ID"
Write-Host "UMI Principal ID: $UMI_PRINCIPAL_ID"
```

### Step 1.4 — Create Storage Account

```powershell
# Create storage account (no public access, no shared key)
az storage account create `
    --name $STORAGE_NAME `
    --resource-group $RG_SRE `
    --location $LOCATION `
    --sku Standard_LRS `
    --kind StorageV2 `
    --allow-shared-key-access false `
    --default-action Deny `
    --min-tls-version TLS1_2

# Create blob container
az storage container create `
    --name $CONTAINER_NAME `
    --account-name $STORAGE_NAME `
    --auth-mode login

# Grant UMI blob read access (for config proxy to read configs)
az role assignment create `
    --assignee-object-id $UMI_PRINCIPAL_ID `
    --assignee-principal-type ServicePrincipal `
    --role "Storage Blob Data Reader" `
    --scope "/subscriptions/$SUB_ID/resourceGroups/$RG_SRE/providers/Microsoft.Storage/storageAccounts/$STORAGE_NAME"
```

### Step 1.5 — Create Integration Subnet (if it doesn't exist)

The function apps need VNet integration to reach the storage account firewall.

```powershell
# Check if subnet exists
az network vnet subnet show --vnet-name $VNET_NAME -g $VNET_RG -n $SUBNET_INTEGRATION 2>$null

# If not, create it (use an available /26 or /27 CIDR)
az network vnet subnet create `
    --vnet-name $VNET_NAME `
    --resource-group $VNET_RG `
    --name $SUBNET_INTEGRATION `
    --address-prefixes "10.x.y.0/26" `
    --delegations "Microsoft.Web/serverFarms"
```

Add the subnet to the storage firewall:

```powershell
$SUBNET_ID = az network vnet subnet show --vnet-name $VNET_NAME -g $VNET_RG -n $SUBNET_INTEGRATION --query id -o tsv

az storage account network-rule add `
    --account-name $STORAGE_NAME `
    --subnet $SUBNET_ID
```

### Step 1.6 — Deploy Function Apps

```powershell
# Create App Service plan (B1 is sufficient)
az appservice plan create `
    --name $FUNC_PLAN `
    --resource-group $RG_SRE `
    --location $LOCATION `
    --sku B1 `
    --is-linux

# --- Config Proxy ---
az functionapp create `
    --name $FUNC_CONFIG `
    --resource-group $RG_SRE `
    --plan $FUNC_PLAN `
    --runtime python `
    --runtime-version 3.11 `
    --functions-version 4 `
    --os-type Linux `
    --assign-identity $UMI_ID `
    --storage-account $STORAGE_NAME

# --- Command Proxy ---
az functionapp create `
    --name $FUNC_COMMAND `
    --resource-group $RG_SRE `
    --plan $FUNC_PLAN `
    --runtime python `
    --runtime-version 3.11 `
    --functions-version 4 `
    --os-type Linux `
    --assign-identity $UMI_ID `
    --storage-account $STORAGE_NAME
```

### Step 1.7 — Configure Function App Settings

Generate a random API key (agents will use this to call the proxies):

```powershell
$API_KEY = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N").Substring(0,16)
Write-Host "API Key: $API_KEY  (save this — you'll need it for the agent)"
```

Set app settings on both function apps:

```powershell
# Config proxy settings
az functionapp config appsettings set -n $FUNC_CONFIG -g $RG_SRE --settings `
    AZURE_CLIENT_ID=$UMI_CLIENT_ID `
    STORAGE_ACCOUNT_NAME=$STORAGE_NAME `
    CONTAINER_NAME=$CONTAINER_NAME `
    AGENT_KEY_sre1=$API_KEY

# Command proxy settings
az functionapp config appsettings set -n $FUNC_COMMAND -g $RG_SRE --settings `
    AZURE_CLIENT_ID=$UMI_CLIENT_ID `
    SUBSCRIPTION_ID=$SUB_ID `
    AGENT_KEY_sre1=$API_KEY

# Enable AlwaysOn to prevent cold starts
az functionapp config set -n $FUNC_CONFIG -g $RG_SRE --always-on true
az functionapp config set -n $FUNC_COMMAND -g $RG_SRE --always-on true
```

### Step 1.8 — VNet Integration

```powershell
az functionapp vnet-integration add -n $FUNC_CONFIG -g $RG_SRE --vnet $VNET_NAME --subnet $SUBNET_INTEGRATION
az functionapp vnet-integration add -n $FUNC_COMMAND -g $RG_SRE --vnet $VNET_NAME --subnet $SUBNET_INTEGRATION
```

### Step 1.9 — Deploy Function Code

```powershell
# From the repo root
cd proxy/sre-config-proxy
func azure functionapp publish $FUNC_CONFIG --python
cd ../sre-command-proxy
func azure functionapp publish $FUNC_COMMAND --python
```

### Step 1.10 — RBAC for Command Proxy

The command proxy needs permission to run VM commands on SAP VMs. Create a custom role with minimum privileges:

```powershell
# Create custom role definition
$roleDef = @{
    Name = "Custom - VM Run Command Operator"
    Description = "Run read-only commands on SAP VMs via Azure VM Run Command"
    Actions = @(
        "Microsoft.Compute/virtualMachines/runCommand/action"
        "Microsoft.Compute/virtualMachines/read"
    )
    AssignableScopes = @("/subscriptions/$SUB_ID")
} | ConvertTo-Json -Depth 3

$roleDef | Out-File -FilePath role-definition.json -Encoding UTF8
az role definition create --role-definition role-definition.json
```

Assign the role to the UMI on each SAP resource group:

```powershell
foreach ($rg in $SAP_RGS) {
    az role assignment create `
        --assignee-object-id $UMI_PRINCIPAL_ID `
        --assignee-principal-type ServicePrincipal `
        --role "Custom - VM Run Command Operator" `
        --scope "/subscriptions/$SUB_ID/resourceGroups/$rg"
    Write-Host "Assigned VM Run Command role on $rg"
}
```

---

## Phase 2: SAP VM Configuration Collector

The collector is a shell script that runs on each SAP VM via cron. It gathers HANA configs, OS tuning, Pacemaker settings, and uploads them to blob storage for the agent to read.

### Step 2.1 — Create Environment File on Each SAP VM

SSH to each SAP VM and create the environment file:

```bash
sudo mkdir -p /opt/sre
sudo tee /opt/sre/sre.env << 'EOF'
# SRE Agent Collector Configuration
# Generated during SRE Agent onboarding

SRE_STORAGE_ACCOUNT="<your-storage-account-name>"
SRE_CONTAINER="sap-configs"
SRE_UMI_CLIENT_ID="<your-umi-client-id>"
EOF

sudo chmod 600 /opt/sre/sre.env
```

### Step 2.2 — Deploy Collector Script

Copy `collector/collect-sap-configs.sh` to each SAP VM:

```bash
# From a machine that can SSH to the SAP VMs:
scp collector/collect-sap-configs.sh <vm-ip>:/tmp/
ssh <vm-ip> "sudo mv /tmp/collect-sap-configs.sh /opt/sre/ && sudo chmod +x /opt/sre/collect-sap-configs.sh"
```

### Step 2.3 — Grant Blob Upload Permission to VMs

Each SAP VM needs a user-assigned managed identity with **Storage Blob Data Contributor** to upload configs. You can reuse the same UMI from Phase 1 or create a separate one.

```powershell
# Assign the UMI to each SAP VM
foreach ($rg in $SAP_RGS) {
    $vms = az vm list -g $rg --query "[].name" -o tsv
    foreach ($vm in $vms) {
        az vm identity assign -g $rg -n $vm --identities $UMI_ID
        Write-Host "Assigned UMI to $vm in $rg"
    }
}

# Grant Storage Blob Data Contributor (upload permission)
az role assignment create `
    --assignee-object-id $UMI_PRINCIPAL_ID `
    --assignee-principal-type ServicePrincipal `
    --role "Storage Blob Data Contributor" `
    --scope "/subscriptions/$SUB_ID/resourceGroups/$RG_SRE/providers/Microsoft.Storage/storageAccounts/$STORAGE_NAME"
```

> **Note:** If the storage account firewall is set to Deny, SAP VMs must be in a subnet with a service endpoint or private endpoint to the storage account, OR you add each VM subnet to the storage firewall rules.

### Step 2.4 — Install Azure CLI on SAP VMs (if not present)

```bash
# SLES
sudo zypper install -y azure-cli

# RHEL
sudo dnf install -y azure-cli
```

### Step 2.5 — Test the Collector Manually

Run once manually to verify everything works:

```bash
# Source the environment file
source /opt/sre/sre.env

# Run for a HANA DB server (adjust SID, instance, roles)
sudo -E /opt/sre/collect-sap-configs.sh --sid ECP --db-sid ECP --roles db --hana-inst 00

# Run for an ASCS server
sudo -E /opt/sre/collect-sap-configs.sh --sid ECP --db-sid ECP --roles ascs --ascs-inst 01

# Run for a standalone (all-in-one) system
sudo -E /opt/sre/collect-sap-configs.sh --sid ECP --db-sid ECP --roles db,ascs,app --hana-inst 00 --ascs-inst 01 --app-inst 02
```

Verify the upload in Azure:

```powershell
az storage blob list --account-name $STORAGE_NAME --container-name $CONTAINER_NAME --auth-mode login --query "[].name" -o tsv
```

You should see blobs at paths like `ECP/ecpdb01/latest/...`.

### Step 2.6 — Set Up Weekly Cron Job

On each SAP VM, create a cron entry that runs weekly:

```bash
# Create the cron wrapper script
sudo tee /opt/sre/run-collector.sh << 'CRONEOF'
#!/bin/bash
# SRE Config Collector — Weekly Cron Wrapper
# Loads environment and runs the collector with VM-specific parameters

source /opt/sre/sre.env

# === EDIT: Set the correct values for THIS VM ===
SID="ECP"
DB_SID="ECP"
ROLES="db"              # db | ascs | app | sbd | db,ascs,app (for all-in-one)
HANA_INST="00"          # HANA instance number (only for db role)
ASCS_INST=""            # ASCS instance number (only for ascs role)
APP_INST=""             # APP instance number (only for app role)
# ================================================

ARGS="--sid $SID --db-sid $DB_SID --roles $ROLES"
[ -n "$HANA_INST" ] && ARGS="$ARGS --hana-inst $HANA_INST"
[ -n "$ASCS_INST" ] && ARGS="$ARGS --ascs-inst $ASCS_INST"
[ -n "$APP_INST" ]  && ARGS="$ARGS --app-inst $APP_INST"

/opt/sre/collect-sap-configs.sh $ARGS >> /var/log/sre-config-collect.log 2>&1
CRONEOF

sudo chmod +x /opt/sre/run-collector.sh

# Schedule: Every Sunday at 2:00 AM
echo "0 2 * * 0 root /opt/sre/run-collector.sh" | sudo tee /etc/cron.d/sre-collector
sudo chmod 644 /etc/cron.d/sre-collector
```

### Step 2.7 — Verify Cron Setup

```bash
# Confirm the cron entry exists
cat /etc/cron.d/sre-collector

# Check collector logs after next run
tail -50 /var/log/sre-config-collect.log
```

---

## Phase 3: SRE Agent Setup

### Step 3.1 — Create SAP Landscape Inventory

Copy `config/sap-landscape-inventory.template.json` and fill in your systems:

```bash
cp config/sap-landscape-inventory.template.json config/sap-landscape-inventory.json
```

Edit with your SAP system details (SIDs, VMs, IPs, resource groups, instance numbers).

### Step 3.2 — Fill In Configuration

```bash
cp config/config.template.yaml config/config.<your-org>.yaml
```

Fill in all values from Phase 1 (subscription ID, storage account name, proxy URLs, API key, AMS workspace ID, etc.).

### Step 3.3 — Create the SRE Agent

1. Go to [sre.azure.com](https://sre.azure.com)
2. Create a new agent (e.g., `sap-sre-agent`)
3. Assign it to your subscription
4. Note the agent's **Managed Identity** name and client ID

### Step 3.4 — Grant Agent MI Read Access

The agent's managed identity needs Reader on SAP resource groups:

```powershell
$AGENT_MI_PRINCIPAL = "<agent-mi-principal-id-from-portal>"

# Reader on each SAP resource group
foreach ($rg in $SAP_RGS) {
    az role assignment create `
        --assignee-object-id $AGENT_MI_PRINCIPAL `
        --assignee-principal-type ServicePrincipal `
        --role "Reader" `
        --scope "/subscriptions/$SUB_ID/resourceGroups/$rg"
}

# Log Analytics Reader on AMS workspace
az role assignment create `
    --assignee-object-id $AGENT_MI_PRINCIPAL `
    --assignee-principal-type ServicePrincipal `
    --role "Log Analytics Reader" `
    --scope "/subscriptions/$SUB_ID/resourceGroups/$AMS_RG/providers/Microsoft.OperationalInsights/workspaces/$AMS_WORKSPACE"
```

### Step 3.5 — Upload Skills

In the SRE Agent portal, go to **Skill Builder** and upload each file from the `skills/` folder:

| # | Skill File | Skill Name |
|---|-----------|------------|
| 1 | `skills/sap-landscape-discovery/SKILL.md` | SAP Landscape Discovery |
| 2 | `skills/sap-deployment-readiness/SKILL.md` | SAP Deployment Readiness |
| 3 | `skills/sap-operational-health/SKILL.md` | SAP Operational Health |
| 4 | `skills/sap-configuration-guardian/SKILL.md` | SAP Configuration Guardian |
| 5 | `skills/sap-incident-rca/SKILL.md` | SAP Incident RCA |
| 6 | `skills/sap-performance-diagnostics/SKILL.md` | SAP Performance Diagnostics |
| 7 | `skills/sap-ha-dr-guardian/SKILL.md` | SAP HA & DR Guardian |
| 8 | `skills/sap-resiliency-assessment/SKILL.md` | SAP Resiliency Assessment |
| 9 | `skills/sap-cost-insights/SKILL.md` | SAP Cost Insights |
| 10 | `skills/sap-anomaly-forecaster/SKILL.md` | SAP Anomaly Forecaster |
| 11 | `skills/sap-maintenance-autopilot/SKILL.md` | SAP Maintenance Autopilot |
| 12 | `skills/sap-self-healing/SKILL.md` | SAP Self-Healing |
| 13 | `skills/sap-command-executor/SKILL.md` | SAP Command Executor |
| 14 | `skills/sap-servicenow-connector/SKILL.md` | SAP ServiceNow Connector |
| 15 | `skills/sap-apm-connector/SKILL.md` | SAP APM Connector |

### Step 3.6 — Configure Team Onboarding

1. Open `onboarding/team-onboarding.template.md`
2. Replace all `{{placeholder}}` values with your values from `config.<your-org>.yaml`
3. Paste the completed content into the agent's **Team Onboarding** section

### Step 3.7 — Upload Knowledge Sources

Upload these files to the agent's **Knowledge Sources**:

- `config/sap-landscape-inventory.json` — your filled-in SAP landscape
- Optionally: this GitHub repo as a connected Knowledge Source

---

## Phase 4: Validation

Run these test prompts in the SRE Agent chat to verify each layer:

| Test | Prompt | Validates |
|------|--------|-----------|
| 1 | "What SAP systems do I have?" | Landscape Discovery + inventory file |
| 2 | "Is everything healthy?" | Operational Health + AMS + Azure Monitor |
| 3 | "Run crm_mon on \<db-vm\>" | Command Executor + command proxy + RBAC |
| 4 | "Show config for \<SID\>" | Configuration Guardian + config proxy + blob storage |
| 5 | "How much do our SAP systems cost?" | Cost Insights + Cost Management API |
| 6 | "Show HA status for \<SID\>" | HA & DR Guardian + Pacemaker commands |

**Expected results for each test:**

- **Test 1:** Agent lists all SAP systems from the inventory file
- **Test 2:** Agent queries AMS Log Analytics, Resource Health, and returns a health summary
- **Test 3:** Agent calls the command proxy, returns Pacemaker cluster status
- **Test 4:** Agent calls the config proxy, returns HANA/OS config files
- **Test 5:** Agent queries Cost Management API and returns cost breakdown
- **Test 6:** Agent runs `SAPHanaSR-showAttr` and `crm_mon` on HA nodes

---

## Troubleshooting

### Config proxy returns 401
- Verify `AGENT_KEY_sre1` app setting matches the API key in Team Onboarding
- Check function app logs: `az functionapp log tail -n $FUNC_CONFIG -g $RG_SRE`

### Config proxy returns 403 / empty results
- Storage account firewall is blocking. Verify IntegrationSubnet is in the allowed rules
- Verify UMI has **Storage Blob Data Reader** on the storage account
- Verify `AZURE_CLIENT_ID` app setting matches the UMI client ID

### Command proxy returns "VM not found"
- Verify `SUBSCRIPTION_ID` app setting is correct
- Verify UMI has **Custom - VM Run Command Operator** on the SAP resource group

### Collector fails to upload
- Verify `az` CLI is installed on the VM: `az --version`
- Verify UMI is assigned to the VM: `az vm identity show -g <rg> -n <vm>`
- Verify UMI has **Storage Blob Data Contributor** on the storage account
- Check if VM subnet has a service endpoint to `Microsoft.Storage`
- Check logs: `tail -50 /var/log/sre-config-collect.log`

### Agent can't query AMS
- Verify the agent MI has **Log Analytics Reader** on the AMS workspace
- Verify the AMS workspace ID in Team Onboarding matches the actual workspace

---

## File Summary

| File | Purpose | When to Edit |
|------|---------|-------------|
| `config/config.template.yaml` | Master config template | Copy and fill during setup |
| `config/sap-landscape-inventory.json` | SAP system inventory | Update when systems change |
| `onboarding/team-onboarding.template.md` | Agent instructions | Fill during agent setup |
| `collector/collect-sap-configs.sh` | Config collector for SAP VMs | Deploy once to each VM |
| `proxy/sre-config-proxy/function_app.py` | Config proxy function | Deploy once |
| `proxy/sre-command-proxy/function_app.py` | Command proxy function | Deploy once |
| `/opt/sre/sre.env` (on SAP VMs) | Collector environment variables | Create on each VM |
| `/opt/sre/run-collector.sh` (on SAP VMs) | Cron wrapper with VM-specific args | Create on each VM |

---

## Security Notes

- **No shared keys.** Storage account uses `allowSharedKeyAccess: false`. All access via Entra ID (MI).
- **No public endpoints.** Storage firewall is Deny by default. Only IntegrationSubnet and SAP VM subnets can reach it.
- **Allowlisted commands only.** The command proxy has a hardcoded allowlist of 14 read-only commands. No arbitrary script execution.
- **API key per agent.** Each SRE Agent instance gets its own `AGENT_KEY_*` app setting. Rotate by updating the app setting.
- **Minimum RBAC.** UMI gets only Reader, Blob Reader/Contributor, and a custom VM Run Command role — no Contributor on the subscription.
