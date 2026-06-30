# Knowledge sources

The SRE Agent **Plugin Marketplace** delivers *skills and MCP configs* from this repo, but it does
**not** deliver *knowledge files*. Knowledge is a separate connection. Wire it up so this repo is
the single source of truth for both.

## Connect this repo as a knowledge source

**Builder → Knowledge base → Add repository** and point at your fork of
`mcaps-microsoft/sap-azure-sre-agent`. The agent then indexes the repo (this `knowledge/` folder,
`config/`, and `docs/`) and references it automatically during investigations.

Optionally also enable **Builder → Code Access** on the same repo so root-cause analysis can cite
the proxy app, collector script, and IaC by file and commit.

## Add these web pages as knowledge

Add via **Builder → Knowledge base → Add web page** (kept as live URLs so they stay current):

| Source | URL | Why |
|--------|-----|-----|
| SAP Note 1928533 | https://launchpad.support.sap.com/#/notes/1928533 | Supported VM SKUs / OS for SAP (non-HANA certification) |
| HANA Hardware Directory | https://www.sap.com/dmc/exp/2014-09-02-hana-hardware/enEN/iaas.html | HANA-certified IaaS VMs |

## Repo-hosted knowledge

| File | Purpose |
|------|---------|
| `../config/sap-landscape-inventory.template.json` | Fill in your SAP systems, then upload as a Knowledge Source (or let the collector publish the live inventory to blob) |

> **Tip:** the landscape inventory is the single most valuable knowledge file — it gives the agent
> application context (which systems are critical, how they connect, role per VM) that live Azure
> Resource Graph alone can't provide.
