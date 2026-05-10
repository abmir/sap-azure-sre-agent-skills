---
name: sap-operational-health
description: "Unified health dashboard for SAP systems. Checks AMS provider health, data freshness, VM power state, CPU/memory/disk metrics, accelerated networking, proximity placement, Resource Health, and alert coverage. Traffic-light output per layer."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
    - SearchMemory
    - SearchIncidentKnowledge
    - MCP-MSLearnDocs_microsoft_docs_search
    - MCP-MSLearnDocs_microsoft_docs_fetch
    - QueryLogAnalyticsByWorkspaceId
    - QueryLogAnalyticsByResourceId
    - ListAvailableMetrics
    - GetMetricTimeSeriesElementsForAzureResource
    - GetDimensionNames
    - ValidateQuery
    - CheckTcpConnectivity
    - CheckIfResourceExists
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - GetTlsSettings
    - PlotAreaChartWithCorrelation
    - PlotBarChart
    - PlotHeatmap
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## When to Use

- "Is everything healthy?" / "Show SAP health"
- "Health check for AB1" / "Status of all systems"
- "Any CPU or memory pressure on AB3 VMs?"
- "Are there network issues between DB and App servers?"
- "Check if AB1 VMs have accelerated networking"

## Data Sources

| Source | Freshness | What It Provides |
|--------|-----------|-----------------|
| Azure Monitor Metrics | Real-time (1 min) | VM CPU, memory, disk IOPS/latency, network |
| Azure Resource Health | Real-time | Platform events (planned/unplanned maintenance) |
| Azure VM Power State | Real-time | Running/stopped/deallocated |
| AMS Log Analytics | 2-5 min | HANA availability, OS metrics, Pacemaker state, SAP processes |
| Activity Log | Real-time | Recent changes |
| Config Proxy | Cron (4-6h) | ethtool output, VM configs |

## Authentication

```python
import requests, json
from datetime import datetime, timedelta, timezone

# SUB_ID: Use subscription_id from Team Onboarding
# AMS_WORKSPACE_ID: Use ams_workspace_id from Team Onboarding

# PROXY_URL: Use config_proxy_url from Team Onboarding
# PROXY_KEY: Use config_proxy_api_key from Team Onboarding

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

def get_metrics(resource_id, metric_names, timespan_hours=4, interval="PT1H"):
    token = get_mi_token("https://management.azure.com/")
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=timespan_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"https://management.azure.com{resource_id}/providers/Microsoft.Insights/metrics"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"},
        params={"api-version": "2023-10-01", "metricnames": metric_names,
                "timespan": f"{start}/{end}", "interval": interval, "aggregation": "Average,Maximum"},
        timeout=60)
    resp.raise_for_status()
    return resp.json()

def query_log_analytics(query, timespan_hours=4):
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

def get_landscape_registry():
    resp = requests.get(f"{PROXY_URL}/registry", headers={"x-api-key": PROXY_KEY}, timeout=30)
    return resp.json() if resp.status_code == 200 else None

def get_vm_configs(sid, hostname):
    resp = requests.get(f"{PROXY_URL}/configs/{sid}/{hostname}", headers={"x-api-key": PROXY_KEY}, timeout=60)
    return resp.json().get("files", {}) if resp.status_code == 200 else {}
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
```
Prometheus_OSExporter_CL
| where TimeGenerated > ago(15m)
| summarize latest_cpu=max(node_cpu_seconds_total_d), latest_mem=max(node_memory_MemTotal_bytes_d - node_memory_MemAvailable_bytes_d) by Computer
```

### Layer 3: Pacemaker Cluster (AMS — HA systems only)
```
Prometheus_HaClusterExporter_CL
| where TimeGenerated > ago(15m)
| summarize nodes_online=dcountif(ha_cluster_pacemaker_nodes_d, ha_cluster_pacemaker_nodes_d == 1) by ClusterName_s
```

### Layer 4: HANA Database (AMS)
```
SapHana_SystemAvailability_CL
| where TimeGenerated > ago(15m)
| summarize arg_max(TimeGenerated, *) by SID_s, HOST_s
| project SID_s, HOST_s, ACTIVE_STATUS_s, DATABASE_NAME_s
```

### Layer 5: SAP Application (AMS — if available)
```
SapNetweaver_GetProcessList_CL
| where TimeGenerated > ago(15m)
| summarize arg_max(TimeGenerated, *) by SID_s, instanceNr_s, name_s
| project SID_s, instanceNr_s, name_s, dispstatus_s
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
```
