# SAP Azure SRE Agent

AI-powered SRE agent for SAP HANA and NetWeaver on Azure. Automates health monitoring, STAF configuration validation, incident analysis, and cost optimization — all through natural language at [sre.azure.com](https://sre.azure.com).

**Three adoption tiers** — pick what fits your security posture. Start with zero-infrastructure Azure-native telemetry, add a customer-controlled config store when you want STAF compliance, add a brokered command proxy when you’re ready for live remediation. See [Adoption Tiers](#adoption-tiers) below.

## This repo is the single source of truth

This repository **is** the agent's skill pack. You don't upload skills by hand — you **fork this repo and point your SRE Agent at it**, then install the skills you need. Updates ship as pull requests; each agent picks them up on its own schedule.

The agent consumes this repo through three independent GitHub paths (use whichever you need):

| Path | Portal location | What it pulls from this repo |
|------|-----------------|------------------------------|
| **Plugin Marketplace** | Builder → Plugins | **Skills** (and any MCP server configs) — the tiered plugins in [`plugins/`](plugins/) via [`.github/plugin/marketplace.json`](.github/plugin/marketplace.json) |
| **Knowledge base → Add repository** | Builder → Knowledge base | **Knowledge** — the inventory template, `docs/`, and references (see [`knowledge/`](knowledge/README.md)) |
| **Code Access** (optional) | Builder → Code Access | **Code** — proxy app, collector, IaC — so RCA can cite exact files/commits |

> **Version pinning (important):** Plugin installs are pinned to the exact git commit at install time. Changes you merge here do **not** reach an agent until someone clicks **Update** on that plugin (the portal diffs by SHA-256 hash). This is by design — it gives you production stability, staged rollouts (update dev before prod), and version diversity across agents. It is *not* a live, auto-propagating feed.

### Tiered plugins

| Plugin | Tier | Skills | Requires |
|--------|------|:------:|----------|
| [`sap-sre-core`](plugins/sap-sre-core) | Azure-Native | 10 | Nothing (Azure APIs + AMS) |
| [`sap-sre-config`](plugins/sap-sre-config) | + Config Store | 1 | Storage Account |
| [`sap-sre-proxy-ops`](plugins/sap-sre-proxy-ops) | + Live Proxy | 2 | SRE Proxy + custom RBAC |

Install only the plugins your tier supports. A security-strict customer installs just `sap-sre-core` and never sees the proxy skills.

## Architecture

![Azure SRE Agent for SAP Workloads — architecture](docs/sap-on-azure-sre-agent.png)

```
┌──────────────────────────────────────────────────────────┐
│ Azure SRE Agent (sre.azure.com)                          │
│  13 Custom Skills + 39 Built-in Skills                   │
│  Tools: ARM API, AMS KQL, Azure Monitor, CLI, Python     │
│  Knowledge: SAP landscape, SAP Notes, STAF references    │
└──────────┬────────────────┬────────────────┬─────────────┘
           │ Azure APIs   │ + Config Store │ + Live Proxy
           │ (always on)  │ (storage)      │ (proxy)
           ▼                ▼                ▼
   ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐
   │ Azure APIs  │  │ Config Store │  │ SRE Proxy        │
   │ + AMS / LAW │  │ (Storage)    │  │ (Container App)  │
   │ ARM, Cost   │  │ sap-configs/ │  │ /api/command     │
   │ Monitor,    │  │   SID/host/  │  │ /api/batch       │
   │ Advisor     │  │   latest/    │  │ 14-cmd allowlist │
   │             │  │ Agent UMI:   │  │ Entra ID + API   │
   │             │  │ Blob Reader  │  │ proxy-umi        │
   └─────────────┘  └──────┬───────┘  └────────┬─────────┘
                           │ upload            │ ARM
                           │ (weekly cron +    │ run-command
                           │  on-demand)       │ (read-only)
                           ▼                   ▼
                   ┌────────────────────────────────────┐
                   │ SAP VMs                            │
                   │  /opt/sre/collect-sap-configs.sh   │
                   │  collector-umi (Blob Contributor)  │
                   └────────────────────────────────────┘
```

STAF check definitions live in the public [`Azure/sap-automation-qa`](https://github.com/Azure/sap-automation-qa) repo and are pulled live by the Config Validator skill (requires a Storage Account) — they are not hosted by Microsoft inside this stack.

## What It Does

| Capability | Example Prompt | Requires | How It Works |
|-----------|----------------|:--------:|-------------|
| Landscape discovery | "What SAP systems do I have?" | — | Azure Resource Graph |
| Health monitoring | "Is AB1 healthy?" | — | AMS telemetry + Azure Monitor + ARM API |
| Cost analysis | "How much does AB1 cost?" | — | Azure Cost Management API |
| Trend analysis | "Show memory trends for AB1" | — | AMS time-series with regression |
| Resiliency assessment | "Assess AB1 resiliency" | — | Azure Advisor + ACSS checks |
| Deployment readiness | "Can I deploy Standard_M32 in eastus?" | — | SKU / quota / HANA certification |
| Incident analysis (basic) | "Why did HANA restart?" | — | AMS + Activity Log correlation |
| Performance diagnostics (basic) | "Why is AB1 slow?" | — | AMS + Azure Monitor metrics |
| HA cluster health (basic) | "Cluster status for AB1" | — | AMS + Resource Graph |
| Maintenance handler (basic) | "Any scheduled maintenance?" | — | Scheduled Events API + Service Health |
| **Config validation (STAF)** | "Run config checks for AB1" | **Storage Account** | STAF YAML fetched live from `Azure/sap-automation-qa` on GitHub, compared against blob-stored VM configs — entirely in-skill, no proxy |
| **Config-enriched RCA / perf / cluster** | "Cross-layer RCA for AB1" | Storage Account | Adds stored config files (sysctl, global.ini, corosync) to incident / performance / HA skills |
| **Live VM commands** | "Run uptime on AB1vm" | **SRE Proxy** | Proxy → ARM run-command API (14-command allowlist, read-only) |
| **Self-healing remediation** | Auto: `/hana/log` full → log backup | SRE Proxy | Proxy executes restricted write commands within strict guardrails |

## Adoption Tiers

The agent supports three deployment tiers. Each tier is a strict superset of the previous one — start with Azure-native, add a config store when you need config-level visibility, add the proxy when you're ready for live commands. Skills auto-detect what's available from the `## Deployed Infrastructure` section in Team Onboarding — no mode numbers to manage.

| Tier | What's Deployed | Capabilities | Customer Profile | Effort |
|:----:|----------------|--------------|------------------|:------:|
| **Azure-Native** | Nothing (just `sre.azure.com` + your existing AMS) | 10 skills using only Azure APIs and AMS telemetry. No config validation, no live commands. | Security-strict customers. AMS already deployed. Wants insight without standing up new infra. | ~30 min |
| **+ Config Store** | **Storage Account** (`sap-configs` blob container) + **collector UMI** (assigned to SAP VMs) | All base skills + **STAF config validation** + config-enriched RCA / performance / HA skills. 11 skills total. | Wants STAF compliance reporting and richer RCA, but does not want a proxy executing live commands. | ~1 hr |
| **+ Live Proxy** | Config Store + **Container App proxy** (FastAPI, VNet-integrated) + **proxy UMI** + Entra ID Easy Auth | All skills including **live read-only VM commands** + **self-healing remediation**. All 13 skills. | Wants full SRE automation including remediation. Accepts a brokered command path with custom RBAC. | ~2 hr |

### How config validation works

- **No Storage Account** — Config Validator tells the user a storage account is needed.
- **Storage Account deployed** — The Config Validator skill **fetches STAF check definitions directly from `Azure/sap-automation-qa` on GitHub at runtime**, reads collected configs from your storage account, and runs the comparison **in-skill** (Python via `ExecutePythonCode`). No proxy involved.
- **Proxy also deployed** — Same as above, with the added option to trigger a fresh on-demand collection through the proxy before validating.

### Adding infrastructure later

- **Add Config Store** — Run the deploy script with `-Mode ConfigStore -SreAgentUmiPrincipalId <agent-mi-object-id>`; assign the collector UMI to SAP VMs; deploy the collector. Add the `Storage Account` line to Team Onboarding. Import the Config Validator skill on the portal.
- **Add Live Proxy** — Run the deploy script with `-Mode Full`; add the `SRE Proxy` line to Team Onboarding. Import `sap-command-runner` and `sap-self-healing` skills.

### Skills × Infrastructure capability matrix

How each of the 13 custom skills behaves based on what infrastructure is deployed. **Full** = skill operates at its full design intent. **Enhanced** = skill adds config or live data. **Blocked** = skill refuses politely and explains what's needed.

| # | Skill | No infra | + Storage Account | + SRE Proxy | Notes |
|:-:|------|:------:|:------:|:------:|------|
| 1 | `sap-landscape-discovery` | ✅ Full | ✅ Full | ✅ Full | Pure ARM API |
| 2 | `sap-operational-health` | ✅ Full | ✅ Full | ✅ + live | Already has graceful proxy fallback |
| 3 | `sap-cost-analysis` | ✅ Full | ✅ Full | ✅ Full | Pure Cost Management API |
| 4 | `sap-trend-analysis` | ✅ Full | ✅ Full | ✅ Full | Pure AMS KQL |
| 5 | `sap-deployment-readiness` | ✅ Full | ✅ Full | ✅ Full | Pure ARM + SAP Notes |
| 6 | `sap-resiliency-assessment` | ✅ Full | ✅ Full | ✅ Full | Pure Advisor + ACSS |
| 7 | `sap-incident-analysis` | ⚠️ AMS-only | ✅ + stored configs | ✅ + live OS state | Storage adds sysctl / global.ini / corosync context |
| 8 | `sap-maintenance-handler` | ⚠️ Detect-only | ✅ + pre-checks | ✅ Full | Storage adds config pre-flight; proxy enables execution |
| 9 | `sap-performance-diagnostics` | ⚠️ Metrics-only | ✅ + HANA configs | ✅ + live HANA SQL | Proxy adds live `hdbsql` |
| 10 | `sap-ha-cluster-health` | ⚠️ ILB probes only | ✅ + corosync / SBD | ✅ + live `crm_mon` | No infra = load balancer probes + AMS only |
| 11 | `sap-config-validator` | ❌ Blocked | ✅ In-skill (blob + GitHub) | ✅ + on-demand collection | STAF YAML fetched from GitHub at runtime — no proxy required |
| 12 | `sap-command-runner` | ❌ Blocked | ❌ Blocked | ✅ Full | Needs the proxy to reach VMs |
| 13 | `sap-self-healing` | ❌ Blocked | ❌ Blocked | ✅ Full | Needs the proxy to execute remediation |

**Net skill counts**: No infra = 6 full + 4 degraded = 10 working. + Storage = 11 full + 2 blocked. + Storage + Proxy = 13 full.

## Repository Layout

```
sap-azure-sre-agent/
├── .github/plugin/
│   └── marketplace.json         # Plugin Marketplace manifest — catalog of the 3 tiered plugins
├── plugins/                     # The agent's skill pack (what the Plugin Marketplace installs)
│   ├── sap-sre-core/            #   Tier 1 — 10 Azure-native skills (no infra)
│   │   ├── plugin.json
│   │   └── skills/              #   one SKILL.md per skill
│   ├── sap-sre-config/          #   Tier 2 — STAF config validator (needs Storage Account)
│   │   ├── plugin.json
│   │   └── skills/
│   └── sap-sre-proxy-ops/       #   Tier 3 — command runner + self-healing (needs SRE Proxy)
│       ├── plugin.json
│       └── skills/
├── knowledge/                   # Knowledge-source guidance (connect via Knowledge base → Add repository)
│   └── README.md
├── config/
│   ├── sap-landscape-inventory.template.json  # Fill in your SAP systems
│   └── sap-landscape-inventory.json           # Example filled inventory
├── onboarding/
│   └── team-onboarding.template.md  # Skill routing + auth context (paste into Team Onboarding)
├── infra/                       # Operator deploy automation (NOT installed by the agent)
│   ├── deploy-sre-infra.ps1     # One-shot infra deploy (VNet, ACR, Storage, UMI, Container App)
│   └── sap-sre-agent-role.json  # Custom RBAC role (read + runCommand only)
├── proxy/                       # FastAPI proxy (Container App)
│   ├── app.py                   # All API routes
│   ├── Dockerfile               # Build context for ACR
│   └── requirements.txt
├── collector/
│   └── collect-sap-configs.sh   # Bash script deployed to SAP VMs
└── docs/                        # Architecture diagrams
```

> **`plugins/` is agent-facing; `infra/`, `proxy/`, `collector/` are operator-facing.** The Plugin
> Marketplace only reads `plugins/` (via the manifest) — it ignores the deployment automation, which
> you run once to stand up the optional Storage Account and proxy.

## Prerequisites

- **SAP workloads on Azure** — VMs running HANA and/or NetWeaver
- **Azure Monitor for SAP Solutions (AMS)** — configured with HANA + OS providers ([setup guide](https://learn.microsoft.com/azure/sap/monitor/quickstart-portal))
- **Azure CLI** — installed and logged in (`az login`)
- **PowerShell 7+** — for deployment scripts
- **Access to [sre.azure.com](https://sre.azure.com)** — agent platform
- **GitHub access** to this repo (fork to your org for Code Access integration)
- **Permissions** — Owner or Contributor + User Access Administrator on the deployment subscription

---

## Setup Guide

The setup has three phases:

| Phase | What | Where | Effort |
|-------|------|-------|--------|
| **1. Platform** | Create agent, enable tools, import skills, add connectors | sre.azure.com portal | ~30 min |
| **2. Infrastructure** | Deploy proxy, storage, identities; install collector on VMs | Azure CLI / PowerShell | ~20 min |
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

**Step 5: Connect this repo** → Point the agent at your fork of `mcaps-microsoft/sap-azure-sre-agent` in up to three places (see [This repo is the single source of truth](#this-repo-is-the-single-source-of-truth)):
- **Builder → Knowledge base → Add repository** — indexes `knowledge/`, `config/`, `docs/`.
- **Builder → Code Access** (optional) — lets RCA cite the proxy/collector/IaC by file and commit.

**Step 6: Incident Platform** → Select Azure Monitor.

**Step 7: Install Skills from the Plugin Marketplace** → Builder → Plugins → **Add marketplace** (enter your fork URL) or **Install from URL**. Install the plugins for your tier — each skill auto-detects available infrastructure at runtime:

| Plugin | Install when… | Skills |
|--------|---------------|--------|
| **`sap-sre-core`** | Always (Tier 1+) | landscape-discovery, operational-health, cost-analysis, trend-analysis, resiliency-assessment, deployment-readiness, incident-analysis, performance-diagnostics, ha-cluster-health, maintenance-handler |
| **`sap-sre-config`** | You deployed a Storage Account (Tier 2+) | config-validator |
| **`sap-sre-proxy-ops`** | You deployed the SRE Proxy (Tier 3) | command-runner, self-healing |

Each install is pinned to the exact commit. To adopt later changes, click **Update** on the plugin. To author or edit skills, see [Updating Skills](#updating-skills).

**Step 8: Managed Resources** → Add: all SAP RGs, AMS RG, `rg-sre-proxy` (created in Phase 2).

**Step 9: Knowledge Sources** → Upload:
- `sap-landscape-inventory.json` — fill in your SAP systems from `config/sap-landscape-inventory.template.json`
- SAP Note 1928533 PDF — VM/OS certification matrix
- HANA Hardware Directory PDF — HANA-certified VMs

---

### Phase 2 — Infrastructure (tier-dependent)

This phase depends on which adoption tier you chose ([Adoption Tiers](#adoption-tiers)):

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

## Operations

### Updating the Proxy (Code Changes)

After editing `proxy/app.py`:

```powershell
# The deploy script's flow — copy collector into build context, build image, restart app
Copy-Item collector\collect-sap-configs.sh proxy\collect-sap-configs.sh -Force
az acr build --registry <acr-name> -t sre-proxy:latest .\proxy
az containerapp update -n sap-sre-proxy -g rg-sre-proxy `
    --image <acr-name>.azurecr.io/sre-proxy:latest
```

> **Note**: `proxy/collect-sap-configs.sh` is gitignored — it's a build artifact copied from `collector/` at build time. Always edit the source in `collector/`.

### Updating Skills

Skills live under `plugins/<plugin>/skills/<name>/SKILL.md`. To change a skill:

1. Edit the `SKILL.md`, open a **pull request**, and merge to your fork's default branch.
2. In sre.azure.com → **Builder → Plugins**, open the installed plugin and click **Update**. The portal diffs the new commit against the installed one (SHA-256) and shows what changed before you apply.

Because installs are **commit-pinned**, merged changes never reach an agent automatically — each agent updates on its own schedule. This is what lets you stage a change on a dev agent before promoting it to production. (Knowledge-base and Code Access sources re-index on their own; only Plugin Marketplace installs are pinned.)

> **Tip — drive it from your IDE/terminal.** With the [SRE Agent MCP server](https://learn.microsoft.com/azure/sre-agent/mcp-server) (shipped in the Azure MCP Server, `sreagent_*` tools), you can list/update skills and configure connectors from VS Code, Copilot CLI, Cursor, or Claude — no portal tab required. Needs `Reader` + `SRE Agent Administrator` on the agent resource.

### Adding a New SAP System

1. Add the system to `config/sap-landscape-inventory.json`; re-upload to Knowledge Sources
2. Append to the landscape table in `onboarding/team-onboarding.template.md`; re-paste to Team Onboarding
3. Grant proxy UMI the custom role on the new SAP RG (Step 11)
4. Assign collector UMI to the new VMs (Step 12)
5. Add the new subnet to storage firewall (Step 13)
6. Deploy collector to each new VM (Step 14)

---

## API Endpoints

The proxy exposes only the endpoints the agent actually needs. STAF validation is **not** a proxy endpoint — the `sap-config-validator` skill fetches STAF YAML from GitHub and reads collected configs from blob entirely in-skill (no proxy round-trip).

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/health` | Health check (no auth) |
| GET | `/api/diag` | MI token + ARM connectivity test |
| GET | `/api/commands` | List allowed VM commands |
| POST | `/api/command` | Execute one of 14 allowed commands |
| POST | `/api/batch` | Execute up to 6 commands in one call |
| GET | `/api/registry` | SAP system inventory (from landscape JSON) |
| GET | `/api/configs/{sid}/{hostname}` | All collected config files for a VM |
| GET | `/api/configs/{sid}` | List hosts under a SID |
| GET | `/api/config/{sid}/{hostname}/{filepath}` | Single config file contents |

### Config Validation Flow (in-skill, no proxy)

The `sap-config-validator` skill performs the entire flow client-side via `ExecutePythonCode` + `RunAzCliReadCommands`:

```
1. Skill calls RunAzCliReadCommands:
     az storage blob download-batch --auth-mode login \
       --source sap-configs --pattern "<SID>/<host>/latest/*"
     (uses the SRE Agent's own MI — requires Storage Blob Data Reader on the storage account)

2. Skill calls ExecutePythonCode:
     requests.get(<github raw>) for the 9 STAF YAML files in
       Azure/sap-automation-qa @ main/src/roles/configuration_checks/tasks/files
     parse → filter applicability → extract actuals from collected files
     → compare (string / range / list) → emit JSON report

3. Agent presents the report to the user verbatim.
```

This keeps the proxy focused on its one job — brokered VM command execution — and lets the Config Validator skill work with just a Storage Account (no proxy needed).

---

## Identities & RBAC

| Identity | Assigned to | Purpose | RBAC |
|----------|------------|---------|------|
| SRE Agent MI | SRE Agent platform | Azure API queries + direct blob reads (when Storage Account deployed) | Reader on SAP RGs, Log Analytics Reader on AMS LAW, **Storage Blob Data Reader on the `sap-configs` storage account** (only when Storage Account is deployed — required by `sap-config-validator` skill) |
| sre-proxy-umi | Container App | VM commands + blob + ARM queries | Custom - SAP SRE Agent Operator on SAP RGs; Storage Blob Data Contributor on storage; AcrPull on ACR |
| sre-collector-umi | SAP VMs | Config upload to blob | Storage Blob Data Contributor on storage |

The custom role grants:
- `Microsoft.Compute/virtualMachines/read`, `/runCommand/action`
- `Microsoft.Compute/disks/read`
- `Microsoft.Network/networkInterfaces/read`, `/loadBalancers/read`, `/proximityPlacementGroups/read`
- `Microsoft.Resources/subscriptions/resourceGroups/read`
- **No** write, delete, restart, deallocate, or power-state actions.

---

## Security

- **Command allowlist** — 14 read-only commands hardcoded in `proxy/app.py` (`ALLOWED_COMMANDS`) — no arbitrary shell execution
- **API key auth** — required for all proxy calls except `/api/health`
- **Custom RBAC** — read + runCommand only — no VM delete/restart/write
- **No shared storage keys** — Entra ID auth only (MCAP-compliant)
- **VNet-integrated proxy** — Container App on a delegated subnet; storage firewall restricts to proxy + SAP subnets
- **Audit logging** — every command logged with caller, VM, RG, timestamp (visible in Container App logs)
- **Onboarding rule** — agent is instructed to NEVER use `az vm run-command` directly; all VM execution goes through the proxy

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `/api/diag` returns 500 on ARM call | Proxy UMI missing RBAC on subscription/SAP RG | Re-run Step 11 |
| `/api/command` returns "AuthorizationFailed" | Custom role not assigned on the target RG | Step 11 — confirm role assignment on the **VM's** RG |
| `/api/configs/...` returns empty list | Collector hasn't run yet, or blob firewall blocks SAP subnet | Step 13 + Step 17 (run collector once manually) |
| `deploy_collector` returns "AADSTS" error | VM missing collector UMI assignment | Step 12 |
| Collector log shows "AuthorizationPermissionMismatch" | Collector UMI missing Storage Blob Data Contributor | Re-run deploy script (Step 10 grants this automatically) |
| Skills can't find systems | `sap-landscape-inventory.json` not uploaded to Knowledge Sources | Step 9 |
| Agent asks for proxy URL repeatedly | Team Onboarding not pasted | Step 15 |
| AMS queries return no rows | Wrong column names (`sapsid_s` vs `SID_s`) | See onboarding template "Data Sources" section |

Container App logs:
```powershell
az containerapp logs show -n sap-sre-proxy -g rg-sre-proxy --tail 100 --follow
```

---

## References

- [SAP Testing Automation Framework (STAF)](https://github.com/Azure/sap-automation-qa)
- [Azure Monitor for SAP Solutions](https://learn.microsoft.com/azure/sap/monitor/)
- [SAP on Azure Best Practices](https://learn.microsoft.com/azure/sap/workloads/)
- [Azure SRE Agent](https://sre.azure.com)
- [SAP Note 1928533](https://launchpad.support.sap.com/#/notes/1928533) — SAP applications on Azure: Supported products
- [HANA Hardware Directory](https://www.sap.com/dmc/exp/2014-09-02-hana-hardware/enEN/iaas.html) — Certified IaaS platforms
