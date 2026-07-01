# Detailed setup (Steps 1-17)

## Prerequisites

- **SAP workloads on Azure** — VMs running HANA and/or NetWeaver
- **SAP telemetry (recommended, not required)** — [Azure Monitor for SAP Solutions (AMS)](https://learn.microsoft.com/azure/sap/monitor/quickstart-portal) with HANA + OS providers gives the deepest SAP signals. If your SAP-app telemetry lives elsewhere (SAP Cloud ALM / Focus Run, Dynatrace, Sentinel), you can start without AMS and bring that source in later as a connector — telemetry-dependent skills fall back to Azure platform metrics meanwhile. See the [Adoption planner](adoption-planner.md).
- **Azure CLI** — installed and logged in (`az login`)
- **PowerShell 7+** — for deployment scripts
- **Access to [sre.azure.com](https://sre.azure.com)** — agent platform
- **GitHub access** to this repo (fork to your org for Code Access integration)
- **Permissions** — Owner or Contributor + User Access Administrator on the deployment subscription

---

## Setup Guide

This is the full, click-by-click reference for every step. For the simple path, use the **Quick start — 3 phases** in the [repository README](../README.md#quick-start--implement-in-3-phases).


The setup has three phases:

| Phase | What | Where | Effort |
|-------|------|-------|--------|
| **1. Platform** | Create agent, enable tools, install skills, connect repo | sre.azure.com portal | ~30 min |
| **2. Infrastructure** | Deploy storage + collector (and optional proxy), identities | Azure CLI / PowerShell | ~20 min |
| **3. Onboarding** | Paste team context, verify end-to-end | sre.azure.com portal | ~10 min |

---

### Phase 1 — SRE Agent Platform (Steps 1–9)

**Step 1: Create Agent** at [sre.azure.com](https://sre.azure.com) → New agent. Name it (e.g. `sap-sre-agent`), select a region, and assign it to a resource group.

**Step 2: Enable Built-in Tools** → Capabilities → Tools → Enable all (45/51, skip DevOps tools).

**Step 3: Enable Built-in Skills** → Capabilities → Skills → Enable all (39/42, skip web-search if not needed).

**Step 4: Add Connectors**

| Connector | Type | Value |
|-----------|------|-------|
| AMS Log Analytics workspace | Log Analytics | Workspace ID of your AMS LAW |
| Microsoft Teams (optional) | Notification | For alert delivery |

**Step 5: Connect this repo** → Point the agent at your fork of `mcaps-microsoft/sap-azure-sre-agent` via **Builder → Code Access** (see [This repo is the single source of truth](#this-repo-is-the-single-source-of-truth)). One connection lets the agent read `knowledge/` (incl. the SAP/HANA cert reference), `config/`, `docs/`, and the proxy/IaC code, and cite them by file and commit during investigations.

> Repository connections live under **Code Access**, not Knowledge base (the portal moved them). **Knowledge base** is optional — use it only to upload extra files (e.g. a customer's own PDFs) that aren't in the repo.

**Step 6: Incident Platform** → Select Azure Monitor.

**Step 7: Install Skills from the Plugin Marketplace** → Builder → Plugins → **Add marketplace** (enter your fork URL) or **Install from URL**. Install the plugins for your tier — each skill auto-detects available infrastructure at runtime:

| Plugin | Install when… | Skills |
|--------|---------------|--------|
| **`sap-sre-core`** | Always (Tier 1+) | landscape-discovery, operational-health, cost-analysis, trend-analysis, resiliency-assessment, deployment-readiness, incident-analysis, performance-diagnostics, ha-cluster-health, maintenance-handler |
| **`sap-sre-config`** | You deployed a Storage Account (Tier 2+) | config-validator |
| **`sap-sre-proxy-ops`** | You deployed the SRE Proxy (Tier 3) | command-runner, self-healing |

Each install is pinned to the exact commit. To adopt later changes, click **Update** on the plugin. To author or edit skills, see [Updating Skills](#updating-skills).

**Step 8: Managed Resources** → Add: all SAP RGs, AMS RG, `rg-sre-proxy` (created in Phase 2).

**Step 9: Knowledge Sources (optional)** → Most knowledge already comes from the repo via Code Access (Step 5). The Knowledge base is only needed for material you'd rather upload than keep in the repo:
- `sap-landscape-inventory.json` — your filled-in landscape (from `config/sap-landscape-inventory.template.json`). Skip if it's in your fork (covered by Code Access) or published to blob by the collector.
- Any extra customer PDFs.

> SAP/HANA VM certification is **not** uploaded here — it lives in [`knowledge/sap-certified-vms.json`](knowledge/sap-certified-vms.json) and the `sap-deployment-readiness` skill fetches it live. SAP Note 1928533 is behind SAP login and can't be added as a web page; update the repo file via PR when SAP revises the Note.

---

### Phase 2 — Infrastructure (tier-dependent)

This phase depends on which adoption tier you chose ([Adoption planner](adoption-planner.md)):

- **Azure-Native only** — Skip this entire phase. There is nothing to deploy. Grant the SRE Agent Managed Identity `Reader` + `Monitoring Reader` on each SAP RG (and `Cost Management Reader` at subscription scope), then proceed to Phase 3.
- **+ Config Store** — Run the deploy script with `-Mode ConfigStore -SreAgentUmiPrincipalId <agent-mi-object-id>`. This creates only the resource group, collector UMI, storage account with `sap-configs` container, custom RBAC role, and grants the SRE Agent UMI `Storage Blob Data Reader` on the container. Then assign the collector UMI to SAP VMs (Step 12) and install the collector (Step 14 alt: use `az vm run-command` instead of the proxy). Skip Steps 11 and 13 — no proxy means no proxy UMI to grant on SAP RGs, and no storage firewall rules are needed for the proxy subnet.
- **+ Live Proxy** — Default. Run the deploy script with no `-Mode` flag (or `-Mode Full`). Follow all of Steps 10–14 below.

> **All Phase 2 resources go in the same subscription as your SAP VMs.** Cross-subscription is supported but adds RBAC complexity.

**Step 10: Deploy Proxy + Storage + Identities**

```powershell
git clone https://github.com/<your-org>/sap-azure-sre-agent.git
cd sap-azure-sre-agent
az login
az account set --subscription "<sap-subscription-id>"

# Full (default — deploys storage + proxy):
.\ infra\deploy-sre-infra.ps1 `
    -SubscriptionId "<sap-subscription-id>" `
    -StorageAccountName "<globally-unique-name>"   # 3-24 chars, lowercase + numbers

# Config Store only (no proxy):
.\infra\deploy-sre-infra.ps1 `
    -Mode ConfigStore `
    -SubscriptionId "<sap-subscription-id>" `
    -StorageAccountName "<globally-unique-name>" `
    -SreAgentUmiPrincipalId "<agent-mi-object-id-from-sre.azure.com>"

# Azure-Native (prints manual steps and exits, no infra deployed):
.\infra\deploy-sre-infra.ps1 -Mode AzureNative -SubscriptionId "<sap-subscription-id>"
```

What it creates in `rg-sre-proxy` (Full deploy):
- VNet `vnet-sre-ops` (10.60.0.0/16) with delegated subnet for Container Apps
- Storage account with `sap-configs` container (Entra ID auth only, no shared keys)
- ACR `acrsreproxy<sub-prefix>` (Premium) — builds `sre-proxy:latest` image
- Container App `sap-sre-proxy` with VNet integration
- Managed Identities: `sre-proxy-umi` (proxy ARM/blob), `sre-collector-umi` (VM blob upload)
- Custom RBAC role: **Custom - SAP SRE Agent Operator** (read + runCommand only)

**📋 At completion the script prints these values — copy them, you need them for steps 11–14 and Phase 3:**

```
SRE Proxy:       https://sap-sre-proxy.<env-suffix>.<region>.azurecontainerapps.io
API Key:         <generated-key>
Proxy UMI:       Client <guid>  Principal <guid>
Collector UMI:   Client <guid>  Resource <full-resource-id>
Storage:         <your-storage-name>
Custom RBAC:     Custom - SAP SRE Agent Operator
```

**Step 11: Grant Proxy UMI Access to SAP Resource Groups**

```powershell
# Repeat for each SAP RG (e.g., RG_SAP_CUS_AB1, RG_SAP_CUS_AB2, ...)
az role assignment create `
    --assignee-object-id "<PROXY-UMI-PRINCIPAL-ID>" `
    --assignee-principal-type ServicePrincipal `
    --role "Custom - SAP SRE Agent Operator" `
    --scope "/subscriptions/<sub>/resourceGroups/<sap-rg>"
```

**Step 12: Assign Collector UMI to SAP VMs**

```powershell
# Repeat for each SAP VM (DB, ASCS, ERS, PAS, AAS hosts)
az vm identity assign `
    -g "<sap-rg>" -n "<vm-name>" `
    --identities "<COLLECTOR-UMI-RESOURCE-ID>"
```

**Step 13: Storage Firewall — Add SAP VM Subnets**

If your SAP VMs aren't in the proxy VNet, add their subnet IDs to the storage firewall so the collector can upload:

```powershell
$sapSubnetId = az network vnet subnet show -g <sap-vnet-rg> --vnet-name <sap-vnet> -n <sap-subnet> --query id -o tsv
az storage account network-rule add --account-name "<storage>" --subnet $sapSubnetId
```

Also enable the `Microsoft.Storage` service endpoint on each SAP subnet.

**Step 14: Deploy Collector to Each SAP VM**

```powershell
$body = @{
    vm = "AB1vm"; rg = "RG_SAP_CUS_AB1"; command_id = "deploy_collector"
    subscription_id = "<sub-id>"
    storage_account = "<storage>"
    umi_client_id  = "<collector-umi-client-id>"
    sid = "AB1"; db_sid = "DB1"; roles = "db,ascs,pas"
    hana_inst = "00"; ascs_inst = "00"; app_inst = "00"
} | ConvertTo-Json

Invoke-RestMethod "<proxy-url>/api/command" -Method Post `
    -Headers @{"X-API-Key"="<api-key>"; "Content-Type"="application/json"} `
    -Body $body
```

The proxy embeds the collector script (base64-encoded) and installs it inline — no blob download dependency. After deployment, the VM has:

```
/opt/sre/
├── collect-sap-configs.sh     # generic collector (same on all VMs)
├── run-collector.sh           # site-specific wrapper (SID, roles, instances)
├── sre.env                    # storage account + UMI client ID
├── collector.log              # collection log (rotated weekly, 12 keep)
├── staging/                   # temp archive (auto-cleaned after upload)
└── sap-configs/               # mirrors blob: {SID}/{hostname}/latest/
    └── AB1/AB1vm/latest/
        ├── os/                # sysctl, fstab, chrony, IMDS, packages
        ├── hana/              # global.ini, nameserver.ini, profiles
        ├── cluster/           # corosync, pacemaker, SBD
        └── sap-profiles/      # DEFAULT.PFL, instance profiles
```

Cron runs `0 2 * * 0` (weekly Sunday 02:00 local). To trigger immediately:

```bash
ssh <vm> "sudo /opt/sre/run-collector.sh && tail -20 /opt/sre/collector.log"
```

`roles` values per VM: `db` (HANA), `ascs` (ASCS/SCS), `ers` (ERS), `pas` (Primary App Server), `aas` (Additional App Server), `sbd` (SBD-only host). Comma-separated for combined roles.

---

### Phase 3 — Team Onboarding & Verification (Steps 15–17)

**Step 15: Fill Team Onboarding Template**

Edit `onboarding/team-onboarding.template.md` with:
- Your **deployed infrastructure** — add Storage Account and/or SRE Proxy lines to the `## Deployed Infrastructure` section
- If Storage Account deployed: storage account name + `sap-configs` container
- If SRE Proxy deployed: Proxy URL + API key (from Step 10 output) + Proxy UMI client/principal IDs
- AMS workspace ID + provider instance names
- SAP landscape table (SID, RG, VMs, roles, IPs)
- Subscription ID

Paste the filled template into sre.azure.com → Settings → Team Onboarding.

**Step 16: Quick Verification (3 minutes)**

Azure-Native (no infra deployed):

| # | Test | Expected |
|---|------|----------|
| 1 | At sre.azure.com: "Is AB1 healthy?" | 5-layer health dashboard |
| 2 | At sre.azure.com: "How much does AB1 cost?" | Cost breakdown |
| 3 | At sre.azure.com: "Run config checks for AB1" | Skill reports that a Storage Account is needed |

+ Config Store (Storage Account deployed):

| # | Test | Expected |
|---|------|----------|
| 1 | `az storage blob list --account-name <storage> --container-name sap-configs --prefix "<SID>/<host>/latest/" --auth-mode login` | Lists collected config files |
| 2 | At sre.azure.com: "Is AB1 healthy?" | 5-layer health dashboard |
| 3 | At sre.azure.com: "Run config checks for AB1" | STAF compliance report (skill pulled STAF YAML live from GitHub, compared against blob configs) |
| 4 | At sre.azure.com: "How much does AB1 cost?" | Cost breakdown |

+ Live Proxy (all Config Store tests above PLUS):

| # | Test | Expected |
|---|------|----------|
| 5 | `curl https://<proxy>/api/health` | `{"status":"healthy"}` |
| 6 | `curl -H "X-API-Key: <key>" https://<proxy>/api/diag` | MI token + ARM call both OK |
| 7 | `curl -H "X-API-Key: <key>" https://<proxy>/api/configs/<sid>/<host>` | List of config files in blob (fallback path; skills with Storage Account read blob directly) |
| 8 | At sre.azure.com: "Run uptime on AB1vm" | VM uptime via proxy |

**Step 17: Verify Collector is Running (requires Storage Account)**

```powershell
# Trigger fresh collection on a VM (without proxy — use az vm run-command; with proxy — POST /api/command with run_collector)
az vm run-command invoke -g <SAP-RG> -n <vm-name> --command-id RunShellScript `
    --scripts "sudo /opt/sre/run-collector.sh && tail -20 /opt/sre/collector.log"

# Check the blob has fresh configs
az storage blob list --account-name <storage> --container-name sap-configs `
    --prefix "<SID>/<host>/latest/" --auth-mode login `
    --query "[].{name:name, modified:properties.lastModified}" -o table
```


---

