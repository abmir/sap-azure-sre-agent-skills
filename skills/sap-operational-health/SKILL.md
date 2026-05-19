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

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## When to Use

- "Is everything healthy?" / "Show SAP health"
- "Health check for AB1" / "Status of all systems"
- "Any CPU or memory pressure on AB3 VMs?"
- "Are there network issues between DB and App servers?"
- "Check if AB1 VMs have accelerated networking"

## Data Sources

| Source | Primary/Fallback | Freshness | What It Provides |
|--------|-----------------|-----------|-----------------|
| Azure Monitor Metrics | Primary | Real-time (1 min) | VM CPU, memory, disk IOPS/latency, network |
| Azure Resource Health | Primary | Real-time | Platform events (planned/unplanned maintenance) |
| Azure VM Power State | Primary | Real-time | Running/stopped/deallocated |
| AMS Log Analytics | Primary | 2-5 min | HANA availability, OS metrics, Pacemaker state, SAP processes |
| Activity Log | Primary | Real-time | Recent changes |
| **Command proxy `/batch`** | **Primary** | **Live** | **ethtool output, PPG validation, accelerated networking, VM configs** |
| Config Proxy (blob) | Fallback | Cron (4-6h) | ethtool output, VM configs — **only if command proxy fails** |

## Authentication

**IMPORTANT — Azure API Access:** Do NOT use IMDS tokens (169.254.169.254) or ManagedIdentityCredential — they are not available in the agent sandbox. Instead:
- For Azure Resource Manager queries: Use the built-in `GetArmResourceAsJson` or `RunAzCliReadCommands` tools
- For Log Analytics queries: Use the built-in `QueryLogAnalyticsByWorkspaceId` tool  
- For metrics: Use the built-in `GetMetricTimeSeriesElementsForAzureResource` tool
- For proxy HTTP calls: Use `ExecutePythonCode` with `X-API-Key` header (API key from Team Onboarding)

```python
# Only use ExecutePythonCode for proxy HTTP calls. Use built-in tools for Azure API access.
import requests, json
from datetime import datetime, timedelta, timezone

# SUB_ID: Use subscription_id from Team Onboarding
# AMS_WORKSPACE_ID: Use ams_workspace_id from Team Onboarding

# PROXY_URL: Use config_proxy_url from Team Onboarding
# PROXY_KEY: Use config_proxy_api_key from Team Onboarding

# COMMAND_PROXY_URL: Use command_proxy_url from Team Onboarding
# COMMAND_PROXY_KEY: Use command_proxy_api_key from Team Onboarding

def get_landscape_registry():
    resp = requests.get(f"{PROXY_URL}/registry", headers={"x-api-key": PROXY_KEY}, timeout=30)
    return resp.json() if resp.status_code == 200 else None

def get_vm_data_live(vm_name, rg, commands):
    """Fetch VM data via command proxy batch (live, primary source)."""
    resp = requests.post(f"{COMMAND_PROXY_URL}/batch",
        headers={"x-api-key": COMMAND_PROXY_KEY, "Content-Type": "application/json"},
        json={"vm": vm_name, "rg": rg, "commands": commands}, timeout=180)
    if resp.status_code == 200:
        results = {r["id"]: r["output"] for r in resp.json().get("results", [])}
        return {"source": "live", "data": results, "timestamp": datetime.now(timezone.utc).isoformat()}
    return None

def get_vm_configs_fallback(sid, hostname):
    """Fallback: fetch VM config data from blob (stale, 4-6h old)."""
    resp = requests.get(f"{PROXY_URL}/configs/{sid}/{hostname}", headers={"x-api-key": PROXY_KEY}, timeout=60)
    if resp.status_code == 200:
        data = resp.json()
        return {"source": "blob", "data": data.get("files", {}), "timestamp": data.get("last_modified", "unknown")}
    return {"source": "none", "data": {}, "timestamp": None}

def get_vm_data(vm_name, rg, sid, hostname, commands):
    """Try live command proxy first, fall back to blob."""
    live = get_vm_data_live(vm_name, rg, commands)
    if live:
        return live
    return get_vm_configs_fallback(sid, hostname)
```

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
