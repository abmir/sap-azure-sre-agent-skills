---
name: sap-operational-health
description: "Unified health dashboard for SAP systems. Checks AMS provider health, data freshness, VM power state, CPU/memory/disk metrics, accelerated networking, proximity placement, Resource Health, and alert coverage. Traffic-light output per layer. Enriched with live VM data when command proxy is available."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - QueryLogAnalyticsByWorkspaceId
    - GetMetricTimeSeriesElementsForAzureResource
    - PlotBarChart
    - PlotHeatmap
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Config reads & proxy fallback**: Stored SAP/OS configs are read **directly from the `sap-configs` blob container using the agent's own Managed Identity** (`--auth-mode login`) — there is **no config proxy**. The MCP command proxy is optional and runs only **live VM commands**; if it is not deployed or errors (timeout, 5xx, unreachable), continue with stored blob configs + Azure-native sources (AMS, ARM API, Azure Monitor). Never block the skill on the proxy.

**Telemetry source adaptation**: HANA/OS/SAP-app depth depends on which telemetry source is listed in `## Deployed Infrastructure`. Adapt automatically — never hard-fail because a specific source is absent:
- **AMS (Azure Monitor for SAP) listed** → use the AMS KQL tables below (deepest HANA/OS/cluster signals).
- **No AMS, but an SAP APM connector is listed (SAP Cloud ALM / Focus Run / Dynatrace)** → invoke the SAP APM Connector for SAP-app/HANA signals; use Azure Monitor platform metrics for VM/infra.
- **No SAP telemetry source at all** → use Azure platform metrics only (VM Insights, Azure Monitor, Resource Health) for VM-level CPU/memory/disk/network, and disclose in the header: "SAP-application-level telemetry is not configured; results use Azure platform metrics. Add AMS or an APM connector for HANA/SAP depth."
- "Health check for AB1" / "Status of all systems"
- "Any CPU or memory pressure on AB3 VMs?"
- "Are there network issues between DB and App servers?"
- "Check if AB1 VMs have accelerated networking"

## Topology handling (all 8 system types)

Read the target system's `architecture` (`scale-up`|`scale-out`) and `deployment` (`standalone`|`distributed`|`high-availability`|`disaster-recovery`) from the inventory and adapt — never assume AB1's single-VM shape:
- **scale-out** → the HANA DB spans master + worker (+ standby) nodes. Group every HANA/OS KQL by `HOST_s` and report each DB node's L2/L4 health individually; flag worker imbalance and standby readiness. `scale-up` = one active DB node per replica.
- **standalone / distributed** → no Pacemaker/HSR — set **L3 Cluster = ⚪ N/A** and skip the Pacemaker query (never error).
- **high-availability** → run the L3 Pacemaker + HSR checks.
- **disaster-recovery** → also show the cross-region DR replica (`ha_role: dr`, `dr.region`) as a separate health line with its async HSR status; don't fold it into the primary site.

## Data Sources

| Source | Primary/Fallback | Freshness | What It Provides |
|--------|-----------------|-----------|-----------------|
| Azure Monitor Metrics | Primary | Real-time (1 min) | VM CPU, memory, disk IOPS/latency, network |
| Azure Resource Health | Primary | Real-time | Platform events (planned/unplanned maintenance) |
| Azure VM Power State | Primary | Real-time | Running/stopped/deallocated |
| AMS Log Analytics | Primary | 2-5 min | HANA availability, OS metrics, Pacemaker state, SAP processes |
| Activity Log | Primary | Real-time | Recent changes |
| **`sap-configs` blob (agent MI, direct)** | **Primary for stored configs** | Cron (weekly) | ethtool output, VM configs, accelerated networking — read **directly with the agent's own Managed Identity** (`az storage blob ... --auth-mode login`); **no proxy** |
| MCP command proxy (run_batch, optional) | Live enrichment only | Live | Freshest ethtool / PPG / live VM state — **only if** the MCP command proxy is configured |

## Authentication

**IMPORTANT — Azure API Access:** Do NOT use IMDS tokens (169.254.169.254) or ManagedIdentityCredential — they are not available in the agent sandbox. Instead:
- For Azure Resource Manager queries: Use the built-in `GetArmResourceAsJson` or `RunAzCliReadCommands` tools
- For Log Analytics queries: Use the built-in `QueryLogAnalyticsByWorkspaceId` tool  
- For metrics: Use the built-in `GetMetricTimeSeriesElementsForAzureResource` tool
- For live VM commands: invoke the **SAP Command Runner** skill (the `sap-sre-proxy` MCP connector) — never make direct HTTP or proxy calls

```python
# Use the built-in tools above for Azure API access.
import json
from datetime import datetime, timedelta, timezone

# SUB_ID: Use subscription_id from Team Onboarding
# AMS_WORKSPACE_ID: Use ams_workspace_id from Team Onboarding (only if AMS is listed)
```

**Getting VM data (adaptive — no dead endpoints):**
- **Stored configs** (accelerated networking, ethtool, VM configs) — read **directly** from the `sap-configs` blob with the agent's own MI: `RunAzCliReadCommands` → `az storage blob download --auth-mode login --account-name <sa> --container-name sap-configs --name <SID>/<host>/latest/...`. Available whenever a **Storage Account** is listed.
- **Live VM state** (freshest ethtool / PPG / uptime) — only when the **MCP command proxy** is configured: invoke the **SAP Command Runner** skill (`run_batch`) with the health commands below. If it is not configured or errors, silently use the stored blob configs. Never block on it.
- **Landscape inventory** — read from the agent Knowledge Base (primary) or the `sap-configs` blob directly. There is **no** `/api/registry` endpoint — the proxy is MCP (live commands only).

**Standard commands for health checks**:
```python
HEALTH_COMMANDS = [
    {"id": "ethtool", "cmd": "ethtool -i eth0 2>/dev/null | grep driver"},
    {"id": "accel_net", "cmd": "ethtool -i eth0 2>/dev/null | grep -q 'driver: mlx' && echo 'enabled' || echo 'disabled'"},
    {"id": "uptime", "cmd": "uptime"},
    {"id": "free_mem", "cmd": "free -m | grep Mem"},
    {"id": "df_usage", "cmd": "df -h /hana/data /hana/log /hana/shared 2>/dev/null || df -h /"},
]
```

## Health Dashboard — 5 Layers

### Layer 1: Azure Infrastructure (ARM API + Azure Monitor)
| Check | Source | GREEN | AMBER | RED |
|-------|--------|-------|-------|-----|
| VM Power State | ARM instance view | Running | — | Stopped/Deallocated |
| CPU % (avg 1h) | Azure Monitor | <70% | 70-90% | >90% |
| Memory % (avg 1h) | Azure Monitor | <80% | 80-95% | >95% |
| Data Disk IOPS % | Azure Monitor | <70% | 70-90% | >90% (throttled) |
| Network Packets Dropped | Azure Monitor | 0 | 1-100 | >100 |
| Accelerated Networking | ARM NIC | Enabled | — | Disabled |
| Proximity Placement | ARM PPG | In PPG | — | No PPG |
| Resource Health | Resource Health API | Available | Degraded | Unavailable |

### Layer 2: Guest OS (AMS)
> Run `Prometheus_OSExporter_CL | getschema` FIRST — the node_exporter column names below are a template and vary by AMS collector version; bind to the real columns before running.
```
Prometheus_OSExporter_CL
| where TimeGenerated > ago(15m)
| summarize latest_cpu=max(node_cpu_seconds_total_d), latest_mem=max(node_memory_MemTotal_bytes_d - node_memory_MemAvailable_bytes_d) by Computer
```

### Layer 3: Pacemaker Cluster (AMS — only when `deployment` is high-availability or disaster-recovery; skip for standalone/distributed)
> Run `Prometheus_HaClusterExporter_CL | getschema` FIRST — the `ha_cluster_*` column names below are a template and vary by AMS collector version; bind to the real columns before running.
```
Prometheus_HaClusterExporter_CL
| where TimeGenerated > ago(15m)
| summarize nodes_online=dcountif(ha_cluster_pacemaker_nodes_d, ha_cluster_pacemaker_nodes_d == 1) by ClusterName_s
```

### Layer 4: HANA Database (AMS)
> Run `getschema` first — column names vary by AMS version. HANA tables key on `sapsid_s` (not `SID_s`); availability fields are `SYSTEM_ACTIVE_s` / `DATABASE_ACTIVE_s` / `HOST_ACTIVE_s`.
```
SapHana_SystemAvailability_CL
| where TimeGenerated > ago(15m)
| summarize arg_max(TimeGenerated, *) by sapsid_s, HOST_s
| project sapsid_s, HOST_s, SYSTEM_ACTIVE_s, DATABASE_ACTIVE_s, DATABASE_NAME_s
```

### Layer 5: SAP Application (AMS — only if the NetWeaver provider is configured)
> Optional provider. If `SapNetweaver_GetProcessList_CL` doesn't exist in the workspace, skip Layer 5 and note the NetWeaver provider isn't configured.
```
SapNetweaver_GetProcessList_CL
| where TimeGenerated > ago(15m)
| summarize arg_max(TimeGenerated, *) by sapsid_s, instanceNr_s, name_s
| project sapsid_s, instanceNr_s, name_s, dispstatus_s
```

## Output Format

Traffic-light dashboard per system:
```
AB1 — Overall: 🟢 GREEN
  L1 Infrastructure: 🟢 (CPU 23%, Mem 45%, Disk 12%, AccelNet ✓, PPG ✓)
  L2 Guest OS:       🟢 (CPU 18%, Mem 42%, Swap 0%)
  L3 Cluster:        ⚪ N/A (no HA)
  L4 HANA:           🟢 (ACTIVE, DB1 online)
  L5 SAP App:        🟡 (Instance 02 dispatcher GRAY)
  Alerts:            🟢 No active alerts for AB1
```

### Alert Filtering — CRITICAL

**When checking alerts for a specific SAP system, ALWAYS filter by that system's provider instance tag.**

Alert rules in AMS are scoped to the shared Log Analytics workspace, which monitors ALL SAP systems (AB1, HSO, etc.). If you query alerts without filtering, you will see alerts from OTHER systems — causing false positives.

**How to filter:**
1. When querying fired alerts via `RunAzCliReadCommands`, filter by the `sap-system` or `profile-id` tag on the alert rule
2. Use this pattern:
```
az monitor scheduled-query list --resource-group mrg-sapmon-abb --subscription {SUB_ID} --query "[?tags.\"sap-system\"=='{SID}']"
```
3. Or when listing fired alert instances, cross-reference the alert rule name against the system being checked

**Provider instance mapping** (from Team Onboarding):
| System | AMS Provider Instance | Alert Tag |
|--------|----------------------|-----------|
| AB1 | `sap-hana-pr-AB1` | `sap-system: AB1` |
| HSO | `hana-pr-HSO` | `profile-id: hana-pr-HSO` |

**NEVER show alerts from system X when the user asks about system Y.** If unsure which system an alert belongs to, check the alert rule's KQL query for the `PROVIDER_INSTANCE_s` filter or the alert rule's tags.
