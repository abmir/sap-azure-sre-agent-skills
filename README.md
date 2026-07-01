# SAP Azure SRE Agent

AI-powered SRE agent for SAP HANA and NetWeaver on Azure. Automates health monitoring, STAF configuration validation, incident analysis, and cost optimization — all through natural language at [sre.azure.com](https://sre.azure.com).

**Three adoption tiers** — pick what fits your security posture. Start with Azure-native telemetry using your existing Azure signals, add a customer-controlled config store for STAF compliance that unlocks the config validator and enriches 4 more skills (incident RCA, performance diagnostics, HA cluster health, maintenance) — 5 skills in all, then add an MCP command proxy (registered as a connector) to run an approved set of read-only commands on your SAP VMs. See [Tiered plugins](#tiered-plugins) below, or the full [Adoption planner](docs/adoption-planner.md).

## This repo is the single source of truth

This repository **is** the agent's skill pack. You don't upload skills by hand — you **fork this repo and point your SRE Agent at it**, then install the skills you need. Updates ship as pull requests; each agent picks them up on its own schedule.

The agent consumes this repo through two GitHub connections (plus one manual paste):

| Path | Portal location | What it pulls from this repo |
|------|-----------------|------------------------------|
| **Plugin Marketplace** | Builder → Plugins | **Skills** (and any MCP server configs) — the tiered plugins in [`plugins/`](plugins/) via [`.github/plugin/marketplace.json`](.github/plugin/marketplace.json). Installed as a version-pinned copy. |
| **Code Access** | Builder → Code Access | **Everything else in the repo** — `knowledge/` (incl. the SAP/HANA cert reference), `config/`, `docs/`, and the IaC + MCP proxy code. One connection covers it all. |
| **Team Onboarding** (manual) | Settings → Team Onboarding | The filled onboarding text. **Pasted by hand** — it carries secrets (MCP endpoint URL, API key) so it is *not* read from the repo. |

> **Note:** plugin installs are **commit-pinned** — merges reach an agent only when someone clicks **Update** on the plugin (it's not a live feed). See [Updates & version pinning](docs/reference.md#updates--version-pinning).

### Tiered plugins

| Plugin | Tier | Skills | Requires |
|--------|------|:------:|----------|
| [`sap-sre-core`](plugins/sap-sre-core) | Azure-Native | 10 | Nothing (Azure APIs + AMS) |
| [`sap-sre-config`](plugins/sap-sre-config) | + Config Store | 1 | Storage Account |
| [`sap-sre-proxy-ops`](plugins/sap-sre-proxy-ops) | + Live Commands | 2 | MCP command proxy + custom RBAC |

Install only the plugins your tier supports. A security-strict customer installs just `sap-sre-core` and never sees the proxy skills.

## Before you start

Have these ready before Phase 0 (the setup below assumes them):

- **Fork this repo** to your org — both **Code Access** and **Plugin Marketplace** point at *your* fork, not this one.
- **Tooling:** Azure CLI (run `az login`) and **PowerShell 7+** on your machine (needed for the Phase 1/2 deploy scripts).
- **Permissions:** **Owner or Contributor _plus_ User Access Administrator** on the subscription — you create role assignments in every phase.
- **Details to collect:** SAP **subscription ID**, the SAP **resource group(s)** and VM names, and — if you use **Azure Monitor for SAP (AMS)** — the AMS Log Analytics **workspace ID** and its resource group (usually `mrg-sapmon-…`).

## Setup — 3 phases

Each phase is independent — stop after any phase. Deeper detail lives in [docs/setup-detailed.md](docs/setup-detailed.md); pick components with the [Adoption planner](docs/adoption-planner.md).

### Phase 0 — Azure-native · ~30 min · no infrastructure · 10 skills

1. **Create the agent & enable capabilities.** At [sre.azure.com](https://sre.azure.com) create an SRE Agent and note its **Managed Identity** object ID (Identity blade).
   - **Built-in Tools** (Capabilities → Tools): leave at the **defaults**. The SAP skills rely on these tools, and access is enforced by the agent's **Managed Identity + read-only RBAC** (step 2), not the toggles.
   - **Built-in Skills** (Capabilities → Skills): keep the defaults, and enable these four — **`log_analytics_query`**, **`app_insights_query`**, **`azure_alerting_incident_handler`**, **`azure_alerting_scheduled_task`**.
   - Further reading: [Manage global tools](https://learn.microsoft.com/azure/sre-agent/manage-global-tools) · [Tools & skills page](https://learn.microsoft.com/azure/sre-agent/global-tools-page) · [Tools overview](https://learn.microsoft.com/azure/sre-agent/tools).
2. **Grant read access (RBAC).** Settings → **Managed Resources** → **Add**, permission level **Reader** (read-only). The portal creates the role assignments for you:

   | What you add | Roles the agent receives | Unlocks |
   |---|---|---|
   | **Subscription** | **Reader** (+ Monitoring Reader) | Read across the whole subscription — resource state, metrics, health, Activity Log, Advisor, Resource Graph |
   | **Each resource group** — SAP system RGs · AMS RGs (your `RG_AMS` + managed `mrg-sapmon-*`) · ACSS RGs (your `RG_SAP_*` + managed `mrg-*`) · shared-services RG | **Monitoring Reader · Log Analytics Reader · Storage Blob Data Reader · Azure Center for SAP solutions** | AMS KQL (HANA/OS/cluster), SAP VIS (ACSS), and blob read (a head start on the Phase 1 config store) |

   Keep **Reader**, *not* Privileged. **Cost Management Reader** isn't in the bundle — add it at subscription scope if you want the cost skill. Adding at **subscription** scope inherits to every RG; the RG-only path works too but loses subscription-only sources (Service Health, sub-wide cost/RI, Defender). Details: [RBAC scoping](docs/reference.md#rbac-scoping--rg-only-vs-subscription) · [SRE Agent permissions](https://learn.microsoft.com/azure/sre-agent/permissions).

   <details>
   <summary>Prefer scripting? Show the <code>az role assignment</code> commands</summary>

   ```bash
   AGENT=<agent-mi-object-id>; SUB=<sub-id>

   # Model A — subscription scope (simplest): resource state + metrics + cost across everything
   for R in "Reader" "Monitoring Reader" "Cost Management Reader"; do
     az role assignment create --assignee-object-id $AGENT --assignee-principal-type ServicePrincipal --role "$R" --scope /subscriptions/$SUB
   done

   # Model B — per RG (instead of Model A): repeat for EACH SAP RG + each mrg-* (ACSS) + the mrg-sapmon-* (AMS) RG
   az role assignment create --assignee-object-id $AGENT --assignee-principal-type ServicePrincipal --role "Reader"           --scope /subscriptions/$SUB/resourceGroups/<RG>
   az role assignment create --assignee-object-id $AGENT --assignee-principal-type ServicePrincipal --role "Monitoring Reader" --scope /subscriptions/$SUB/resourceGroups/<RG>

   # (both models) Log Analytics query on the AMS workspace — scope to the AMS RG:
   az role assignment create --assignee-object-id $AGENT --assignee-principal-type ServicePrincipal --role "Log Analytics Reader" --scope /subscriptions/$SUB/resourceGroups/<mrg-sapmon-RG>
   ```

   </details>

3. **Add connectors** (Builder → **Connectors**). Connectors give the agent persistent context and extra channels; adding one needs **Contributor** on the *agent's* resource group.
   - **AMS Log Analytics workspace** *(recommended)* — persistent awareness of your AMS workspace for the HANA / OS / cluster health layers. Not strictly required: with **Log Analytics Reader** (step 2) + the workspace ID in onboarding the agent can already query it via built-in tools; the connector adds ambient context and richer diagnostics.
   - **Application Insights** *(optional)* — only if your app tier emits to it; **additive** to AMS/ARM, not a replacement.
   - **Notifications — Teams and/or Outlook** *(optional, recommended)* — lets the agent send investigation **summaries** (root cause, impact, recommended actions) to a channel or inbox. Each uses an **OAuth sign-in** (the agent sends *as* that account) plus a **user-assigned managed identity** — create one and reuse it across connectors. The incident-analysis and self-healing skills post here when configured; otherwise they report in chat.
   - **Docs / knowledge** *(optional)* — e.g. an **MCP** connector to Microsoft Learn docs (or GitHub / Azure DevOps) to ground answers.
4. **Connect the repo (Code Access).** Builder → **Code Access** → connect your fork. This lets the agent **read and cite** the repo's knowledge + reference content during investigations (the skills themselves install separately in step 5). What it uses:
   - **`knowledge/`** — the verbatim **SAP Note 1928533** (supported products) and **`sap-certified-vms.json`** (HANA/SAP-certified VM index) that the deployment-readiness skill cites.
   - **`config/`** — the **SAP landscape inventory** template + example (SIDs, resource groups, VMs, roles).
   - **`docs/`** — architecture, adoption planner, setup, and reference guides.
   - **`infra/`, `collector/`, `proxy-mcp/`** — the IaC, collector script, and MCP proxy code (for explaining or troubleshooting the deployment).

   > **Staying in sync:** Code Access **re-indexes on its own schedule** — pushes to your fork's `knowledge/`, `config/`, `docs/`, or `infra/` are picked up **automatically**, no manual step.
5. **Install skills (Plugins).** Builder → **Plugins** → **Add marketplace** (your fork's URL) or **Install from URL**, then install **`sap-sre-core`** (the 10 Azure-native skills).
   - **Where they live:** the tiered plugins in [`plugins/`](plugins/) — `sap-sre-core` (10), `sap-sre-config` (1), `sap-sre-proxy-ops` (2) — declared in [`.github/plugin/marketplace.json`](.github/plugin/marketplace.json); each skill is a `plugins/<plugin>/skills/<name>/SKILL.md`.
   - **How import works:** installing copies the plugin **pinned to the exact commit** — a version-locked snapshot, not a live link.
   - **How updates reach the agent:** merged changes do **not** arrive automatically (unlike Code Access). In **Plugins**, open the installed plugin and click **Update** — the portal shows a **SHA-256 diff** of the new commit vs. the installed one before you apply. Each agent updates on its own schedule, so you can stage a change on a test agent before promoting it. See [Updates & version pinning](docs/reference.md#updates--version-pinning).
6. **Onboard.** Settings → **Team Onboarding** → paste your filled [onboarding template](onboarding/team-onboarding.template.md) (systems, subscription, AMS workspace).
7. **Try it:** *"What SAP systems do I have?"* · *"Is AB1 healthy?"* · *"How much does AB1 cost?"*

### Phase 1 — Config store · ~1 hr · adds STAF validation + config-enriched RCA/perf/HA · +1 skill

1. **Deploy the store.** Creates a private storage account, the `sap-configs` container, a collector identity, and grants the **agent MI Storage Blob Data Reader** so it reads configs **directly — no proxy**:
   ```powershell
   ./infra/deploy-sre-infra.ps1 -SubscriptionId <sub> -StorageAccountName <name> -SreAgentUmiPrincipalId <agent-mi>
   ```
2. **Reach private storage.** VNet-integrate the agent (portal: the agent resource → **Networking** → VNet integration) and allow its delegated subnet on the storage firewall. Pass your SAP VM subnets via `-SapSubnetIds` on the deploy script so the collector can upload. *(Required — the storage account is private with no public access, so the agent must reach it over the VNet.)*
3. **Collect configs.** Assign the collector UMI to each SAP VM, then run the collector:
   ```bash
   az vm identity assign -g <SAP-RG> -n <VM> --identities <collector-umi-resource-id>
   az vm run-command invoke -g <SAP-RG> -n <VM> --command-id RunShellScript --scripts @collector/collect-sap-configs.sh
   ```
4. **Install skills.** Builder → **Plugins** → install **`sap-sre-config`**.
5. **Try it:** *"Run config checks for AB1."*

### Phase 2 — Live commands · optional · ~1 hr · adds command-runner + self-healing · +2 skills

1. **Deploy the MCP proxy.** A VNet-integrated Container App that exposes an approved set of **read-only** commands over MCP (its own resource group + a custom RBAC role scoped to your SAP RGs — no storage role). It prints the **MCP endpoint URL** and **API key**:
   ```powershell
   ./infra/deploy-mcp-proxy.ps1 -SubscriptionId <sub> -SapResourceGroups <RG1>,<RG2> -McpApiKey <key>
   ```
2. **Register the connector.** Builder → **MCP / Connectors** → add the MCP server URL with header `x-api-key: <key>`, and add the URL + key to **Team Onboarding**.
3. **Install skills.** Builder → **Plugins** → install **`sap-sre-proxy-ops`**.
4. **Try it:** *"Run uptime on AB1vm."*

## Not sure what to deploy?

Every part of the architecture is **optional and phased** - enable only what you need now. Use the **[Adoption planner](docs/adoption-planner.md)** to pick your components.

## Documentation

Short, task-focused docs for the details:

| Topic | Doc |
|-------|-----|
| Architecture & what it does | [docs/architecture.md](docs/architecture.md) |
| Adoption planner (mix & match by phase) | [docs/adoption-planner.md](docs/adoption-planner.md) |
| Full setup detail (Steps 1-17) | [docs/setup-detailed.md](docs/setup-detailed.md) |
| Repository layout (folder guide) | [docs/repo-layout.md](docs/repo-layout.md) |
| Operations (update skills, add systems) | [docs/operations.md](docs/operations.md) |
| Reference (RBAC, MCP tools, config flow, troubleshooting) | [docs/reference.md](docs/reference.md) |
| Knowledge sources | [knowledge/README.md](knowledge/README.md) |

## References

- [SAP Testing Automation Framework (STAF)](https://github.com/Azure/sap-automation-qa)
- [Azure Monitor for SAP Solutions](https://learn.microsoft.com/azure/sap/monitor/)
- [SAP on Azure Best Practices](https://learn.microsoft.com/azure/sap/workloads/)
- [Azure SRE Agent](https://sre.azure.com)
- [SAP Note 1928533](https://launchpad.support.sap.com/#/notes/1928533) — SAP applications on Azure: Supported products
- [HANA Hardware Directory](https://www.sap.com/dmc/exp/2014-09-02-hana-hardware/enEN/iaas.html) — Certified IaaS platforms
