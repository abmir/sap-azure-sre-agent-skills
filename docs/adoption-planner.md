# Adoption planner

## Adoption Tiers

The agent supports three deployment tiers. Each tier is a strict superset of the previous one — start with Azure-native, add a config store when you need config-level visibility, add the MCP command proxy when you're ready for live commands. Skills auto-detect what's available from the `## Deployed Infrastructure` section in Team Onboarding — no mode numbers to manage.

> **These three tiers are *presets*, not a rigid ladder.** Every box in the [architecture](architecture.md) is **independently optional** and can be adopted in any **phase**. If you want to mix and match — e.g. collect configs to storage but *not* deploy the proxy, keep SAP telemetry in SAP Cloud ALM / Focus Run for now, or defer ServiceNow — see the component menu below.

| Tier | What's Deployed | Capabilities | Customer Profile | Effort |
|:----:|----------------|--------------|------------------|:------:|
| **Azure-Native** | Nothing (just `sre.azure.com` + your existing AMS) | 10 skills using only Azure APIs and AMS telemetry. No config validation, no live commands. | Security-strict customers. AMS already deployed. Wants insight without standing up new infra. | ~30 min |
| **+ Config Store** | **Storage Account** (`sap-configs` blob container) + **collector UMI** (assigned to SAP VMs) | All base skills + **STAF config validation** + config-enriched RCA / performance / HA skills. 11 skills total. | Wants STAF compliance reporting and richer RCA, but does not want a proxy executing live commands. | ~1 hr |
| **+ Live Commands** | Config Store + **MCP command proxy** (VNet-integrated Container App exposing read-only commands over MCP) + **proxy UMI** + custom RBAC | All skills including **live read-only VM commands** + **self-healing remediation**. All 13 skills. | Wants full SRE automation including remediation. Accepts a brokered command path with custom RBAC. | ~2 hr |

### How config validation works

- **No Storage Account** — Config Validator tells the user a storage account is needed.
- **Storage Account deployed** — The Config Validator skill **fetches STAF check definitions directly from `Azure/sap-automation-qa` on GitHub at runtime**, reads collected configs from your storage account, and runs the comparison **in-skill** (Python via `ExecutePythonCode`). No proxy involved.
- **Proxy also deployed** — Same as above, with the added option to trigger a fresh on-demand collection through the proxy before validating.

### Adding infrastructure later

- **Add Config Store** — Run `infra/deploy-sre-infra.ps1 -SreAgentUmiPrincipalId <agent-mi-object-id>`; assign the collector UMI to SAP VMs; deploy the collector. Add the `Storage Account` line to Team Onboarding. Import the Config Validator skill on the portal.
- **Add Live Commands** — Run `infra/deploy-mcp-proxy.ps1`; register the `sap-sre-proxy` MCP connector and add the `MCP Command Proxy` line to Team Onboarding. Import `sap-command-runner` and `sap-self-healing` skills.

### Skills × Infrastructure capability matrix

How each of the 13 custom skills behaves based on what infrastructure is deployed. **Full** = skill operates at its full design intent. **Enhanced** = skill adds config or live data. **Blocked** = skill refuses politely and explains what's needed.

| # | Skill | No infra | + Storage Account | + MCP Proxy | Notes |
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
| **G. MCP Command Proxy** — Container App + proxy UMI + custom RBAC | Live read-only VM commands + self-healing | Proxy deployed + `sap-sre-proxy` connector | command-runner & self-healing blocked | 2+ |
| **H. Incident platform — Azure Monitor** | Alert-driven investigations | Azure Monitor alerts | manual / chat-driven only | 1 or 2 |
| **H′. Incident platform — ServiceNow / PagerDuty** | Ticketing integration | Connector | use Azure Monitor or none | usually 2 |
| **I. Notification connectors** — Teams / Outlook | Push alerts & summaries to people | Connector | no push notifications | any |
| **J. Third-party connectors** — Dynatrace / Sentinel / Focus Run | Extra observability / SIEM signals | Connector | Azure-native signals only | 2+ |

> **Key separation (the common Phase-1 shape):** the **Config Store (F)** — collector UMI + cron uploading configs to a Storage Account — is **independent of the MCP command proxy (G)**. You can enable config collection and STAF validation **without** the Container App proxy: deploy the collector with `az vm run-command` instead of the proxy (see [Adding infrastructure later](#adding-infrastructure-later)). The proxy is only needed for *live* VM commands and self-healing (Phase 2+).

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
| MCP Command Proxy + `sap-sre-proxy-ops` (G, B) | | ✅ | |
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

