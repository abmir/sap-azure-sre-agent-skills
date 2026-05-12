# SAP Azure SRE Agent Skills

15 SRE skills for SAP HANA and NetWeaver workloads on Azure. Import via Plugin Marketplace in 1 click.

## Prerequisites

- Existing SAP workloads on Azure VMs (HANA + NetWeaver running)
- Azure Monitor for SAP Solutions (AMS) with HANA provider configured
- A VNet subnet delegated to `Microsoft.Web/serverFarms` (for function app integration)
- Azure SRE Agent created at [sre.azure.com](https://sre.azure.com)

## Setup (4 Steps)

### 1. Deploy Infrastructure

```powershell
git clone https://github.com/<org>/sap-azure-sre-agent-skills.git
cd sap-azure-sre-agent-skills

.\infra\deploy-sre-infra.ps1 `
    -SubscriptionId "<your-subscription-id>" `
    -StorageAccountName "<globally-unique-name>" `
    -IntegrationSubnetId "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet>"

# Deploy function code
cd proxy/sre-config-proxy && func azure functionapp publish <config-proxy-name> --python
cd ../sre-command-proxy && func azure functionapp publish <command-proxy-name> --python
```

**Assign RBAC on each SAP resource group:**

| Role | Scope | Purpose |
|------|-------|---------|
| Reader | Each SAP resource group | VM discovery |
| Virtual Machine Contributor | Each SAP resource group | VM Run Command execution |

```powershell
az role assignment create --assignee-object-id <UMI-PRINCIPAL-ID> --role "Reader" --scope "/subscriptions/<sap-sub>/resourceGroups/<sap-rg>"
az role assignment create --assignee-object-id <UMI-PRINCIPAL-ID> --role "Virtual Machine Contributor" --scope "/subscriptions/<sap-sub>/resourceGroups/<sap-rg>"
```

### 2. Import Skills

1. SRE Agent portal → **Plugins** → **Manage marketplaces** → **Add**
2. Enter: `<org>/sap-azure-sre-agent-skills` → **Resolve** → **Add**
3. Click plugin → **Install** (imports all 15 skills)

> **Note:** Repo must be public. Uncheck "Supported" filter to see skills-only plugins.

### 3. Configure SRE Agent

- **Managed Resources:** Add SAP resource groups + AMS resource group
- **Connectors:** Add Log Analytics Workspace (AMS workspace)
- **Knowledge Sources:** Upload `config/sap-landscape-inventory.template.json` (filled in with your SAP systems)
- **Team Onboarding:** Paste filled-in `onboarding/team-onboarding.template.md` with proxy URLs, API key, subscription ID

### 4. Deploy Collector to SAP VMs

The collector script gathers SAP/HANA/OS configs from each VM and uploads to blob storage weekly.

**a) Download the collector script** (already uploaded to storage by the deploy script):

```bash
# On each SAP VM (as root):
mkdir -p /opt/sre
az login --identity --username <UMI-CLIENT-ID> --output none
az storage blob download --account-name <STORAGE-ACCOUNT> --container-name sap-configs \
    --name scripts/collect-sap-configs.sh --file /opt/sre/collect-sap-configs.sh --auth-mode login
chmod +x /opt/sre/collect-sap-configs.sh
```

**b) Create the environment file** (`/opt/sre/sre.env`):

```bash
cat > /opt/sre/sre.env << 'EOF'
SRE_STORAGE_ACCOUNT=<your-storage-account-name>
SRE_CONTAINER=sap-configs
SRE_UMI_CLIENT_ID=<your-umi-client-id>
EOF
chmod 600 /opt/sre/sre.env
```

**c) Create the cron wrapper** (`/opt/sre/run-collector.sh`):

```bash
cat > /opt/sre/run-collector.sh << 'EOF'
#!/bin/bash
source /opt/sre/sre.env

# === EDIT: Set values for THIS VM ===
SID="AB1"           # SAP System ID
DB_SID="DB1"         # HANA Database SID
ROLES="db,ascs,app"  # Roles on this VM: db, ascs, app, sbd (comma-separated)
HANA_INST="00"       # HANA instance number (db role only)
ASCS_INST="01"       # ASCS instance number (ascs role only)
APP_INST="02"        # App instance number (app role only)
# ====================================

ARGS="--sid $SID --db-sid $DB_SID --roles $ROLES"
[ -n "$HANA_INST" ] && ARGS="$ARGS --hana-inst $HANA_INST"
[ -n "$ASCS_INST" ] && ARGS="$ARGS --ascs-inst $ASCS_INST"
[ -n "$APP_INST" ]  && ARGS="$ARGS --app-inst $APP_INST"

/opt/sre/collect-sap-configs.sh $ARGS >> /var/log/sre-config-collect.log 2>&1
EOF
chmod +x /opt/sre/run-collector.sh
```

**d) Set up weekly cron** (every Sunday at 2:00 AM):

```bash
echo "0 2 * * 0 root /opt/sre/run-collector.sh" > /etc/cron.d/sre-collector
chmod 644 /etc/cron.d/sre-collector
```

**e) Assign the UMI to the VM** (so it can upload to blob storage):

```powershell
az vm identity assign -g <sap-rg> -n <vm-name> --identities <UMI-RESOURCE-ID>
```

**f) Add SAP VM subnet to storage firewall** (so VMs can reach the storage account):

```powershell
az storage account network-rule add --account-name <STORAGE-ACCOUNT> \
    --subnet "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<sap-subnet>"
```

## What Gets Created

```
RG_SRE_OPS
├── sre-ops-umi              User-Assigned Managed Identity
├── <storage-account>        Storage Account (SAP configs, shared key disabled)
├── sre-ops-plan             App Service Plan (B1 Linux)
├── <config-proxy>           Function App (reads SAP configs from blob)
├── <command-proxy>          Function App (executes allowlisted commands on SAP VMs)
└── 2x Application Insights  Auto-created with function apps
```

## RBAC Summary

| Identity | Role | Scope | Purpose |
|----------|------|-------|---------|
| sre-ops-umi | Storage Blob Data Owner | Storage account | Runtime storage + config read/write |
| sre-ops-umi | Storage Queue/Table Data Contributor | Storage account | Function runtime |
| sre-ops-umi | Reader | Each SAP RG | VM discovery |
| sre-ops-umi | Virtual Machine Contributor | Each SAP RG | VM Run Command |
| agent-mi (auto) | Reader | SAP RGs + AMS RG | Azure API calls |
| agent-mi (auto) | Log Analytics Reader | AMS workspace | KQL queries |

## Skill Catalog

| Skill | Tier | Description |
|-------|------|-------------|
| SAP Landscape Discovery | T1 | System inventory, VM power state |
| SAP Operational Health | T1 | AMS health, VM metrics, alert coverage |
| SAP Performance Diagnostics | T1 | HANA memory, SQL probe, disk IOPS |
| SAP Deployment Readiness | T1 | SKU check, quota, zone availability |
| SAP Resiliency Assessment | T1 | HA architecture, zone coverage, SPOFs |
| SAP Cost Insights | T1 | Cost breakdown, RI coverage, savings |
| SAP Incident RCA | T2 | Cross-layer root cause analysis |
| SAP Configuration Guardian | T1+T3 | 59 config checks aligned with STAF |
| SAP HA & DR Guardian | T1+T3 | Pacemaker, HSR, failover forensics |
| SAP Anomaly Forecaster | T3 | Memory/disk trend projection |
| SAP Maintenance Autopilot | T4 | Graceful shutdown for scheduled maintenance |
| SAP Self-Healing | T4 | Log volume full, backup stale, sysctl drift |
| SAP Command Executor | Internal | 15 allowlisted VM commands via proxy |
| SAP ServiceNow Connector | Internal | ITSM integration (if configured) |
| SAP APM Connector | Internal | Dynatrace/Grafana integration (if configured) |

## Security

- **Command allowlist:** Only 15 specific commands can run on SAP VMs. No arbitrary shell execution.
- **No shared keys:** Storage uses identity-based auth. No secrets in code.
- **Network isolation:** Storage firewall Deny by default. Only IntegrationSubnet + SAP VM subnets allowed.
- **Input sanitization:** All parameters shell-quoted (`shlex.quote`) and regex-validated.
- **Audit trail:** Every command execution logged to Application Insights.
