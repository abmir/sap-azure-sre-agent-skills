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

1. **Create the agent.** At [sre.azure.com](https://sre.azure.com) create an SRE Agent, enable its built-in **Tools** and **Skills** (Capabilities), and note its **Managed Identity** object ID (Identity blade).
2. **Grant read access** on each SAP resource group (add **Cost Management Reader** at subscription scope for cost skills):
   ```bash
   az role assignment create --assignee-object-id <agent-mi> --assignee-principal-type ServicePrincipal --role Reader             --scope /subscriptions/<sub>/resourceGroups/<SAP-RG>
   az role assignment create --assignee-object-id <agent-mi> --assignee-principal-type ServicePrincipal --role "Monitoring Reader" --scope /subscriptions/<sub>/resourceGroups/<SAP-RG>
   ```
   **If you use AMS**, also grant the agent access to the AMS Log Analytics workspace — it lives in a *different* resource group (e.g. `mrg-sapmon-…`), and without this the HANA/OS/cluster health layers come back empty:
   ```bash
   az role assignment create --assignee-object-id <agent-mi> --assignee-principal-type ServicePrincipal --role "Log Analytics Reader" --scope /subscriptions/<sub>/resourceGroups/<AMS-RG>
   ```
3. **Connect the repo.** Builder → **Code Access** → connect your fork (skills' source + knowledge base).
4. **Install skills.** Builder → **Plugins** → install **`sap-sre-core`**.
5. **Onboard.** Settings → **Team Onboarding** → paste your filled [onboarding template](onboarding/team-onboarding.template.md) (systems, subscription, AMS workspace).
6. **Try it:** *"What SAP systems do I have?"* · *"Is AB1 healthy?"* · *"How much does AB1 cost?"*

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
