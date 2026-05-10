---
name: sap-ha-dr-guardian
description: "Unified HA and DR health for SAP. Evaluates Pacemaker cluster state, HSR replication status, takeover readiness, and failover forensics. In T1 mode: show status. In T3 mode: on SFAIL/quorum loss, alert and recommend remediation with approval gate."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
    - SearchMemory
    - SearchIncidentKnowledge
    - MCP-MSLearnDocs_microsoft_docs_search
    - MCP-MSLearnDocs_microsoft_docs_fetch
    - GetActivityLogsSummary
    - QueryLogAnalyticsByWorkspaceId
    - GetMetricTimeSeriesElementsForAzureResource
    - GetDimensionNames
    - ListAvailableMetrics
    - CheckTcpConnectivity
    - GetArmResourceAsJson
    - RunAzCliReadCommands
    - PlotAreaChartWithCorrelation
    - PlotScatter
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## Mode Selection

- **User asks** "Show cluster status for HSO" → **T1 mode** (read-only, display status)
- **User asks** "Why did HSO fail over?" → **T1 forensic mode** (read-only, timeline reconstruction)
- **Alert fires** "Pacemaker node offline" or **scheduled** (every 15 min) → **T3 mode** (diagnose, recommend, await approval)
- **SFAIL detected even in T1** → **auto-escalate to T3** (immediate alert)

## When to Use

- "Show Pacemaker status for HSO" / "Cluster status"
- "Is HSR in sync?" / "Replication lag?"
- "Why did HSO fail over?" / "Fencing event investigation"
- "Takeover readiness?" / "Is HA working?"
- Auto-triggered by alerts on cluster/HSR state

## Scope

**HA systems only**: HSO (scaleout-ha). Skip AB1/AB3 (no HA).

## Data Sources

| Source | Freshness | Used For |
|--------|-----------|----------|
| AMS: `Prometheus_HaClusterExporter_CL` | 2-5 min | Live node/resource status, fail-counts |
| AMS: `SapHana_SystemReplication_CL` | 2-5 min | HSR sync state, mode, lag |
| Blob: `crm-status.txt` | Cron (4-6h) | Full Pacemaker config (crm_mon output) |
| Blob: `saphanasr-showattr.txt` | Cron (4-6h) | SOK/SFAIL, operation mode, site attributes |
| Blob: `global.ini` | Cron (4-6h) | SR hook provider registration |
| Azure Monitor: VM Availability | Real-time | Platform events |
| Activity Log | Real-time | Fencing = VM deallocate events |
| Resource Health | Real-time | Planned/unplanned events |

## Authentication

```python
import requests, json, re
from datetime import datetime, timedelta, timezone

# SUB_ID: Use subscription_id from Team Onboarding
# AMS_WORKSPACE_ID: Use ams_workspace_id from Team Onboarding

# PROXY_URL: Use config_proxy_url from Team Onboarding
# PROXY_KEY: Use config_proxy_api_key from Team Onboarding

# VM commands are executed via the SAP Command Executor skill (never directly)
# When T3 mode needs to remediate, instruct the agent to invoke SAP Command Executor

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

def get_vm_configs(sid, hostname):
    resp = requests.get(f"{PROXY_URL}/configs/{sid}/{hostname}", headers={"x-api-key": PROXY_KEY}, timeout=60)
    return resp.json().get("files", {}) if resp.status_code == 200 else {}
```

## T1 Mode: Cluster + HSR Status

### Pacemaker Checks
```
Prometheus_HaClusterExporter_CL
| where TimeGenerated > ago(15m)
| summarize arg_max(TimeGenerated, *) by ha_cluster_pacemaker_nodes_s
| project Node=ha_cluster_pacemaker_nodes_s, Online=ha_cluster_pacemaker_nodes_d, Maintenance=ha_cluster_pacemaker_nodes_maintenance_d
```

### HSR Checks (10 total)
| ID | Check | Source |
|----|-------|--------|
| HSR-001 | HSR sync state (ACTIVE/ERROR) | AMS `SapHana_SystemReplication_CL` |
| HSR-002 | Replication mode (sync/syncmem/async) | AMS |
| HSR-003 | SAPHanaSR-showAttr sync_state (SOK/SFAIL) | Blob `saphanasr-showattr.txt` |
| HSR-004 | Operation mode (logreplay/delta_datashipping) | Blob |
| HSR-005 | SR hook provider in global.ini | Blob `global.ini` |
| HSR-006 | AUTOMATED_REGISTER = true | Blob `crm-status.txt` |
| HSR-007 | PREFER_SITE_TAKEOVER = true | Blob `crm-status.txt` |
| HSR-008 | Pacemaker SAPHana resource state | AMS |
| HSR-009 | VM availability (both nodes) | Azure Monitor |
| HSR-010 | Data freshness (last AMS record) | AMS |

## T1 Forensic Mode: Failure Timeline

1. Query Resource Health for platform events in time window
2. Query Activity Log for VM deallocate/restart (= STONITH fencing)
3. Query AMS for state transitions (node online→offline, resource Master→Slave)
4. Query VM Availability metric for dips
5. Check blob crm-status.txt for failed actions
6. Build root cause timeline

## T3 Mode: Remediation with Approval

When SFAIL, quorum loss, or increasing fail-counts detected:

1. **Diagnose**: Gather full cluster + HSR state
2. **Classify severity**:
   - SFAIL with both nodes online → HIGH (takeover won't work)
   - Node offline, resources running on surviving node → MEDIUM (degraded but functional)
   - Quorum lost → CRITICAL (cluster may fence unpredictably)
3. **Recommend** specific action:
   - SFAIL → "Re-register secondary: `hdbnsutil -sr_register --remoteHost=...`"
   - Failed resource → "Cleanup: `crm resource cleanup <rsc>`"
   - Node in standby → "Bring online: `crm node online <node>`"
4. **Approval**: Send Teams card with diagnosis + recommended action
5. **Execute**: On approval, invoke the **SAP Command Executor** skill:
   - For failed resource cleanup: command_id=`crm_cleanup`, vm=<node>, rg=<rg>
   - For node standby: command_id=`crm_maintenance_on`, vm=<node>, rg=<rg>
   - For node online: command_id=`crm_maintenance_off`, vm=<node>, rg=<rg>
   
   Do NOT call the command proxy directly from this skill.
6. **Validate**: Re-check cluster + HSR state after action

## Output Format

T1 mode:
```
HSO — HA & DR Status: 🟢 HEALTHY

Pacemaker:
  Nodes: vm01 ✅ online, vm02 ✅ online
  Resources: SAPHana (Master on vm01), SAPHanaTopology (Started on both)
  Fail-counts: 0
  Maintenance: OFF

HSR:
  Replication: ✅ ACTIVE (sync mode, logreplay)
  SAPHanaSR: SOK (both sites)
  SR Hook: SAPHanaSR registered in global.ini
  AUTOMATED_REGISTER: true
  PREFER_SITE_TAKEOVER: true

Takeover Readiness: ✅ READY
```

T3 mode:
```
🔴 HSO — HA ALERT: HSR SFAIL detected

  SAPHanaSR sync_state: SFAIL (secondary not in sync)
  Both nodes online — but takeover WILL FAIL if primary crashes.

  RECOMMENDED ACTION: Re-register secondary
    Command: hdbnsutil -sr_register --remoteHost=vm01 ...
    Risk: LOW (secondary only, no primary impact)

  [Approve] [Deny] [Show Details]
```
