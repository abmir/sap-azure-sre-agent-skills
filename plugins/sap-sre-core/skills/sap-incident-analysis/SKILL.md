---
name: sap-incident-analysis
description: "Cross-layer root cause analysis for SAP incidents. Correlates Azure infrastructure events, Guest OS, Pacemaker cluster, HANA database, and SAP application evidence to explain why a system is down, slow, or unstable. No proxy required — uses Azure-native APIs only."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - GetActivityLogsSummary
    - GetChangeHistory
    - QueryLogAnalyticsByWorkspaceId
    - GetMetricTimeSeriesElementsForAzureResource
    - PlotAreaChartWithCorrelation
    - PlotBarChart
    - PlotScatter
    - CreateScheduledMonitoringTask
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Config reads & proxy fallback**: Stored SAP/OS configs are read **directly from the `sap-configs` blob container using the agent's own Managed Identity** (`--auth-mode login`) — there is **no config proxy**. The MCP command proxy is optional and runs only **live VM commands**; if it is not deployed or errors (timeout, 5xx, unreachable), continue with stored blob configs + Azure-native sources (AMS, ARM API, Azure Monitor). Never block the skill on the proxy.

**Telemetry & incident-platform adaptation**: This skill adapts to whichever telemetry and incident platform are listed in `## Deployed Infrastructure`:
- **Telemetry** — AMS if listed (deepest); else an SAP APM connector (SAP Cloud ALM / Focus Run / Dynatrace) if listed; else Azure platform metrics + Activity Log only. Disclose the reduced fidelity in the report header when AMS/APM are absent.
- **Change correlation** — always Azure Activity Log; **also** ServiceNow changes if a ServiceNow connector is listed.
- **Incident creation** — if an incident platform (ServiceNow) is listed, create/update the incident with the RCA payload; otherwise send the RCA via Teams/Outlook only. Never fail because a platform is absent.

## Infrastructure Requirements

This skill adapts automatically based on what infrastructure is listed in the `## Deployed Infrastructure` section of Team Onboarding.

- **No infrastructure listed** — RCA uses only AMS telemetry + Activity Log + Resource Health + ARM API. No config files, no live OS state. Still produces a meaningful timeline and identifies infrastructure-layer root causes, but cannot inspect sysctl, `global.ini`, corosync, or any OS-level config. **Always disclose in the report header**: "Config-layer and live-OS evidence are unavailable (no Storage Account or MCP command proxy in Deployed Infrastructure). For deeper RCA, deploy a config store and/or proxy."
- **Storage Account listed** — Also reads collected configs (sysctl, `global.ini`, corosync, etc.) from the `sap-configs` blob container via `RunAzCliReadCommands`. Can correlate "sysctl change at T-2h" with "HANA OOM at T".
- **MCP command proxy also listed** — Also pulls live OS state at the moment of the incident (current `dmesg` tail, current process list, current `crm_mon`) through the command proxy. Best fidelity.

## When to Use

- "Why did SAP go down?" / "Cross-layer RCA for AB1"
- "What happened at 3 AM on HSO?"
- "Analyze the alert that just fired"
- "Give me a root-cause timeline"
- **Auto-triggered** by Azure Monitor alert response plans

## Topology handling (all 8 system types)

Read the system's `architecture` + `deployment` from the inventory and scope the layers accordingly — never assume AB1's single-VM shape:
- **scale-out** → group Layer-2 / Layer-4 KQL by `HOST_s` and reconstruct the timeline per DB node (master/workers/standby); a single hot or failed worker is the root cause you must surface, not an aggregate.
- **standalone / distributed** → skip Layer 3 (Pacemaker) entirely (no cluster) — note it as N/A, don't error.
- **high-availability** → run Layer 3 and correlate fencing / takeover events.
- **disaster-recovery** → also evaluate async HSR replica lag and whether the DR region/replica was part of the incident window.

## Core Principle: Bottom-Up Analysis

If Layer 1 (Azure Infrastructure) is RED, that's the root cause — upper layers are collateral damage. Always find the **deepest** infrastructure failure and explain the cascade upward.

## Data Sources — Each Layer Has a Live Source

| Layer | Source | Freshness |
|-------|--------|-----------|
| 1. Azure Infrastructure | ARM API, Azure Monitor metrics, Resource Health | Real-time |
| 2. Guest OS | AMS: `Prometheus_OSExporter_CL` | 2-5 min |
| 3. Pacemaker Cluster | AMS: `Prometheus_HaClusterExporter_CL` | 2-5 min |
| 4. HANA Database | AMS: `SapHana_SystemAvailability_CL`, `SapHana_SystemReplication_CL` | 2-5 min |
| 5. SAP Application | AMS: `SapNetweaver_GetProcessList_CL` (if available) | 2-5 min |
| Change Correlation | Activity Log (always) + ServiceNow changes (if configured) | Real-time |
| HANA Deep Telemetry | AMS (always) + Grafana/Focus Run (if configured) | 2-5 min |

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

# Live VM/OS state at incident time (optional): invoke the SAP Command Runner skill
# (MCP run_command / run_batch) — only when the MCP command proxy is configured.

# Conditional integrations — invoked as separate skills when available
# SAP ServiceNow Connector: queries changes, creates incidents (only if ServiceNow configured)
# SAP APM Connector: pulls Dynatrace / SAP Cloud ALM / Focus Run HANA telemetry (only if configured)
# When not configured, fall back to Azure Activity Log and AMS (or Azure platform metrics) respectively
```

## RCA Procedure

### Step 1: Layer 1 — Azure Infrastructure
- ARM instance view (power state, platform faults)
- Azure Monitor metrics: CPU, memory, disk IOPS %, network drops (last 6h)
- Resource Health events
- Activity Log: VM deallocate/restart/redeploy events

### Step 2: Layer 2 — Guest OS
```
Prometheus_OSExporter_CL
| where TimeGenerated > ago(6h)
| where Computer in ("<vm_list>")
| summarize avg(node_cpu_seconds_total_d), avg(node_memory_MemAvailable_bytes_d), max(node_filesystem_avail_bytes_d) by bin(TimeGenerated, 5m), Computer
```

### Step 3: Layer 3 — Pacemaker Cluster (only when `deployment` is high-availability or disaster-recovery; skip for standalone/distributed)
```
Prometheus_HaClusterExporter_CL
| where TimeGenerated > ago(6h)
| summarize arg_max(TimeGenerated, *) by ha_cluster_pacemaker_nodes_s
| project TimeGenerated, Node=ha_cluster_pacemaker_nodes_s, Status=ha_cluster_pacemaker_nodes_d
```

### Step 4: Layer 4 — HANA Database
> Run `getschema` first — HANA tables key on `sapsid_s` (not `SID_s`); availability is `SYSTEM_ACTIVE_s` / `DATABASE_ACTIVE_s`.
```
SapHana_SystemAvailability_CL
| where TimeGenerated > ago(6h)
| summarize arg_max(TimeGenerated, *) by sapsid_s, HOST_s
| project TimeGenerated, sapsid_s, HOST_s, SYSTEM_ACTIVE_s, DATABASE_ACTIVE_s
```

### Step 5: Change Correlation (conditional)
```python
# Always: Azure Activity Log (use GetActivityLogsSummary and GetChangeHistory tools)

# Conditional: Invoke SAP ServiceNow Connector skill for recent changes
# The ServiceNow Connector uses targeted queries (filtered by CI + time window, max 20 results)
# If ServiceNow is not configured, the connector skill returns None and we use Activity Log only
```

### Step 6: HANA Deep Telemetry (conditional)
```python
# Always: AMS HANA tables
hana_telemetry = query_log_analytics("SapHana_LoadHistory_CL | where TimeGenerated > ago(6h)")

# Conditional: Invoke SAP APM Connector skill for Dynatrace/Focus Run deep metrics
# The APM connector connects to the customer's APM tool and returns enriched HANA telemetry
# If not configured, AMS data is sufficient for RCA
```

### Step 7: Correlate and Determine Root Cause
- Find the lowest RED layer → that's the root cause
- Build timeline: event → impact → cascade
- Cite specific data points (timestamps, metric values, log entries)

### Step 8: Output
```python
# Always: Display RCA in chat + send via Teams/Outlook
# Conditional: Invoke SAP ServiceNow Connector skill to create incident with RCA payload
# If ServiceNow not configured, send via Teams connector (existing working path)
```

## Output Format

```
🔴 ROOT CAUSE ANALYSIS — AB1 — May 7, 2026 03:14 UTC

TIMELINE:
  03:12  Azure Platform: VM maintenance event detected (Resource Health)
  03:14  Layer 1 RED: AB1vm rebooted (Activity Log: Compute/Restart)
  03:14  Layer 4 RED: HANA unavailable (AMS: ACTIVE_STATUS = NO)
  03:15  Layer 5 RED: SAP processes GRAY (AMS: dispstatus = GRAY)
  03:18  Layer 4 GREEN: HANA auto-started
  03:19  Layer 5 GREEN: SAP processes GREEN

ROOT CAUSE: Azure planned maintenance caused VM reboot.
IMPACT: 5 minutes SAP downtime (03:14 - 03:19)
RECOMMENDATION: Enable SAP Maintenance Autopilot skill for zero-downtime maintenance.
```
