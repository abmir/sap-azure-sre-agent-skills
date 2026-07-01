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

This is the full, click-by-click reference for every step. For the simple path, use the **Setup — 3 phases** in the [repository README](../README.md#setup--3-phases).


The setup has three phases:

| Phase | What | Where | Effort |
|-------|------|-------|--------|
| **1. Platform** | Create agent, enable tools, install skills, connect repo | sre.azure.com portal | ~30 min |
| **2. Infrastructure** | Deploy config store + collector (and optional MCP command proxy), identities | Azure CLI / PowerShell | ~20 min |
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

**Step 5: Connect this repo** → Point the agent at your fork of `mcaps-microsoft/sap-azure-sre-agent` via **Builder → Code Access** (see [This repo is the single source of truth](#this-repo-is-the-single-source-of-truth)). One connection lets the agent read `knowledge/` (incl. the SAP/HANA cert reference), `config/`, `docs/`, and the IaC + MCP proxy code, and cite them by file and commit during investigations.

> Repository connections live under **Code Access**, not Knowledge base (the portal moved them). **Knowledge base** is optional — use it only to upload extra files (e.g. a customer's own PDFs) that aren't in the repo.

**Step 6: Incident Platform** → Select Azure Monitor.

**Step 7: Install Skills from the Plugin Marketplace** → Builder → Plugins → **Add marketplace** (enter your fork URL) or **Install from URL**. Install the plugins for your tier — each skill auto-detects available infrastructure at runtime:

| Plugin | Install when… | Skills |
|--------|---------------|--------|
| **`sap-sre-core`** | Always (Tier 1+) | landscape-discovery, operational-health, cost-analysis, trend-analysis, resiliency-assessment, deployment-readiness, incident-analysis, performance-diagnostics, ha-cluster-health, maintenance-handler |
| **`sap-sre-config`** | You deployed a Storage Account (Tier 2+) | config-validator |
| **`sap-sre-proxy-ops`** | You deployed the MCP command proxy (Tier 3) | command-runner, self-healing |

Each install is pinned to the exact commit. To adopt later changes, click **Update** on the plugin. To author or edit skills, see [Updating Skills](#updating-skills).

**Step 8: Managed Resources** → Add: all SAP RGs, AMS RG, the config-store RG (`rg-sre-agent`), and — if you deploy live commands — the MCP proxy RG (`rg-sre-proxy-mcp`), both created in Phase 2.

**Step 9: Knowledge Sources (optional)** → Most knowledge already comes from the repo via Code Access (Step 5). The Knowledge base is only needed for material you'd rather upload than keep in the repo:
- `sap-landscape-inventory.json` — your filled-in landscape (from `config/sap-landscape-inventory.template.json`). Skip if it's in your fork (covered by Code Access) or published to blob by the collector.
- Any extra customer PDFs.

> SAP/HANA VM certification is **not** uploaded here — it lives in [`knowledge/sap-certified-vms.json`](knowledge/sap-certified-vms.json) and the `sap-deployment-readiness` skill fetches it live. SAP Note 1928533 is behind SAP login and can't be added as a web page; update the repo file via PR when SAP revises the Note.

---

### Phase 2 — Infrastructure (tier-dependent)

This phase depends on which adoption tier you chose ([Adoption planner](adoption-planner.md)):

- **Azure-Native only** — Skip this entire phase. There is nothing to deploy. Grant the SRE Agent Managed Identity `Reader` + `Monitoring Reader` on each SAP RG (and `Cost Management Reader` at subscription scope), then proceed to Phase 3.
- **+ Config Store** — Run `infra/deploy-sre-infra.ps1 -SreAgentUmiPrincipalId <agent-mi-object-id>`. This creates the resource group, collector UMI, and a private storage account with a `sap-configs` container, and grants the SRE Agent UMI `Storage Blob Data Reader` on it (direct config reads — no proxy). Then assign the collector UMI to SAP VMs (Step 12) and run the collector (Step 14). Skip Step 11 — there is no proxy UMI.
- **+ Live Commands** — Also run `infra/deploy-mcp-proxy.ps1` to stand up the MCP command proxy and register it as the `sap-sre-proxy` connector. Follow Steps 10b–11.

> **All Phase 2 resources go in the same subscription as your SAP VMs.** Cross-subscription is supported but adds RBAC complexity.

**Step 10a: Deploy the Config Store (storage + collector identity)**

```powershell
git clone https://github.com/<your-org>/sap-azure-sre-agent.git
cd sap-azure-sre-agent
az login
az account set --subscription "<sap-subscription-id>"

.\infra\deploy-sre-infra.ps1 `
    -SubscriptionId "<sap-subscription-id>" `
    -StorageAccountName "<globally-unique-name>" `           # 3-24 chars, lowercase + numbers
    -SreAgentUmiPrincipalId "<agent-mi-object-id-from-sre.azure.com>" `
    -SapSubnetIds @("<sap-subnet-resource-id>")              # optional: allow SAP subnets on the firewall
```

What it creates in `rg-sre-agent`:
- Storage account with `sap-configs` container (Entra ID auth only, no shared keys, firewall Deny + AzureServices bypass)
- Managed Identity `sre-collector-umi` (Storage Blob Data Contributor — VM blob upload)
- Grants the SRE Agent MI `Storage Blob Data Reader` on the storage account (direct config reads)

At completion the script prints the storage account name and the collector UMI client + resource IDs — you need them for Steps 12 and 14, and Phase 3.

**Step 10b: Deploy the MCP Command Proxy (only for + Live Commands)**

```powershell
.\infra\deploy-mcp-proxy.ps1 `
    -SubscriptionId "<sap-subscription-id>" `
    -SapResourceGroups "RG_SAP_CUS_AB1","RG_SAP_CUS_AB2" `
    -McpApiKey "<generate-a-strong-key>"
```

What it creates in `rg-sre-proxy-mcp`:
- VNet + delegated subnet for Container Apps
- ACR — builds the `sre-mcp:latest` image from `proxy-mcp/`
- Container App `sap-sre-mcp` (VNet-integrated) serving Streamable-HTTP MCP on `/mcp`
- Managed Identity `sre-mcp-umi` with the custom **SAP SRE Agent Operator** role on your SAP RGs (read + runCommand only — **no storage role**)

It prints the **MCP endpoint URL** and **API key**. Register them as a connector (Step 11) and put them in Team Onboarding (Step 15).

**Step 11: Register the MCP Connector (only for + Live Commands)**

The MCP proxy UMI is granted the SAP-RG role automatically by `deploy-mcp-proxy.ps1`. To make the tools available to the agent:

1. Install the **`sap-sre-proxy-ops`** plugin (Step 7) — it ships the [`.mcp.json`](../plugins/sap-sre-proxy-ops/.mcp.json).
2. **Builder → Connectors → Add connector → MCP Server** — enter the MCP endpoint URL and the `x-api-key` header (API key from Step 10b). Set the key as the `SAP_SRE_MCP_API_KEY` setting.
3. Once **Connected**, `sap-command-runner` and `sap-self-healing` call `list_allowed_commands` / `run_command` / `run_batch` natively.

**Step 12: Assign Collector UMI to SAP VMs**

```powershell
# Repeat for each SAP VM (DB, ASCS, ERS, PAS, AAS hosts)
az vm identity assign `
    -g "<sap-rg>" -n "<vm-name>" `
    --identities "<COLLECTOR-UMI-RESOURCE-ID>"
```

**Step 13: Storage Firewall — Add SAP VM Subnets**

If you didn't pass `-SapSubnetIds` in Step 10a, add the SAP VM subnet IDs now so the collector can upload:

```powershell
$sapSubnetId = az network vnet subnet show -g <sap-vnet-rg> --vnet-name <sap-vnet> -n <sap-subnet> --query id -o tsv
az storage account network-rule add --account-name "<storage>" --subnet $sapSubnetId
```

Also enable the `Microsoft.Storage` service endpoint on each SAP subnet.

**Step 14: Run the Collector on Each SAP VM**

The collector reads its target storage account and collector-UMI client ID from `/opt/sre/sre.env`, then uploads configs to blob. Install and run it via `az vm run-command` (no proxy needed):

```powershell
az vm run-command invoke `
    -g "<sap-rg>" -n "<vm-name>" `
    --command-id RunShellScript `
    --scripts @collector/collect-sap-configs.sh `
    --parameters "STORAGE_ACCOUNT=<storage>" "UMI_CLIENT_ID=<collector-umi-client-id>" `
                 "SID=AB1" "DB_SID=DB1" "ROLES=db,ascs,pas"
```

Schedule it weekly with cron (`0 2 * * 0`) so configs stay fresh. After a run the VM has:

```
/opt/sre/
├── collect-sap-configs.sh     # generic collector (same on all VMs)
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

To trigger a collection immediately on a VM:

```bash
ssh <vm> "sudo /opt/sre/collect-sap-configs.sh && tail -20 /opt/sre/collector.log"
```

`roles` values per VM: `db` (HANA), `ascs` (ASCS/SCS), `ers` (ERS), `pas` (Primary App Server), `aas` (Additional App Server), `sbd` (SBD-only host). Comma-separated for combined roles.

---

### Phase 3 — Team Onboarding & Verification (Steps 15–17)

**Step 15: Fill Team Onboarding Template**

Edit `onboarding/team-onboarding.template.md` with:
- Your **deployed infrastructure** — add Storage Account and/or MCP Command Proxy lines to the `## Deployed Infrastructure` section
- If Storage Account deployed: storage account name + `sap-configs` container
- If MCP command proxy deployed: MCP endpoint URL + API key (from Step 10b output)
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

+ Live Commands (MCP command proxy deployed):

| # | Test | Expected |
|---|------|----------|
| 5 | `curl https://<proxy>/mcp` (no key) | Rejected — the MCP endpoint requires the `x-api-key` header |
| 6 | In sre.azure.com → Connectors, the `sap-sre-proxy` connector shows **Connected** | Green / connected |
| 7 | At sre.azure.com: "What commands are available?" | `list_allowed_commands` returns the 14-command allowlist |
| 8 | At sre.azure.com: "Run uptime on AB1vm" | VM uptime via the MCP command proxy (`run_command`) |

**Step 17: Verify Collector is Running (requires Storage Account)**

```powershell
# Trigger fresh collection on a VM (az vm run-command — no proxy needed)
az vm run-command invoke -g <SAP-RG> -n <vm-name> --command-id RunShellScript `
    --scripts "sudo /opt/sre/collect-sap-configs.sh && tail -20 /opt/sre/collector.log"

# Check the blob has fresh configs
az storage blob list --account-name <storage> --container-name sap-configs `
    --prefix "<SID>/<host>/latest/" --auth-mode login `
    --query "[].{name:name, modified:properties.lastModified}" -o table
```


---

