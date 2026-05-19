# SAP Azure SRE Agent

12 SRE skills + 1 command runner for SAP HANA and NetWeaver on Azure.

## Architecture

![SAP Azure SRE Agent Architecture](docs/sap-on-azure-sre-agent.png)

## Prerequisites

Before starting, you need:
- **SAP workloads** running on Azure VMs (HANA + NetWeaver)
- **Azure Monitor for SAP Solutions (AMS)** with HANA and OS providers configured
- **Azure CLI** (`az`) installed — [install](https://aka.ms/installazurecli)
- Access to [sre.azure.com](https://sre.azure.com)

## Skills

| # | Skill | What it does | Proxy needed? |
|---|-------|-------------|---------------|
| 1 | `sap-landscape-discovery` | SAP system inventory, VM power state, topology | No |
| 2 | `sap-operational-health` | 5-layer health dashboard (infra/OS/cluster/HANA/app) | Optional |
| 3 | `sap-config-validator` | STAF-aligned config checks from Azure/sap-automation-qa | Optional |
| 4 | `sap-ha-cluster-health` | Pacemaker/HSR status, takeover readiness | Optional |
| 5 | `sap-incident-analysis` | Cross-layer root cause analysis | No |
| 6 | `sap-resiliency-assessment` | Azure Advisor + ACSS reliability checks | No |
| 7 | `sap-performance-diagnostics` | HANA memory, disk IOPS, savepoint diagnostics | Optional |
| 8 | `sap-deployment-readiness` | SKU availability, quota, SAP certification | No |
| 9 | `sap-cost-analysis` | Per-system cost breakdown, RI coverage, savings | No |
| 10 | `sap-trend-analysis` | Memory/disk/CPU trend projection via AMS | No |
| 11 | `sap-self-healing` | Log volume full, backup staleness, sysctl drift | Yes |
| 12 | `sap-maintenance-handler` | Azure scheduled maintenance graceful handling | Yes |
| — | `sap-command-runner` | 14 read-only commands on SAP VMs | Yes |

---

## Setup Guide

### Step 1: Create Azure SRE Agent

1. Go to [sre.azure.com](https://sre.azure.com)
2. Create a new agent in your subscription
3. Note the agent's resource group and managed identity

### Step 2: Configure Built-in Tools

Go to **Capabilities → Tools → Built-in tools** and enable:

| Category | Tools to Enable |
|----------|----------------|
| **Core** | All 17/17 |
| **Azure Operation** | All 4/4 |
| **Knowledge Base** | All 4/4 |
| **Log Query** | All 5/5 |
| **Other** | All 7/7 |
| **System** | All 1/1 |
| **Utility** | All 2/2 |
| **Visualization** | All 5/5 |
| **Workspace Operation** | All 1/1 |
| **DevOps** | Optional (0/5 — enable if using Azure DevOps) |

**Target: 45/51 active tools** (all except DevOps)

### Step 3: Configure Built-in Skills

Go to **Capabilities → Skills → Built-in skills** and enable:

| Skill | Required? |
|-------|----------|
| `aks_general` | Optional |
| `api_management` | Optional |
| `app_insights_query` | Recommended |
| `application_gateway_troubleshoot` | Optional |
| `azure_activity_logs` | **Required** |
| `azure_alerting_incident_handler` | **Required** |
| `azure_alerting_scheduled_task` | **Required** |
| `azure_application_insights` | Recommended |
| `azure_cli_command_executor` | **Required** |
| `cannot_connect_to_vm` | Recommended |
| `cdb_general` | Optional |
| `code_repository_management` | Optional |
| `container_apps` | Optional |
| All others | Enable as needed |

**Target: 39/42 active skills** (enable all except those you don't need)

### Step 4: Add Connectors

Go to **Builder → Connectors → Add connector**:

| Category | Connector | Service | Details |
|----------|-----------|---------|---------|
| **Telemetry** | AMS Log Analytics workspace | Log Analytics | Connect to the AMS workspace (workspace ID from your AMS deployment) |
| **Code Repository** | sap-azure-sre-agent | GitHub | Connect to `mcaps-microsoft/sap-azure-sre-agent` (requires OAuth authorization) |
| **MCP** | MCP-MSLearnDocs | MCP server | Microsoft Learn documentation search — helps skills reference SAP on Azure best practices |
| **Notification** | Microsoft Teams | Microsoft Teams | For alert notifications and approval workflows (optional) |
| **Notification** | Outlook | Office 365 Outlook | For email notifications (optional) |

### Step 5: Configure Code Access

Go to **Builder → Code Access**:

1. Click **Add repository**
2. Authorize GitHub OAuth for your org
3. Add `mcaps-microsoft/sap-azure-sre-agent` → verify **Ready** status
4. This lets the agent reference skill code and proxy source during troubleshooting

### Step 6: Configure Incident Platform

Go to **Builder → Incident platform**:

1. Select **Azure Monitor** as the incident source
2. This enables the agent to respond to Azure Monitor alerts automatically

### Step 7: Import Custom Skills

Go to **Builder → Skill builder**:

**Option A: Plugin Marketplace** (if your repo is public or accessible)
1. **Plugins → Manage marketplaces → Add**
2. Enter: `mcaps-microsoft/sap-azure-sre-agent` → **Resolve** → **Add**
3. Click plugin → **Install** (imports all 13 skills)

**Option B: Manual import** (for private/EMU repos — recommended for MCAP)
1. For each of the 13 skills, click **New** in Skill Builder
2. Paste the entire content of `skills/<skill-name>/SKILL.md` (including the `---` YAML frontmatter)
3. Save each skill

Skills to import (in order):
| # | Skill | File |
|---|-------|------|
| 1 | sap-command-runner | `skills/sap-command-runner/SKILL.md` |
| 2 | sap-landscape-discovery | `skills/sap-landscape-discovery/SKILL.md` |
| 3 | sap-operational-health | `skills/sap-operational-health/SKILL.md` |
| 4 | sap-config-validator | `skills/sap-config-validator/SKILL.md` |
| 5 | sap-ha-cluster-health | `skills/sap-ha-cluster-health/SKILL.md` |
| 6 | sap-incident-analysis | `skills/sap-incident-analysis/SKILL.md` |
| 7 | sap-resiliency-assessment | `skills/sap-resiliency-assessment/SKILL.md` |
| 8 | sap-performance-diagnostics | `skills/sap-performance-diagnostics/SKILL.md` |
| 9 | sap-deployment-readiness | `skills/sap-deployment-readiness/SKILL.md` |
| 10 | sap-cost-analysis | `skills/sap-cost-analysis/SKILL.md` |
| 11 | sap-trend-analysis | `skills/sap-trend-analysis/SKILL.md` |
| 12 | sap-self-healing | `skills/sap-self-healing/SKILL.md` |
| 13 | sap-maintenance-handler | `skills/sap-maintenance-handler/SKILL.md` |

### Step 8: Add Managed Resources

Go to **Settings → Managed resources → Add**:

Add each resource group the agent needs to monitor:

| Resource Group | Purpose |
|---------------|---------|
| SAP resource groups (e.g., `RG_SAP_CUS_AB1`) | SAP VMs, disks, NICs, load balancers |
| AMS resource group (e.g., `mrg-sapmon-abb`) | AMS workspace and providers |
| ACSS managed resource groups (e.g., `mrg-AB1-48f3f2`) | SAP Virtual Instances |
| Proxy resource group (`rg-sre-proxy`) | After Step 10 — proxy Container App |

### Step 9: Upload Knowledge Sources

Go to **Builder → Knowledge sources → Add file**:

1. Fill in `config/sap-landscape-inventory.template.json` with your SAP systems:
   - SID, DB SID, resource groups, VM names, roles, instance numbers
   - SAP sidadm and HANA sidadm (may differ, e.g., `ab1adm` vs `db1adm`)
   - AMS workspace name and ID
   - See `config/sap-landscape-inventory.json` for a completed example
2. Upload as `sap-landscape-inventory.json`
3. Verify status shows **Indexed**

> **Tip:** You can also ask the agent "Build SAP landscape inventory from Azure Resource Graph" and it will generate the JSON for you. Review and upload.

### Step 10: Deploy Proxy Infrastructure

The proxy enables live VM command execution. Deploy it in the **same subscription** as your SAP VMs.

```powershell
git clone https://github.com/mcaps-microsoft/sap-azure-sre-agent.git
cd sap-azure-sre-agent

az login
az account set --subscription "<your-sap-subscription-id>"

.\infra\deploy-sre-infra.ps1 `
    -SubscriptionId "<your-sap-subscription-id>" `
    -StorageAccountName "<globally-unique-name>"
```

**Deploy in the same subscription as your SAP VMs** to avoid cross-subscription identity and networking issues.

The script creates:
```
rg-sre-proxy
├── sre-proxy-umi            User-Assigned Managed Identity (proxy)
├── sre-collector-umi        User-Assigned Managed Identity (SAP VMs)
├── <storage-account>        Storage Account (Entra ID auth only, no shared keys)
├── <acr>                    Azure Container Registry (Premium)
├── vnet-sre-proxy           Virtual Network (10.60.0.0/16)
│   └── sn-container-apps    Subnet (/23, delegated to Container Apps)
├── sre-proxy-env            Container Apps Environment (VNet-integrated)
└── sap-sre-proxy            Container App (14 read-only commands)
```

Override defaults: `-ResourceGroupName`, `-ProxyName`, `-Location`, `-VNetAddressSpace`.

### Step 11: Grant RBAC on SAP Resource Groups

Use the **UMI Principal ID** from the deploy script output:

```powershell
$proxyUmi = "<PROXY-UMI-PRINCIPAL-ID>"
$sub = "<your-sap-subscription-id>"

# Create custom role (one-time) — read + VM Run Command only
$roleDef = Get-Content infra/sap-sre-agent-role.json -Raw
$roleDef = $roleDef.Replace("<YOUR-SUBSCRIPTION-ID>", $sub)
$roleDef | Set-Content infra/sap-sre-agent-role.json
az role definition create --role-definition infra/sap-sre-agent-role.json

# Assign to each SAP resource group
foreach ($rg in @("RG_SAP_CUS_AB1")) {
    az role assignment create --assignee-object-id $proxyUmi `
        --assignee-principal-type ServicePrincipal `
        --role "Custom - SAP SRE Agent Operator" `
        --scope "/subscriptions/$sub/resourceGroups/$rg"
}
```

### Step 12: Assign Collector Identity to SAP VMs

The `sre-collector-umi` identity allows SAP VMs to upload config files to blob storage:

```powershell
$collectorUmiId = "<COLLECTOR-UMI-RESOURCE-ID>"

# Assign to each SAP VM
az vm identity assign -g RG_SAP_CUS_AB1 -n AB1vm --identities $collectorUmiId
```

### Step 13: Add SAP VM Subnet to Storage Firewall

Allow SAP VMs to upload collected configs:

```powershell
# Get the SAP VM subnet ID
$sapSubnetId = az network vnet subnet show `
    --name DBSubnet --vnet-name VNET_CUS -g RG_SharedServices_CUS `
    --query id -o tsv

# Add to storage firewall
az storage account network-rule add `
    --account-name <storage-account> --subnet $sapSubnetId
```

### Step 14: Deploy Collector and Run First Collection

Deploy the config collector to each SAP VM via the proxy:

```powershell
$proxy = "https://sap-sre-proxy.<env-id>.centralus.azurecontainerapps.io"
$key   = "<api-key-from-deploy-output>"

$body = @{
    vm              = "AB1vm"
    rg              = "RG_SAP_CUS_AB1"
    command_id      = "deploy_collector"
    storage_account = "<storage-account>"
    umi_client_id   = "<COLLECTOR-UMI-CLIENT-ID>"
    sid             = "AB1"
    db_sid          = "DB1"
    roles           = "db,ascs,app"
    hana_inst       = "00"
    ascs_inst       = "01"
    app_inst        = "00"
} | ConvertTo-Json

Invoke-RestMethod "$proxy/api/command" -Method Post `
    -Headers @{"X-API-Key"=$key; "Content-Type"="application/json"} -Body $body
```

Then trigger the first collection:

```powershell
# Run collector immediately (instead of waiting for Sunday cron)
$body = @{
    vm = "AB1vm"; rg = "RG_SAP_CUS_AB1"; command_id = "uptime"
    subscription_id = "<sub-id>"
} | ConvertTo-Json

# SSH to the VM and run: sudo /opt/sre/run-collector.sh
# Check log: sudo tail -30 /var/log/sre-config-collect.log
```

### Step 15: Update Team Onboarding

Go to **Team onboarding** (left sidebar):

1. Fill in `onboarding/team-onboarding.template.md` with:
   - Proxy URL and API key (from Step 10 output)
   - AMS workspace name and ID
   - SAP system details (SIDs, VMs, resource groups)
   - Subscription IDs
2. Paste the filled-in content into the Team Onboarding text box
3. Save

### Step 16: Verify

Open a new chat in the SRE Agent and test:

| Test | Expected Result |
|------|----------------|
| "What SAP systems do I have?" | Shows AB1 system from landscape inventory |
| "Run resiliency assessment for RG_SAP_CUS_AB1" | Advisor findings + supplemental checks |
| "Is AB1 up?" | Full stack health check (VM + SAP + HANA) |
| "Run uptime on AB1vm" | Raw VM output via command proxy |
| "How much does AB1 cost?" | Cost breakdown from Cost Management API |
| "Analyze memory trends for AB1" | AMS trend charts with regression |

---

## Architecture

```
┌─────────────────────────────────────────┐
│ Azure SRE Agent (sre.azure.com)         │
│ ├── 13 Custom Skills                    │
│ ├── 39+ Built-in Skills                 │
│ ├── 45 Built-in Tools                   │
│ └── Connectors: AMS, GitHub, MCP, Teams │
│                                         │
│ Uses: GetArmResourceAsJson,             │
│   RunAzCliReadCommands,                 │
│   QueryLogAnalyticsByWorkspaceId,       │
│   ExecutePythonCode (proxy calls only)  │
└──────────────┬──────────────────────────┘
               │ HTTPS + API Key
               ▼
┌─────────────────────────────────────────┐
│ Command Proxy (Container App)           │
│ ├── 14 read-only commands               │
│ ├── Config file reads from blob         │
│ └── Auth: API Key (Entra ID optional)   │
│                                         │
│ Uses: sre-proxy-umi (Managed Identity)  │
│   → VM Run Command (ARM API)            │
│   → Blob Storage read/write             │
└──────────────┬──────────────────────────┘
               │ ARM API (management plane)
               ▼
┌─────────────────────────────────────────┐
│ SAP VMs (AB1vm, etc.)                   │
│ ├── HANA + NetWeaver                    │
│ ├── sre-collector-umi (uploads configs) │
│ └── Cron: weekly config collection      │
└─────────────────────────────────────────┘
```

## Identities

| Identity | Assigned to | Purpose | RBAC |
|----------|------------|---------|------|
| **SRE Agent MI** | SRE Agent (platform) | Azure API queries | Reader on SAP RGs, Log Analytics Reader on AMS workspace |
| **sre-proxy-umi** | Container App | VM Run Commands + blob access | Custom RBAC on SAP RGs, Storage Blob Data Owner |
| **sre-collector-umi** | SAP VMs | Config upload to blob | Storage Blob Data Contributor |

## Security

| Layer | Protection |
|-------|-----------|
| **Authentication** | API key required for all proxy calls. Entra ID Easy Auth can be enabled. |
| **Authorization** | Custom RBAC role — read + runCommand only, no VM delete/restart. |
| **Command allowlist** | 14 read-only commands hardcoded in proxy. No arbitrary shell execution. |
| **Identity binding** | sre-proxy-umi bound to Container App. sre-collector-umi bound to SAP VMs. |
| **Network** | Storage: Deny by default, VNet service endpoints only. No shared keys. |
| **Audit** | Every command logged with caller, VM, command_id, timestamp. |

## API Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/commands` | List 14 allowed commands |
| POST | `/api/command` | Execute command on SAP VM |
| POST | `/api/batch` | Execute multiple commands (max 6) |
| GET | `/api/registry` | SAP landscape inventory from blob |
| GET | `/api/configs/{sid}/{hostname}` | All configs for a VM |
| GET | `/api/diag` | MI + ARM connectivity test |
