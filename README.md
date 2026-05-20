# SAP Azure SRE Agent

AI-powered SRE agent for SAP HANA and NetWeaver on Azure. Automates health monitoring, STAF configuration validation, incident analysis, and cost optimization — all through natural language at [sre.azure.com](https://sre.azure.com).

## What It Does

| Capability | Example Prompt | How It Works |
|-----------|----------------|-------------|
| **Health monitoring** | "Is AB1 healthy?" | AMS telemetry + Azure Monitor + ARM API |
| **Config validation** | "Run config checks for AB1" | STAF checks from GitHub, compared against collected VM configs |
| **Incident analysis** | "Why did HANA restart?" | Cross-layer log correlation via AMS |
| **Cost analysis** | "How much does AB1 cost?" | Azure Cost Management API |
| **Trend analysis** | "Show memory trends for AB1" | AMS time-series with regression |
| **Resiliency assessment** | "Assess AB1 resiliency" | Azure Advisor + ACSS checks |

## Architecture

```
┌────────────────────────────────────────────────┐
│ Azure SRE Agent (sre.azure.com)                │
│  13 Custom Skills + 39 Built-in Skills         │
│  Tools: ARM API, AMS KQL, Azure Monitor, CLI   │
│  Knowledge: SAP landscape inventory, SAP Notes  │
└──────────────┬─────────────────────────────────┘
               │ HTTPS + API Key
               ▼
┌────────────────────────────────────────────────┐
│ SRE Proxy (Container App)                      │
│  /api/validate   → full STAF config validation │
│  /api/staf-checks → STAF definitions from GitHub│
│  /api/command    → 14 read-only VM commands     │
│  /api/configs    → collected config files       │
│  Auth: sre-proxy-umi (Managed Identity)         │
└──────────────┬─────────────────────────────────┘
               │ ARM Run Command API
               ▼
┌────────────────────────────────────────────────┐
│ SAP VMs                                        │
│  /opt/sre/collect-sap-configs.sh (collector)   │
│  /opt/sre/sap-configs/{SID}/{host}/latest/     │
│  Auth: sre-collector-umi → blob upload          │
│  Cron: weekly collection + on-demand            │
└────────────────────────────────────────────────┘
```

## Prerequisites

- SAP workloads on Azure VMs (HANA + NetWeaver)
- Azure Monitor for SAP Solutions (AMS) configured
- Azure CLI installed
- Access to [sre.azure.com](https://sre.azure.com)

## Setup Guide

### Phase 1: SRE Agent Platform (Steps 1-9)

**Step 1: Create Agent** at [sre.azure.com](https://sre.azure.com) → New agent

**Step 2: Enable Built-in Tools** → Capabilities → Tools → Enable all (45/51, skip DevOps)

**Step 3: Enable Built-in Skills** → Capabilities → Skills → Enable all (39/42)

**Step 4: Add Connectors**

| Connector | Type | Purpose |
|-----------|------|---------|
| AMS Log Analytics workspace | Log Analytics | HANA/OS telemetry |
| sap-azure-sre-agent | GitHub repo | Skill source code |
| Microsoft Teams | Notification | Alert notifications (optional) |

**Step 5: Code Access** → Add GitHub repo `mcaps-microsoft/sap-azure-sre-agent`

**Step 6: Incident Platform** → Select Azure Monitor

**Step 7: Import Skills** → Skill Builder → New → paste each `skills/<name>/SKILL.md`:

| # | Skill | Purpose |
|---|-------|---------|
| 1 | sap-command-runner | 14 read-only VM commands via proxy |
| 2 | sap-landscape-discovery | System inventory from ARM |
| 3 | sap-operational-health | 5-layer health dashboard |
| 4 | sap-config-validator | STAF config compliance (calls /api/validate) |
| 5 | sap-ha-cluster-health | Pacemaker/HSR status |
| 6 | sap-incident-analysis | Cross-layer RCA |
| 7 | sap-resiliency-assessment | Advisor + ACSS checks |
| 8 | sap-performance-diagnostics | HANA memory/disk/savepoint |
| 9 | sap-deployment-readiness | SKU/quota/certification |
| 10 | sap-cost-analysis | Cost breakdown + savings |
| 11 | sap-trend-analysis | AMS trend projection |
| 12 | sap-self-healing | Log volume + backup + drift |
| 13 | sap-maintenance-handler | Graceful maintenance handling |

**Step 8: Managed Resources** → Add SAP RGs, AMS RG, proxy RG

**Step 9: Knowledge Sources** → Upload:
- `sap-landscape-inventory.json` (from `config/` template, filled with your SAP systems)
- SAP Note 1928533 PDF (VM/OS certification)
- HANA Hardware Directory PDF (HANA certified VMs)

### Phase 2: Proxy Infrastructure (Steps 10-14)

> Deploy in the **same subscription** as SAP VMs.

**Step 10: Deploy Proxy**

```powershell
git clone https://github.com/mcaps-microsoft/sap-azure-sre-agent.git
cd sap-azure-sre-agent
az account set --subscription "<sap-subscription-id>"

.\infra\deploy-sre-infra.ps1 `
    -SubscriptionId "<sap-subscription-id>" `
    -StorageAccountName "<globally-unique-name>"
```

Creates: `rg-sre-proxy` with Container App, ACR, storage, VNet, 2 managed identities.

**Step 11: Grant RBAC**

```powershell
# Custom role (read + VM Run Command only)
az role definition create --role-definition infra/sap-sre-agent-role.json

# Assign to SAP resource groups
az role assignment create --assignee-object-id <PROXY-UMI-PRINCIPAL-ID> \
    --role "Custom - SAP SRE Agent Operator" \
    --scope "/subscriptions/<sub>/resourceGroups/<sap-rg>"
```

**Step 12: Assign Collector Identity to SAP VMs**

```powershell
az vm identity assign -g <sap-rg> -n <vm-name> --identities <COLLECTOR-UMI-RESOURCE-ID>
```

**Step 13: Storage Firewall** — Add SAP VM subnet to storage network rules

**Step 14: Deploy Collector**

```powershell
$body = @{
    vm = "AB1vm"; rg = "RG_SAP_CUS_AB1"; command_id = "deploy_collector"
    subscription_id = "<sub-id>"; storage_account = "<storage>"
    umi_client_id = "<collector-umi-client-id>"
    sid = "AB1"; db_sid = "DB1"; roles = "db,ascs,pas"; hana_inst = "00"
} | ConvertTo-Json

Invoke-RestMethod "<proxy-url>/api/command" -Method Post `
    -Headers @{"X-API-Key"="<api-key>"; "Content-Type"="application/json"} -Body $body
```

This deploys to `/opt/sre/` on the VM:
```
/opt/sre/
├── collect-sap-configs.sh     # generic collector (same on all VMs)
├── run-collector.sh           # site-specific wrapper (SID, roles, instances)
├── sre.env                    # storage account + UMI credentials
├── collector.log              # collection log
├── staging/                   # temp archive (auto-cleaned)
└── sap-configs/               # mirrors blob: {SID}/{hostname}/latest/
    └── AB1/AB1vm/latest/
        ├── os/                # sysctl, fstab, chrony, IMDS, packages
        ├── hana/              # global.ini, nameserver.ini, profiles
        ├── cluster/           # corosync, pacemaker, SBD
        └── sap-profiles/      # DEFAULT.PFL, instance profiles
```

### Phase 3: Team Onboarding (Step 15-16)

**Step 15: Team Onboarding** → Fill `onboarding/team-onboarding.template.md` with proxy URL, API key, AMS workspace, SAP system details. Paste into Team Onboarding.

**Step 16: Verify**

| Test | Expected |
|------|----------|
| "Is AB1 healthy?" | 5-layer health dashboard |
| "Run config checks for AB1" | STAF compliance report (pass/fail per check) |
| "How much does AB1 cost?" | Cost breakdown |
| "Run uptime on AB1vm" | VM uptime via proxy |

## API Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/validate/{sid}/{hostname}` | **Full STAF config validation** (fresh collection + comparison) |
| GET | `/api/staf-checks` | STAF check definitions from GitHub (filtered by applicability) |
| POST | `/api/command` | Execute one of 14 allowed commands on a VM |
| POST | `/api/batch` | Execute up to 6 commands in one call |
| GET | `/api/configs/{sid}/{hostname}` | All collected config files for a VM |
| GET | `/api/commands` | List allowed commands |
| GET | `/api/diag` | MI + ARM connectivity test |

### Config Validation Flow (`/api/validate`)

```
GET /api/validate/AB1/AB1vm?os_type=SLES_SAP&roles=DB,SCS,PAS&db_type=HANA
    &storage_type=Premium_LRS&ha_type=false&ha_agent=none&rg=RG_SAP_CUS_AB1

1. STAF checks: GitHub live → cached blob snapshot fallback
2. Config data: trigger collector on VM → read blob → cached fallback
3. ARM data: query disk IOPS, NIC, PPG from Azure Resource Manager
4. Compare: actual vs expected (string, range, list validators)
5. Return: structured JSON report with pass/fail/not_evaluated per check
```

## Identities

| Identity | Assigned to | Purpose | RBAC |
|----------|------------|---------|------|
| SRE Agent MI | SRE Agent platform | Azure API queries | Reader on SAP RGs, Log Analytics Reader |
| sre-proxy-umi | Container App | VM commands + blob + ARM queries | Custom role on SAP RGs |
| sre-collector-umi | SAP VMs | Config upload to blob | Storage Blob Data Contributor |

## Security

- **Command allowlist**: 14 read-only commands hardcoded — no arbitrary shell execution
- **API key auth**: required for all proxy calls
- **Custom RBAC**: read + runCommand only — no VM delete/restart/write
- **No shared keys**: storage uses Entra ID auth only (MCAP compliant)
- **Audit logging**: every command logged with caller, VM, timestamp

## References

- [SAP Testing Automation Framework (STAF)](https://github.com/Azure/sap-automation-qa)
- [SAP on Azure Best Practices](https://learn.microsoft.com/en-us/azure/sap/workloads/)
- [Azure SRE Agent](https://sre.azure.com)
