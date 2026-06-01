# SAP Azure SRE Agent

AI-powered SRE agent for SAP HANA and NetWeaver on Azure. Automates health monitoring, STAF configuration validation, incident analysis, and cost optimization — all through natural language at [sre.azure.com](https://sre.azure.com).

**Three adoption modes** — pick what fits your security posture. Start with zero-infrastructure Azure-native telemetry, add a customer-controlled config store when you want STAF compliance, add a brokered command proxy when you're ready for live remediation. See [Adoption Modes](#adoption-modes) below.

## What It Does

| Capability | Example Prompt | Min. Mode | How It Works |
|-----------|----------------|:---------:|-------------|
| Landscape discovery | "What SAP systems do I have?" | 1 | Azure Resource Graph |
| Health monitoring | "Is AB1 healthy?" | 1 | AMS telemetry + Azure Monitor + ARM API |
| Cost analysis | "How much does AB1 cost?" | 1 | Azure Cost Management API |
| Trend analysis | "Show memory trends for AB1" | 1 | AMS time-series with regression |
| Resiliency assessment | "Assess AB1 resiliency" | 1 | Azure Advisor + ACSS checks |
| Deployment readiness | "Can I deploy Standard_M32 in eastus?" | 1 | SKU / quota / HANA certification |
| Incident analysis (basic) | "Why did HANA restart?" | 1 | AMS + Activity Log correlation |
| Performance diagnostics (basic) | "Why is AB1 slow?" | 1 | AMS + Azure Monitor metrics |
| HA cluster health (basic) | "Cluster status for AB1" | 1 | AMS + Resource Graph |
| Maintenance handler (basic) | "Any scheduled maintenance?" | 1 | Scheduled Events API + Service Health |
| **Config validation (STAF)** | "Run config checks for AB1" | **2** | STAF YAML fetched live from `Azure/sap-automation-qa` on GitHub, compared against blob-stored VM configs — entirely in-skill, no proxy |
| **Config-enriched RCA / perf / cluster** | "Cross-layer RCA for AB1" | 2 | Adds stored config files (sysctl, global.ini, corosync) to incident / performance / HA skills |
| **Live VM commands** | "Run uptime on AB1vm" | **3** | Proxy → ARM run-command API (14-command allowlist, read-only) |
| **Self-healing remediation** | Auto: `/hana/log` full → log backup | 3 | Proxy executes restricted write commands within strict guardrails |

## Adoption Modes

The agent supports three deployment modes. Each mode is a strict superset of the previous one — start with Mode 1, add Mode 2 when you need config-level visibility, add Mode 3 when you're ready to execute live commands on SAP VMs.

| Mode | What's Deployed | Capabilities | Customer Profile | Effort |
|:----:|----------------|--------------|------------------|:------:|
| **1 — Azure-Native** | Nothing (just `sre.azure.com` + your existing AMS) | 10 skills using only Azure APIs and AMS telemetry. No config validation, no live commands. | Security-strict customers. AMS already deployed. Wants insight without standing up new infra. | ~30 min |
| **2 — + Config Store** | Mode 1 + **Storage Account** (`sap-configs` blob container) + **collector UMI** (assigned to SAP VMs) | All Mode 1 skills + **STAF config validation** + config-enriched RCA / performance / HA skills. 11 skills total. | Wants STAF compliance reporting and richer RCA, but does not want a proxy executing live commands. | ~1 hr |
| **3 — + Live Proxy** | Mode 2 + **Container App proxy** (FastAPI, VNet-integrated) + **proxy UMI** + Entra ID Easy Auth | All Mode 2 skills + **live read-only VM commands** + **self-healing remediation**. All 13 skills. | Wants full SRE automation including remediation. Accepts a brokered command path with custom RBAC. | ~2 hr |

### How each mode handles config validation

- **Mode 1** — Config Validator is disabled. The agent reports that it requires Mode 2 or higher.
- **Mode 2** — The Config Validator skill **fetches STAF check definitions directly from `Azure/sap-automation-qa` on GitHub at runtime**, reads collected configs from your storage account, and runs the comparison **in-skill** (Python via `ExecutePythonCode`). No proxy involved.
- **Mode 3** — Same as Mode 2, with the added option to trigger a fresh on-demand collection through the proxy before validating.

### Switching modes later

- **Mode 1 → Mode 2** — Run the deploy script with `-Mode ConfigStore -SreAgentUmiPrincipalId <agent-mi-object-id>`; assign the collector UMI to SAP VMs; deploy the collector. Import the Config Validator skill on the portal.
- **Mode 2 → Mode 3** — Run the deploy script with `-Mode Full`; re-paste the team onboarding with the proxy URL + API key + Mode 3 declaration. Import the two remaining skills (`sap-command-runner`, `sap-self-healing`).

## Architecture

![Azure SRE Agent for SAP Workloads — architecture](docs/sap-on-azure-sre-agent.png)

```
┌──────────────────────────────────────────────────────────┐
│ Azure SRE Agent (sre.azure.com)                          │
│  13 Custom Skills + 39 Built-in Skills                   │
│  Tools: ARM API, AMS KQL, Azure Monitor, CLI, Python     │
│  Knowledge: SAP landscape, SAP Notes, STAF references    │
└──────────┬────────────────┬────────────────┬─────────────┘
           │ Mode 1         │ Mode 2         │ Mode 3
           │ (always on)    │ (config-aware) │ (live ops)
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

STAF check definitions live in the public [`Azure/sap-automation-qa`](https://github.com/Azure/sap-automation-qa) repo and are pulled live by the Config Validator skill (Mode 2+) — they are not hosted by Microsoft inside this stack.

## Repository Layout

```
sap-azure-sre-agent/
├── infra/                       # Deployment automation
│   ├── deploy-sre-infra.ps1     # One-shot infra deploy (VNet, ACR, Storage, UMI, Container App)
│   └── sap-sre-agent-role.json  # Custom RBAC role (read + runCommand only)
├── proxy/                       # FastAPI proxy (Container App)
│   ├── app.py                   # All API routes
│   ├── Dockerfile               # Build context for ACR
│   └── requirements.txt
├── collector/
│   └── collect-sap-configs.sh   # Bash script deployed to SAP VMs
├── skills/                      # 13 SRE Agent custom skills (SKILL.md per skill)
├── config/
│   └── sap-landscape-inventory.template.json  # Fill in your SAP systems
├── onboarding/
│   └── team-onboarding.template.md  # Skill routing + auth context
└── docs/                        # Architecture diagrams
```

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
| sap-azure-sre-agent | GitHub repo | Your fork of `mcaps-microsoft/sap-azure-sre-agent` |
| Microsoft Teams (optional) | Notification | For alert delivery |

**Step 5: Code Access** → Add GitHub repo (your fork). Required so the agent can read skill source code.

**Step 6: Incident Platform** → Select Azure Monitor.

**Step 7: Import Skills** → Skill Builder → New → paste each `skills/<name>/SKILL.md` (import only the skills your chosen mode supports):

| # | Skill | Min. Mode | Purpose |
|---|-------|:---------:|---------|
| 1 | sap-landscape-discovery | 1 | System inventory from ARM |
| 2 | sap-operational-health | 1 | 5-layer health dashboard |
| 3 | sap-cost-analysis | 1 | Cost breakdown + savings |
| 4 | sap-trend-analysis | 1 | AMS trend projection |
| 5 | sap-resiliency-assessment | 1 | Advisor + ACSS checks |
| 6 | sap-deployment-readiness | 1 | SKU / quota / certification |
| 7 | sap-incident-analysis | 1 | Cross-layer RCA (enriched in Mode 2+) |
| 8 | sap-performance-diagnostics | 1 | HANA memory / disk / savepoint (enriched in Mode 2+) |
| 9 | sap-ha-cluster-health | 1 | Pacemaker / HSR status (enriched in Mode 2+) |
| 10 | sap-maintenance-handler | 1 | Graceful maintenance handling (enriched in Mode 2+) |
| 11 | sap-config-validator | **2** | STAF compliance — fetches STAF YAML live from GitHub, compares against blob configs in-skill |
| 12 | sap-command-runner | **3** | 14 read-only VM commands via proxy |
| 13 | sap-self-healing | **3** | Log volume + backup + drift remediation |

**Step 8: Managed Resources** → Add: all SAP RGs, AMS RG, `rg-sre-proxy` (created in Phase 2).

**Step 9: Knowledge Sources** → Upload:
- `sap-landscape-inventory.json` — fill in your SAP systems from `config/sap-landscape-inventory.template.json`
- SAP Note 1928533 PDF — VM/OS certification matrix
- HANA Hardware Directory PDF — HANA-certified VMs

---

### Phase 2 — Infrastructure (mode-dependent)

This phase depends on which adoption mode you chose ([Adoption Modes](#adoption-modes)):

- **Mode 1 (Azure-Native)** — Skip this entire phase. There is nothing to deploy. Grant the SRE Agent Managed Identity `Reader` + `Monitoring Reader` on each SAP RG (and `Cost Management Reader` at subscription scope), then proceed to Phase 3.
- **Mode 2 (ConfigStore)** — Run the deploy script with `-Mode ConfigStore -SreAgentUmiPrincipalId <agent-mi-object-id>`. This creates only the resource group, collector UMI, storage account with `sap-configs` container, custom RBAC role, and grants the SRE Agent UMI `Storage Blob Data Reader` on the container. Then assign the collector UMI to SAP VMs (Step 12) and install the collector (Step 14 alt: use `az vm run-command` instead of the proxy). Skip Steps 11 and 13 — no proxy means no proxy UMI to grant on SAP RGs, and no storage firewall rules are needed for the proxy subnet.
- **Mode 3 (Full)** — Default. Run the deploy script with no `-Mode` flag (or `-Mode Full`). Follow all of Steps 10–14 below.

> **All Phase 2 resources go in the same subscription as your SAP VMs.** Cross-subscription is supported but adds RBAC complexity.

**Step 10: Deploy Proxy + Storage + Identities**

```powershell
git clone https://github.com/<your-org>/sap-azure-sre-agent.git
cd sap-azure-sre-agent
az login
az account set --subscription "<sap-subscription-id>"

# Mode 3 — Full (default):
.\infra\deploy-sre-infra.ps1 `
    -SubscriptionId "<sap-subscription-id>" `
    -StorageAccountName "<globally-unique-name>"   # 3-24 chars, lowercase + numbers

# Mode 2 — Config Store only (no proxy):
.\infra\deploy-sre-infra.ps1 `
    -Mode ConfigStore `
    -SubscriptionId "<sap-subscription-id>" `
    -StorageAccountName "<globally-unique-name>" `
    -SreAgentUmiPrincipalId "<agent-mi-object-id-from-sre.azure.com>"

# Mode 1 — Azure-Native (prints manual steps and exits, no infra deployed):
.\infra\deploy-sre-infra.ps1 -Mode AzureNative -SubscriptionId "<sap-subscription-id>"
```

What it creates in `rg-sre-proxy` (Mode 3 — Full):
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
- Your **deployment mode** (1, 2, or 3) — set the `## Deployment Mode` block at the top
- Mode 2/3: Storage account name + `sap-configs` container
- Mode 3 only: Proxy URL + API key (from Step 10 output) + Proxy UMI client/principal IDs
- AMS workspace ID + provider instance names
- SAP landscape table (SID, RG, VMs, roles, IPs)
- Subscription ID

Paste the filled template into sre.azure.com → Settings → Team Onboarding.

**Step 16: Quick Verification (3 minutes)**

Mode 1 — Azure-Native:

| # | Test | Expected |
|---|------|----------|
| 1 | At sre.azure.com: "Is AB1 healthy?" | 5-layer health dashboard |
| 2 | At sre.azure.com: "How much does AB1 cost?" | Cost breakdown |
| 3 | At sre.azure.com: "Run config checks for AB1" | Skill reports `Config Validator requires Mode 2+` |

Mode 2 — + Config Store:

| # | Test | Expected |
|---|------|----------|
| 1 | `az storage blob list --account-name <storage> --container-name sap-configs --prefix "<SID>/<host>/latest/" --auth-mode login` | Lists collected config files |
| 2 | At sre.azure.com: "Is AB1 healthy?" | 5-layer health dashboard |
| 3 | At sre.azure.com: "Run config checks for AB1" | STAF compliance report (skill pulled STAF YAML live from GitHub, compared against blob configs) |
| 4 | At sre.azure.com: "How much does AB1 cost?" | Cost breakdown |

Mode 3 — + Live Proxy (all Mode 2 tests above PLUS):

| # | Test | Expected |
|---|------|----------|
| 5 | `curl https://<proxy>/api/health` | `{"status":"healthy"}` |
| 6 | `curl -H "X-API-Key: <key>" https://<proxy>/api/diag` | MI token + ARM call both OK |
| 7 | `curl -H "X-API-Key: <key>" https://<proxy>/api/configs/<sid>/<host>` | List of config files in blob (fallback path; Mode 2 skills read blob directly) |
| 8 | At sre.azure.com: "Run uptime on AB1vm" | VM uptime via proxy |

**Step 17: Verify Collector is Running (Mode 2 / Mode 3)**

```powershell
# Trigger fresh collection on a VM (Mode 2 — use az vm run-command; Mode 3 — POST /api/command with run_collector)
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

Edit `skills/<name>/SKILL.md`, commit + push to your fork, then in sre.azure.com → Skill Builder → re-import.

### Adding a New SAP System

1. Add the system to `config/sap-landscape-inventory.json`; re-upload to Knowledge Sources
2. Append to the landscape table in `onboarding/team-onboarding.template.md`; re-paste to Team Onboarding
3. Grant proxy UMI the custom role on the new SAP RG (Step 11)
4. Assign collector UMI to the new VMs (Step 12)
5. Add the new subnet to storage firewall (Step 13)
6. Deploy collector to each new VM (Step 14)

---

## API Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/health` | Health check (no auth) |
| GET | `/api/diag` | MI token + ARM connectivity test |
| GET | `/api/commands` | List allowed VM commands |
| POST | `/api/command` | Execute one of 14 allowed commands |
| POST | `/api/batch` | Execute up to 6 commands in one call |
| GET | `/api/registry` | SAP system inventory (from landscape JSON) |
| GET | `/api/validate/{sid}/{hostname}` | **Full STAF config validation** (fresh collection + comparison) |
| GET | `/api/staf-checks` | STAF check definitions from GitHub (filtered by applicability) |
| GET | `/api/configs/{sid}/{hostname}` | All collected config files for a VM |
| GET | `/api/configs/{sid}` | List hosts under a SID |
| GET | `/api/config/{sid}/{hostname}/{filepath}` | Single config file contents |

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

---

## Identities & RBAC

| Identity | Assigned to | Purpose | RBAC |
|----------|------------|---------|------|
| SRE Agent MI | SRE Agent platform | Azure API queries | Reader on SAP RGs, Log Analytics Reader on AMS LAW |
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
