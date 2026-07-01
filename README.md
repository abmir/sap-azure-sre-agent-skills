# SAP Azure SRE Agent

AI-powered SRE agent for SAP HANA and NetWeaver on Azure. Automates health monitoring, STAF configuration validation, incident analysis, and cost optimization — all through natural language at [sre.azure.com](https://sre.azure.com).

**Three adoption tiers** — pick what fits your security posture. Start with Azure-native telemetry using your existing Azure signals, add a customer-controlled config store for STAF compliance that unlocks the config validator and enriches 4 more skills (incident RCA, performance diagnostics, HA cluster health, maintenance) — 5 skills in all, then add a brokered command proxy to run an approved set of read-only commands on your SAP VMs. See [Tiered plugins](#tiered-plugins) below, or the full [Adoption planner](docs/adoption-planner.md).

## This repo is the single source of truth

This repository **is** the agent's skill pack. You don't upload skills by hand — you **fork this repo and point your SRE Agent at it**, then install the skills you need. Updates ship as pull requests; each agent picks them up on its own schedule.

The agent consumes this repo through two GitHub connections (plus one manual paste):

| Path | Portal location | What it pulls from this repo |
|------|-----------------|------------------------------|
| **Plugin Marketplace** | Builder → Plugins | **Skills** (and any MCP server configs) — the tiered plugins in [`plugins/`](plugins/) via [`.github/plugin/marketplace.json`](.github/plugin/marketplace.json). Installed as a version-pinned copy. |
| **Code Access** | Builder → Code Access | **Everything else in the repo** — `knowledge/` (incl. the SAP/HANA cert reference), `config/`, `docs/`, and the proxy/IaC code. One connection covers it all. |
| **Team Onboarding** (manual) | Settings → Team Onboarding | The filled onboarding text. **Pasted by hand** — it carries secrets (proxy URL, API key) so it is *not* read from the repo. |

> **Note:** plugin installs are **commit-pinned** — merges reach an agent only when someone clicks **Update** on the plugin (it's not a live feed). See [Updates & version pinning](docs/reference.md#updates--version-pinning).

### Tiered plugins

| Plugin | Tier | Skills | Requires |
|--------|------|:------:|----------|
| [`sap-sre-core`](plugins/sap-sre-core) | Azure-Native | 10 | Nothing (Azure APIs + AMS) |
| [`sap-sre-config`](plugins/sap-sre-config) | + Config Store | 1 | Storage Account |
| [`sap-sre-proxy-ops`](plugins/sap-sre-proxy-ops) | + Live Proxy | 2 | SRE Proxy + custom RBAC |

Install only the plugins your tier supports. A security-strict customer installs just `sap-sre-core` and never sees the proxy skills.

## Quick start — implement in 3 phases

Pick your components with the [Adoption planner](docs/adoption-planner.md); this is the simplest happy path. Each phase is independent — stop after any phase.

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

## Verify it works

Open your agent at [sre.azure.com](https://sre.azure.com) and ask:

- **After Phase 0:** "What SAP systems do I have?" - "Is AB1 healthy?" - "How much does AB1 cost?"
- **After Phase 1:** "Run config checks for AB1."
- **After Phase 2:** "Run uptime on AB1vm."

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
| Reference (RBAC, API, config flow, troubleshooting) | [docs/reference.md](docs/reference.md) |
| Knowledge sources | [knowledge/README.md](knowledge/README.md) |

## References

- [SAP Testing Automation Framework (STAF)](https://github.com/Azure/sap-automation-qa)
- [Azure Monitor for SAP Solutions](https://learn.microsoft.com/azure/sap/monitor/)
- [SAP on Azure Best Practices](https://learn.microsoft.com/azure/sap/workloads/)
- [Azure SRE Agent](https://sre.azure.com)
- [SAP Note 1928533](https://launchpad.support.sap.com/#/notes/1928533) — SAP applications on Azure: Supported products
- [HANA Hardware Directory](https://www.sap.com/dmc/exp/2014-09-02-hana-hardware/enEN/iaas.html) — Certified IaaS platforms
