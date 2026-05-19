# SAP Azure SRE Agent

12 SRE skills + 1 command runner for SAP HANA and NetWeaver on Azure. All read-only — zero changes to your SAP environment.

**10 skills work on Day 1** with just Azure APIs. Deploy the optional command proxy to unlock live VM queries.

## Architecture

![SAP Azure SRE Agent Architecture](docs/sap-on-azure-sre-agent.png)

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

## Prerequisites

- **Azure CLI** (`az`) — [install](https://aka.ms/installazurecli)
- SAP workloads running on Azure VMs (HANA + NetWeaver)
- Azure Monitor for SAP Solutions (AMS) with HANA provider configured
- Azure SRE Agent created at [sre.azure.com](https://sre.azure.com)

## Quick Start (Day 1 — No Proxy)

### 1. Import Skills

1. SRE Agent portal → **Plugins** → **Manage marketplaces** → **Add**
2. Enter: `mcaps-microsoft/sap-azure-sre-agent` → **Resolve** → **Add**
3. Click plugin → **Install** (imports all 13 skills)

### 2. Configure SRE Agent

- **Managed Resources:** Add SAP resource groups + AMS resource group
- **Connectors:** Add Log Analytics Workspace (AMS workspace ID)
- **Knowledge Sources:** Upload `config/sap-landscape-inventory.template.json` (filled with your SAP systems)
- **Team Onboarding:** Paste filled-in `onboarding/team-onboarding.template.md`

**Done.** 10 skills are now operational — try "Run resiliency assessment" or "Show SAP landscape".

## Full Setup (Day 2 — With Proxy)

Deploy the command proxy to unlock live VM queries and the remaining 3 skills.

### 3. Deploy Proxy Infrastructure (~5 min)

```powershell
git clone https://github.com/mcaps-microsoft/sap-azure-sre-agent.git
cd sap-azure-sre-agent

az login
.\infra\deploy-sre-infra.ps1 `
    -SubscriptionId "12345678-abcd-efgh-ijkl-123456789012" `
    -StorageAccountName "stsreconfigs001"
```

The script creates everything: Resource Group, VNet, Storage, ACR, Container App, Managed Identity.
Override defaults: `-ResourceGroupName`, `-Location`, `-VNetAddressSpace`.

### 4. Grant Access to SAP Resource Groups

Create a least-privilege custom role and assign it to the UMI (use values from deploy script output):

```powershell
$umi = "<UMI-PRINCIPAL-ID>"
$sub = "<SAP-SUBSCRIPTION-ID>"

# Create custom role (one-time) — grants read + VM Run Command only, no delete/restart
$roleDef = Get-Content infra/sap-sre-agent-role.json -Raw
$roleDef = $roleDef.Replace("<YOUR-SUBSCRIPTION-ID>", $sub)
$roleDef | Set-Content infra/sap-sre-agent-role.json
az role definition create --role-definition infra/sap-sre-agent-role.json

# Assign to each SAP resource group
foreach ($rg in @("RG_SAP_ECP", "RG_SAP_QAS", "RG_SAP_DEV")) {
    az role assignment create --assignee-object-id $umi `
        --assignee-principal-type ServicePrincipal `
        --role "Custom - SAP SRE Agent Operator" --scope "/subscriptions/$sub/resourceGroups/$rg"
}
```

See [`infra/sap-sre-agent-role.json`](infra/sap-sre-agent-role.json) for the full role definition.

<details>
<summary>Quick setup alternative (less secure)</summary>

For demos or quick testing, you can use built-in roles instead of creating a custom role.
**Not recommended for production** — `Virtual Machine Contributor` grants VM delete/restart permissions.

```powershell
foreach ($rg in @("RG_SAP_ECP", "RG_SAP_QAS", "RG_SAP_DEV")) {
    az role assignment create --assignee-object-id $umi `
        --assignee-principal-type ServicePrincipal `
        --role "Reader" --scope "/subscriptions/$sub/resourceGroups/$rg"
    az role assignment create --assignee-object-id $umi `
        --assignee-principal-type ServicePrincipal `
        --role "Virtual Machine Contributor" --scope "/subscriptions/$sub/resourceGroups/$rg"
}
```

</details>

### 5. Update Team Onboarding

Add the proxy URL and API key (from deploy script output) to your Team Onboarding in the SRE Agent portal:

```
Proxy URL:     https://sap-sre-proxy.<env-id>.centralus.azurecontainerapps.io
Proxy API key: <api-key-from-output>
```

### 6. Verify

```powershell
$proxy = "https://sap-sre-proxy.<env-id>.centralus.azurecontainerapps.io"
$key   = "<api-key>"

# Health check
Invoke-RestMethod "$proxy/api/health"

# List available commands
Invoke-RestMethod "$proxy/api/commands" -Headers @{"x-api-key"=$key}

# Run cluster status on a SAP VM
Invoke-RestMethod "$proxy/api/command" -Method Post -Headers @{"x-api-key"=$key; "Content-Type"="application/json"} `
    -Body '{"vm":"ecpdb01","rg":"RG_SAP_ECP","command_id":"crm_mon"}'
```

**Done.** All 13 skills are now operational. Try "Run crm_mon on ecpdb01" in the SRE Agent.

---

## Optional: Deploy Config Collector

The collector gathers SAP/HANA/OS config files from each VM and uploads to blob storage weekly.
This enables offline STAF config validation even when VMs are unreachable. **Not required for Day 1 or Day 2.**

### Option A: Via Proxy Command (recommended — no SSH needed)

Use the built-in `deploy_collector` command to deploy the collector remotely via the proxy:

```powershell
$proxy = "https://sap-sre-proxy.<env-id>.centralus.azurecontainerapps.io"
$key   = "<api-key>"

# Example: Deploy collector to a HANA DB VM
$body = @{
    vm              = "ecpdb01"
    rg              = "RG_SAP_ECP"
    command_id      = "deploy_collector"
    storage_account = "stsreconfigs001"
    umi_client_id   = "<UMI-CLIENT-ID>"
    sid             = "ECP"
    db_sid          = "ECP"
    roles           = "db"
    hana_inst       = "00"
} | ConvertTo-Json

Invoke-RestMethod "$proxy/api/command" -Method Post `
    -Headers @{"x-api-key"=$key; "Content-Type"="application/json"} -Body $body

# Example: Deploy to an ASCS VM
$body = @{
    vm = "ecpascs01"; rg = "RG_SAP_ECP"; command_id = "deploy_collector"
    storage_account = "stsreconfigs001"; umi_client_id = "<UMI-CLIENT-ID>"
    sid = "ECP"; db_sid = "ECP"; roles = "ascs"; ascs_inst = "01"
} | ConvertTo-Json

Invoke-RestMethod "$proxy/api/command" -Method Post `
    -Headers @{"x-api-key"=$key; "Content-Type"="application/json"} -Body $body
```

If `/opt/sre` already exists on the VM, the command overwrites the script and cron config
with the latest version. Existing logs in `/var/log/sre-config-collect.log` are preserved.

**Prerequisites per VM:**
- Assign the UMI to the VM: `az vm identity assign -g <rg> -n <vm> --identities <UMI-RESOURCE-ID>`
- Add SAP VM subnet to storage firewall: `az storage account network-rule add --account-name <storage> --subnet <subnet-id>`

### Option B: Manual (SSH to each VM)

<details>
<summary>Click to expand manual steps</summary>

**On each SAP VM (as root):**

```bash
# 1. Create directory (idempotent — safe if /opt/sre already exists)
mkdir -p /opt/sre

# 2. Download collector script
az login --identity --username <UMI-CLIENT-ID> --output none
az storage blob download --account-name <STORAGE-ACCOUNT> --container-name sap-configs \
    --name scripts/collect-sap-configs.sh --file /opt/sre/collect-sap-configs.sh --auth-mode login
chmod +x /opt/sre/collect-sap-configs.sh

# 3. Create environment file
cat > /opt/sre/sre.env << 'EOF'
SRE_STORAGE_ACCOUNT=<your-storage-account>
SRE_CONTAINER=sap-configs
SRE_UMI_CLIENT_ID=<your-umi-client-id>
EOF
chmod 600 /opt/sre/sre.env

# 4. Create cron wrapper (edit SID/roles for THIS VM)
cat > /opt/sre/run-collector.sh << 'EOF'
#!/bin/bash
source /opt/sre/sre.env
/opt/sre/collect-sap-configs.sh --sid ECP --db-sid ECP --roles db --hana-inst 00 \
    >> /var/log/sre-config-collect.log 2>&1
EOF
chmod +x /opt/sre/run-collector.sh

# 5. Set up weekly cron (Sunday 2:00 AM)
echo "0 2 * * 0 root /opt/sre/run-collector.sh" > /etc/cron.d/sre-collector
chmod 644 /etc/cron.d/sre-collector
```

**Also required (run from PowerShell):**

```powershell
# Assign UMI to the VM
az vm identity assign -g RG_SAP_ECP -n ecpdb01 --identities <UMI-RESOURCE-ID>

# Add VM subnet to storage firewall
az storage account network-rule add --account-name stsreconfigs001 --subnet <sap-vm-subnet-id>
```

</details>

---

## What Gets Created

```
rg-sre-ops  (or custom name via -ResourceGroupName)
├── sre-ops-umi              User-Assigned Managed Identity
├── <storage-account>        Storage Account (SAP configs, shared key disabled)
├── <acr>                    Azure Container Registry (Basic, container images)
├── vnet-sre-ops             Virtual Network (10.60.0.0/16 default)
│   └── sn-container-apps    Subnet (/23, delegated to Container Apps, Storage endpoint)
├── sre-ops-env              Container Apps Environment (VNet-integrated)
└── sap-sre-proxy            Container App (config reads + 14 read-only VM commands)
```

### API Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/commands` | List 14 allowed commands |
| POST | `/api/command` | Execute read-only command on SAP VM |
| GET | `/api/registry` | SAP landscape inventory |
| GET | `/api/config/{sid}/{hostname}/{path}` | Single config file |
| GET | `/api/configs/{sid}/{hostname}` | All configs for a VM |
| GET | `/api/configs/{sid}` | All configs for a system |
| GET | `/api/diag` | MI + ARM connectivity test |

## Security

- **14 read-only commands only:** Hardcoded allowlist in proxy source code. No arbitrary shell execution.
- **Zero changes to SAP:** All commands are read-only — `crm_mon`, `df`, `free`, `HDB info`, etc. Nothing modifies SAP state.
- **No shared keys:** Storage uses identity-based auth (RBAC). Shared key access disabled.
- **Managed Identity bound:** UMI only works from the Container App — cannot be used from any other context.
- **API key required:** Every proxy call requires `x-api-key` header.
- **Input sanitization:** All parameters regex-validated and shell-escaped (`shlex.quote`).
- **Network isolation:** Storage firewall Deny by default. Container App accesses storage via VNet service endpoint.
- **Audit logging:** Every command execution logged with caller, VM, command_id, timestamp.
