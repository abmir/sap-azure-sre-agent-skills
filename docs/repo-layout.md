# Repository layout

## Repository Layout

```
sap-azure-sre-agent/
├── .github/plugin/     # marketplace.json — catalog the agent installs from
├── plugins/            # the 13 SKILLS, in 3 tiered plugins  ← AGENT INSTALLS THIS
│   ├── sap-sre-core/       #   Tier 1 — 10 Azure-native skills (no infra)
│   ├── sap-sre-config/     #   Tier 2 — STAF config validator (needs Storage)
│   └── sap-sre-proxy-ops/  #   Tier 3 — command runner + self-healing (needs proxy)
├── knowledge/          # reference data the agent reads (SAP Note, certified-VM index)
├── config/             # your SAP landscape inventory (template + example)
├── onboarding/         # team-onboarding template you fill in and paste into the agent
├── docs/               # architecture diagram
├── infra/              # deploy scripts + custom RBAC role   ← OPERATOR RUNS THIS
├── proxy/              # OPTIONAL REST proxy (live VM commands)
├── proxy-mcp/          # OPTIONAL MCP server (the proxy's target form)
└── collector/          # script that runs on your SAP VMs (uploads configs)
```

### What each folder is for

**Agent-facing** — what a customer forks and points the SRE Agent at:

| Folder | What it holds | How the agent uses it |
|--------|---------------|-----------------------|
| `.github/plugin/` | `marketplace.json` — the plugin catalog | Builder → **Plugins** reads it to list installable plugins |
| `plugins/` | The **13 skills**, packaged as 3 tiered plugins (`plugin.json` + `skills/<name>/SKILL.md`) | Installed via the **Plugin Marketplace** — this is the core |
| `knowledge/` | The verbatim SAP Note slot + the certified-VM index the deployment skill fetches | Connected via **Code Access**; read as reference |
| `config/` | `sap-landscape-inventory` — **template** (blank) + **example** (fill in your systems) | Uploaded as a knowledge source, or read from your fork |
| `onboarding/` | `team-onboarding.template.md` — your runtime context | You **fill it in and paste** it into Settings → Team Onboarding (has secrets — not read from the repo) |
| `docs/` | The architecture diagram | Shown in this README |

**Operator-facing** — what you deploy or run once, only if a tier needs it (the agent never installs these):

| Folder | What it holds | When you need it |
|--------|---------------|------------------|
| `infra/` | `deploy-sre-infra.ps1` (config store), `deploy-mcp-proxy.ps1` (MCP command proxy), `sap-sre-agent-role.json` (least-privilege RBAC role) | Phase 1 (config store) and Phase 2 (proxy) |
| `collector/` | `collect-sap-configs.sh` — runs on your SAP VMs (cron) to upload configs to storage | Phase 1, if you want config validation |
| `proxy-mcp/` | **Optional** MCP command proxy — the read-only VM commands as native MCP tools, registered as a connector | Phase 2, for live commands / self-healing |

> **Rule of thumb:** `plugins/`, `knowledge/`, `config/`, `onboarding/`, `docs/` = the agent reads them. `infra/`, `proxy-mcp/`, `collector/` = you deploy them once, and all are **optional** except that `infra/` + `collector/` are needed for the config store.

