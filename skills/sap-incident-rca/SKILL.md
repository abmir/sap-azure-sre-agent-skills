---
name: sap-incident-rca
description: "Cross-layer root cause analysis for SAP incidents. Correlates Azure infrastructure, Guest OS, Pacemaker cluster, HANA database, and SAP application evidence to explain why a system is down, slow, or unstable. Conditional ServiceNow and Grafana enrichment when configured."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
    - SearchMemory
    - SearchIncidentKnowledge
    - MCP-MSLearnDocs_microsoft_docs_search
    - MCP-MSLearnDocs_microsoft_docs_fetch
    - GetActivityLogsSummary
    - GetChangeHistory
    - ShowChangeDiffViewer
    - AnalyzeDeploymentFailures
    - QueryLogAnalyticsByWorkspaceId
    - GetMetricTimeSeriesElementsForAzureResource
    - GetArmResourceAsJson
    - RunAzCliReadCommands
    - PlotAreaChartWithCorrelation
    - PlotBarChart
    - PlotScatter
    - CreateScheduledMonitoringTask
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## When to Use

- "Why did SAP go down?" / "Cross-layer RCA for AB1"
- "What happened at 3 AM on HSO?"
- "Analyze the alert that just fired"
- "Give me a root-cause timeline"
- **Auto-triggered** by Azure Monitor alert response plans

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

```python
import requests, json
from datetime import datetime, timedelta, timezone

# SUB_ID: Use subscription_id from Team Onboarding
# AMS_WORKSPACE_ID: Use ams_workspace_id from Team Onboarding

# Conditional integrations — invoked as separate skills when available
# SAP ServiceNow Connector: queries changes, creates incidents (only if SNOW configured)
# SAP APM Connector: pulls Dynatrace/Focus Run HANA telemetry (only if configured)
# When not configured, fall back to Azure Activity Log and AMS data respectively

def get_mi_token(resource):
    resp = requests.get("http://169.254.169.254/metadata/identity/oauth2/token",
        params={"api-version": "2019-08-01", "resource": resource},
        headers={"Metadata": "true"}, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]

def arm_get(path, api_version="2023-09-01"):
    token = get_mi_token("https://management.azure.com/")
    url = f"https://management.azure.com{path}?api-version={api_version}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    resp.raise_for_status()
    return resp.json()

def query_log_analytics(query, timespan_hours=6):
    token = get_mi_token("https://api.loganalytics.io/")
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=timespan_hours)).isoformat()
    end = now.isoformat()
    resp = requests.post(
        f"https://api.loganalytics.io/v1/workspaces/{AMS_WORKSPACE_ID}/query",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "timespan": f"{start}/{end}"}, timeout=120)
    resp.raise_for_status()
    return resp.json()
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

### Step 3: Layer 3 — Pacemaker Cluster (HA systems only)
```
Prometheus_HaClusterExporter_CL
| where TimeGenerated > ago(6h)
| summarize arg_max(TimeGenerated, *) by ha_cluster_pacemaker_nodes_s
| project TimeGenerated, Node=ha_cluster_pacemaker_nodes_s, Status=ha_cluster_pacemaker_nodes_d
```

### Step 4: Layer 4 — HANA Database
```
SapHana_SystemAvailability_CL
| where TimeGenerated > ago(6h)
| summarize arg_max(TimeGenerated, *) by SID_s, HOST_s
| project TimeGenerated, SID_s, HOST_s, ACTIVE_STATUS_s
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
