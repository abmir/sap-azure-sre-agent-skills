# SAP Azure SRE Agent

AI-powered SRE agent for SAP HANA and NetWeaver on Azure. Automates health monitoring, STAF configuration validation, incident analysis, and cost optimization — all through natural language at [sre.azure.com](https://sre.azure.com).

**Three adoption tiers** — pick what fits your security posture. Start with zero-infrastructure Azure-native telemetry, add a customer-controlled config store when you want STAF compliance, add a brokered command proxy when you’re ready for live remediation. See [Adoption Tiers](#adoption-tiers) below.

## This repo is the single source of truth

This repository **is** the agent's skill pack. You don't upload skills by hand — you **fork this repo and point your SRE Agent at it**, then install the skills you need. Updates ship as pull requests; each agent picks them up on its own schedule.

The agent consumes this repo through two GitHub connections (plus one manual paste):

| Path | Portal location | What it pulls from this repo |
|------|-----------------|------------------------------|
| **Plugin Marketplace** | Builder → Plugins | **Skills** (and any MCP server configs) — the tiered plugins in [`plugins/`](plugins/) via [`.github/plugin/marketplace.json`](.github/plugin/marketplace.json). Installed as a version-pinned copy. |
| **Code Access** | Builder → Code Access | **Everything else in the repo** — `knowledge/` (incl. the SAP/HANA cert reference), `config/`, `docs/`, and the proxy/IaC code. One connection covers it all. |
| **Team Onboarding** (manual) | Settings → Team Onboarding | The filled onboarding text. **Pasted by hand** — it carries secrets (proxy URL, API key) so it is *not* read from the repo. |

> **Repository connections are under Code Access, not Knowledge base.** The portal moved them —
> *"Repository connections have moved to Code Access."* **Knowledge base** is now only for uploaded
> files (PDFs) and web pages; you don't need it for anything in this repo.

> **Version pinning (important):** Plugin installs are pinned to the exact git commit at install time. Changes you merge here do **not** reach an agent until someone clicks **Update** on that plugin (the portal diffs by SHA-256 hash). This is by design — it gives you production stability, staged rollouts (update dev before prod), and version diversity across agents. It is *not* a live, auto-propagating feed. *(Exception: data files a skill fetches live at runtime — like [`knowledge/sap-certified-vms.json`](knowledge/sap-certified-vms.json) and the STAF YAML — update immediately, because the skill pulls the current file each run.)*

### Tiered plugins

| Plugin | Tier | Skills | Requires |
|--------|------|:------:|----------|
| [`sap-sre-core`](plugins/sap-sre-core) | Azure-Native | 10 | Nothing (Azure APIs + AMS) |
| [`sap-sre-config`](plugins/sap-sre-config) | + Config Store | 1 | Storage Account |
| [`sap-sre-proxy-ops`](plugins/sap-sre-proxy-ops) | + Live Proxy | 2 | SRE Proxy + custom RBAC |

Install only the plugins your tier supports. A security-strict customer installs just `sap-sre-core` and never sees the proxy skills.

## Quick start — implement in 3 phases

Pick your components with the [Adoption planner](#adoption-planner--mix--match-by-phase); this is the simplest happy path. Each phase is independent — stop after any phase.

**Phase 0 — Azure-native (≈30 min, no infrastructure)**
1. Create an agent at [sre.azure.com](https://sre.azure.com); note its Managed Identity.
2. Grant that identity **Reader** + **Monitoring Reader** on your SAP resource groups (and **Cost Management Reader** at subscription scope).
3. **Builder → Code Access** → connect your fork of this repo (skills' source + knowledge).
4. **Builder → Plugins** → install **`sap-sre-core`** (10 skills).
5. Ask: *"What SAP systems do I have?"*, *"Is AB1 healthy?"*, *"How much does AB1 cost?"*

**Phase 1 — Config store (≈1 hr) — adds STAF validation + config-enriched RCA/perf/HA**
6. Deploy storage + collector: `infra/deploy-sre-infra.ps1 -Mode ConfigStore -SubscriptionId <sub> -StorageAccountName <name> -SreAgentUmiPrincipalId <agent-mi-object-id>`. This creates the `sap-configs` storage and grants the **agent MI Storage Blob Data Reader** (the agent reads configs **directly** — no proxy).
7. VNet-integrate the agent and allow its subnet on the storage firewall (so the private storage is reachable).
8. Assign the **collector UMI** to your SAP VMs and run the collector (via `az vm run-command`).
9. **Builder → Plugins** → install **`sap-sre-config`**. Ask: *"Run config checks for AB1."*

**Phase 2 — Live commands (optional, ≈1 hr) — adds command-runner + self-healing**
10. Deploy the optional proxy: `infra/deploy-sre-infra.ps1 -Mode Full ...` (its own resource group; commands only, no storage role).
11. Add the proxy URL + key to **Team Onboarding**, then **Builder → Plugins** → install **`sap-sre-proxy-ops`**. Ask: *"Run uptime on AB1vm."*

> Full detail for each step is in the [Setup Guide](#setup-guide) below. Deferring a piece (e.g. ServiceNow, SAP Cloud ALM / Focus Run telemetry) is expected — see the [Adoption planner](#adoption-planner--mix--match-by-phase).

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
con
> **These three tiers are *presets*, not a rigid ladder.** Every box in the [architecture](#architecture) is **independently optional** and can be adopted in any **phase**. If you want to mix and match — e.g. collect configs to storage but *not* deploy the proxy, keep SAP telemetry in SAP Cloud ALM / Focus Run for now, or defer ServiceNow — see the [Adoption planner](#adoption-planner--mix--match-by-phase) below.

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

## Adoption planner — mix & match by phase

The three tiers are **presets**. In practice each customer enables a **different subset** of the architecture, often across **phases**. This works because every skill **auto-detects** what's present (from the `## Deployed Infrastructure` section of Team Onboarding) and **degrades gracefully** when a component is absent. Turn on only what you need now; add the rest later by editing onboarding and (if it adds a skill) installing the matching plugin.

### Component menu — everything is independently optional

| Component (from the architecture) | What it unlocks | Requires | If absent, skills… | Typical phase |
|-----------------------------------|-----------------|----------|--------------------|:-------------:|
| **A. SRE Agent + MI + RBAC** (RG_SRE_Agent) | The agent itself — mandatory | Reader + Monitoring Reader on SAP RGs | (nothing runs) | 1 |
| **B. Skills via Plugin Marketplace** | The custom SAP skills (install per tier) | A + your repo fork | fewer skills available | 1 |
| **C. Repo via Code Access** | Knowledge: inventory, cert data, docs | A + fork | lose repo-sourced knowledge | 1 |
| **D. Azure platform data sources** (Monitor, Resource Graph, Cost, Advisor, Health, Activity Log) | Infra-level health, cost, resiliency, RCA | just RBAC (always on) | — | 1 |
| **E. SAP telemetry — AMS** (Azure Monitor for SAP) | HANA/OS metric depth for health/trend/perf | AMS deployed | fall back to VM-level metrics only | 1 or 2 |
| **E′. SAP telemetry — SAP Cloud ALM / Focus Run** | SAP-app-level signals from a non-Azure source | Connector (MCP/HTTP) | skills use whatever telemetry *is* present | usually 2 |
| **F. Config Store** — Storage Account (`sap-configs`) + **collector UMI + cron** | STAF config validation + config-enriched RCA/perf/HA | Storage + collector on VMs (**no proxy needed**) | config-validator blocked; RCA/perf/HA run telemetry-only | 1 or 2 |
| **G. SRE Proxy** — Container App + proxy UMI + custom RBAC | Live read-only VM commands + self-healing | Proxy deployed | command-runner & self-healing blocked | 2+ |
| **H. Incident platform — Azure Monitor** | Alert-driven investigations | Azure Monitor alerts | manual / chat-driven only | 1 or 2 |
| **H′. Incident platform — ServiceNow / PagerDuty** | Ticketing integration | Connector | use Azure Monitor or none | usually 2 |
| **I. Notification connectors** — Teams / Outlook | Push alerts & summaries to people | Connector | no push notifications | any |
| **J. Third-party connectors** — Dynatrace / Sentinel / Focus Run | Extra observability / SIEM signals | Connector | Azure-native signals only | 2+ |

> **Key separation (the common Phase-1 shape):** the **Config Store (F)** — collector UMI + cron uploading configs to a Storage Account — is **independent of the SRE Proxy (G)**. You can enable config collection and STAF validation **without** the Container App proxy: deploy the collector with `az vm run-command` instead of the proxy (see [Adding infrastructure later](#adding-infrastructure-later)). The proxy is only needed for *live* VM commands and self-healing (Phase 2+).

> **Telemetry is pluggable.** Skills don't hard-require AMS. If your SAP-app telemetry lives in **SAP Cloud ALM / Focus Run** (or Dynatrace, Sentinel) rather than Azure Monitor for SAP, leave AMS out and bring that source in later as a **connector (E′/J)**. Until then, telemetry-dependent skills (health, trend, performance) run on **Azure platform metrics** (VM Insights, Resource Health) and note that deeper SAP signals arrive in a later phase.

### Example — Customer-1, phased rollout

A customer pointing the agent at existing SAP workloads and adopting incrementally:

| Component | Phase 1 (now) | Phase 2 (later) | Tabled |
|-----------|:-------------:|:---------------:|:------:|
| SRE Agent + MI + RBAC (A) | ✅ | | |
| Core skills `sap-sre-core` (B) | ✅ | | |
| Repo via Code Access (C) | ✅ | | |
| Azure platform data sources (D) | ✅ | | |
| Config Store + collector cron (F) | ✅ collector → storage, **no proxy** | | |
| Config Validator `sap-sre-config` (B) | ✅ | | |
| SAP telemetry — AMS (E) | ⚠️ only if already in AMS | | |
| SAP telemetry — Focus Run / Cloud ALM (E′) | | ✅ connector | |
| SRE Proxy + `sap-sre-proxy-ops` (G, B) | | ✅ | |
| Incident platform — Azure Monitor (H) | ✅ | | |
| Incident platform — ServiceNow (H′) | | | ⏸️ deferred |
| Teams notifications (I) | optional | | |
| Dynatrace / Sentinel (J) | | ✅ if used | |

**Result:** Customer-1 gets **11 working skills in Phase 1** (10 Azure-native + config-validator) with **no proxy** and **no ServiceNow**. Its SAP-app telemetry stays in SAP Cloud ALM / Focus Run until a Phase-2 connector brings it in; meanwhile telemetry-dependent skills run on Azure platform metrics and say so. Phase 2 adds the proxy (unlocking command-runner + self-healing), the Focus Run connector, and any SIEM integration — by editing onboarding and installing `sap-sre-proxy-ops`. No rework of Phase 1.

### Enabling a component later (any phase transition)

1. **Deploy / enable** the component (storage, proxy, connector, AMS, etc.).
2. **Edit Team Onboarding** → add the component's line to `## Deployed Infrastructure` (each line is independently add/removable).
3. **If it adds skills**, install the matching plugin from the marketplace (`sap-sre-config` for the config store, `sap-sre-proxy-ops` for the proxy).
4. The agent picks it up on its next run — nothing else changes, and earlier phases are untouched.

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
├── proxy/                       # OPTIONAL SRE Proxy (Container App) — live commands only; → future MCP server
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
- **SAP telemetry (recommended, not required)** — [Azure Monitor for SAP Solutions (AMS)](https://learn.microsoft.com/azure/sap/monitor/quickstart-portal) with HANA + OS providers gives the deepest SAP signals. If your SAP-app telemetry lives elsewhere (SAP Cloud ALM / Focus Run, Dynatrace, Sentinel), you can start without AMS and bring that source in later as a connector — telemetry-dependent skills fall back to Azure platform metrics meanwhile. See the [Adoption planner](#adoption-planner--mix--match-by-phase).
- **Azure CLI** — installed and logged in (`az login`)
- **PowerShell 7+** — for deployment scripts
- **Access to [sre.azure.com](https://sre.azure.com)** — agent platform
- **GitHub access** to this repo (fork to your org for Code Access integration)
- **Permissions** — Owner or Contributor + User Access Administrator on the deployment subscription

---

## Setup Guide

**Most people only need the [Quick start — 3 phases](#quick-start--implement-in-3-phases) above.** The section below is the full, click-by-click reference — expand it only if you want the exhaustive detail.

<details>
<summary><b>Full setup detail (advanced) — Steps 1–17</b></summary>

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

</details>

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

Config reads are **decoupled from the proxy**: the SRE Agent reads configs from storage with its **own** identity; the proxy (optional) only runs live VM commands and has **no storage role**.

| Identity | Assigned to | Purpose | RBAC |
|----------|------------|---------|------|
| **SRE Agent MI** | SRE Agent platform | Azure API queries + **direct** config blob reads | Reader on SAP RGs, Log Analytics Reader on AMS LAW, **Storage Blob Data Reader on the `sap-configs` storage account** (whenever a config store is deployed — used by *all* config-consuming skills, not just config-validator) |
| **sre-collector-umi** | SAP VMs | Config upload to blob (write) | Storage Blob Data Contributor on the `sap-configs` storage account |
| **sre-proxy-umi** *(optional)* | SRE Proxy Container App | **Live VM commands only** | Custom - SAP SRE Agent Operator on SAP RGs; AcrPull on ACR. **No storage role** — the proxy is never in the config path. |

> **Network path for direct reads:** because the storage account is private (no shared keys), the SRE Agent must be able to reach it. VNet-integrate the agent (delegated subnet) and allow that subnet on the storage firewall, so the agent MI reads configs privately and Entra-only. The proxy is no longer needed to bridge the network.

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
