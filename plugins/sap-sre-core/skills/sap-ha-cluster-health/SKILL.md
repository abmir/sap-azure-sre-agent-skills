---
name: sap-ha-cluster-health
description: "Evaluates Pacemaker cluster state, HSR replication status, and takeover readiness for SAP HA systems. Uses AMS telemetry and optional live VM commands (crm_mon, SAPHanaSR-showAttr, hsr_state) via command proxy. Read-only."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - GetActivityLogsSummary
    - QueryLogAnalyticsByWorkspaceId
    - GetMetricTimeSeriesElementsForAzureResource
    - PlotAreaChartWithCorrelation
    - PlotScatter
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## Infrastructure Requirements

This section covers what **deployed infrastructure** (Storage Account / SRE Proxy) enhances this skill — not to be confused with the operational tier modes (T1 / T3) in the `## Mode Selection` section below. The skill adapts automatically based on what is listed in the `## Deployed Infrastructure` section of Team Onboarding.

- **No infrastructure listed** — Cluster state is inferred only from Internal Load Balancer backend pool health probes + AMS `Prometheus_HaClusterExporter_CL` + Activity Log (fencing = VM deallocate events). Cannot inspect `corosync.conf`, `SAPHanaSR-showAttr` output, SBD status, or SR hooks in `global.ini`. **Always disclose in the report header**: "Cluster diagnosis is approximate (ILB probes + AMS only). Deploy a config store for corosync / SBD / SR-hook visibility, or add the SRE Proxy for live `crm_mon`."
- **Storage Account listed** — Also reads collected `crm-status.txt`, `saphanasr-showattr.txt`, `corosync.conf`, `sbd-config.txt`, and `global.ini` from the `sap-configs` blob container. Full HSR sync state + SOK / SFAIL visibility.
- **SRE Proxy also listed** — Also pulls live `crm_mon`, live `SAPHanaSR-showAttr`, and live HSR state through the command proxy. Best fidelity for "what is happening right now" questions.

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

| Source | Primary/Fallback | Freshness | Used For |
|--------|-----------------|-----------|----------|
| **Command proxy `/batch`** | **PRIMARY** | **Live** | **crm_mon output, SAPHanaSR-showAttr, global.ini HA hooks, cluster properties** |
| AMS: `Prometheus_HaClusterExporter_CL` | Primary | 2-5 min | Live node/resource status, fail-counts |
| AMS: `SapHana_SystemReplication_CL` | Primary | 2-5 min | HSR sync state, mode, lag |
| Blob: `crm-status.txt` | Fallback | Cron (4-6h) | Full Pacemaker config — **only if command proxy fails** |
| Blob: `saphanasr-showattr.txt` | Fallback | Cron (4-6h) | SOK/SFAIL — **only if command proxy fails** |
| Blob: `global.ini` | Fallback | Cron (4-6h) | SR hook registration — **only if command proxy fails** |
| Azure Monitor: VM Availability | Primary | Real-time | Platform events |
| Activity Log | Primary | Real-time | Fencing = VM deallocate events |
| Resource Health | Primary | Real-time | Planned/unplanned events |

## Authentication

**IMPORTANT — Azure API Access:** Do NOT use IMDS tokens (169.254.169.254) or ManagedIdentityCredential — they are not available in the agent sandbox. Instead:
- For Azure Resource Manager queries: Use the built-in `GetArmResourceAsJson` or `RunAzCliReadCommands` tools
- For Log Analytics queries: Use the built-in `QueryLogAnalyticsByWorkspaceId` tool  
- For metrics: Use the built-in `GetMetricTimeSeriesElementsForAzureResource` tool
- For proxy HTTP calls: Use `ExecutePythonCode` with `X-API-Key` header (API key from Team Onboarding)

```python
# Only use ExecutePythonCode for proxy HTTP calls. Use built-in tools for Azure API access.
import requests, json, re
from datetime import datetime, timedelta, timezone

# SUB_ID: Use subscription_id from Team Onboarding
# AMS_WORKSPACE_ID: Use ams_workspace_id from Team Onboarding

# PROXY_URL: Use config_proxy_url from Team Onboarding
# PROXY_KEY: Use config_proxy_api_key from Team Onboarding

# COMMAND_PROXY_URL: Use command_proxy_url from Team Onboarding
# COMMAND_PROXY_KEY: Use command_proxy_api_key from Team Onboarding

# VM commands are executed via the SAP Command Executor skill (never directly)
# When T3 mode needs to remediate, instruct the agent to invoke SAP Command Executor

def get_ha_data_live(vm_name, rg):
    """Fetch HA/DR data from VM via command proxy batch (live, primary source)."""
    commands = [
        {"id": "crm_status", "cmd": "crm_mon -1 --output-as=text 2>/dev/null || crm status"},
        {"id": "saphanasr_showattr", "cmd": "SAPHanaSR-showAttr 2>/dev/null || echo 'N/A'"},
        {"id": "global_ini_hooks", "cmd": "grep -A5 '\\[ha_dr_provider' /hana/shared/*/global/hdb/custom/config/global.ini 2>/dev/null || echo 'N/A'"},
        {"id": "hsr_state", "cmd": "su - $(ls /hana/shared/ | head -1 | tr '[:upper:]' '[:lower:]')adm -c 'python /usr/sap/*/HDB*/exe/python_support/systemReplicationStatus.py' 2>/dev/null || echo 'N/A'"},
        {"id": "crm_config", "cmd": "cibadmin --query --scope crm_config 2>/dev/null | grep -E 'stonith-enabled|stonith-action|stonith-timeout|concurrent-fencing' || echo 'N/A'"},
    ]
    resp = requests.post(f"{COMMAND_PROXY_URL}/batch",
        headers={"x-api-key": COMMAND_PROXY_KEY, "Content-Type": "application/json"},
        json={"vm": vm_name, "rg": rg, "commands": commands}, timeout=180)
    if resp.status_code == 200:
        results = {r["id"]: r["output"] for r in resp.json().get("results", [])}
        return {"source": "live", "data": results, "timestamp": datetime.now(timezone.utc).isoformat()}
    return None

def get_ha_data_blob(sid, hostname):
    """Fallback: fetch HA/DR data from blob config files (stale, 4-6h old)."""
    resp = requests.get(f"{PROXY_URL}/api/configs/{sid}/{hostname}", headers={"x-api-key": PROXY_KEY}, timeout=60)
    if resp.status_code == 200:
        data = resp.json()
        return {"source": "blob", "data": data.get("files", {}), "timestamp": data.get("last_modified", "unknown")}
    return {"source": "none", "data": {}, "timestamp": None}

def get_ha_data(vm_name, rg, sid, hostname):
    """Try live command proxy first, fall back to blob."""
    live = get_ha_data_live(vm_name, rg)
    if live:
        return live
    return get_ha_data_blob(sid, hostname)
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
| ID | Check | Primary Source | Fallback |
|----|-------|---------------|----------|
| HSR-001 | HSR sync state (ACTIVE/ERROR) | AMS `SapHana_SystemReplication_CL` | — |
| HSR-002 | Replication mode (sync/syncmem/async) | AMS | — |
| HSR-003 | SAPHanaSR-showAttr sync_state (SOK/SFAIL) | **Command proxy: `saphanasr_showattr`** | Blob `saphanasr-showattr.txt` |
| HSR-004 | Operation mode (logreplay/delta_datashipping) | **Command proxy: `saphanasr_showattr`** | Blob |
| HSR-005 | SR hook provider in global.ini | **Command proxy: `global_ini_hooks`** | Blob `global.ini` |
| HSR-006 | AUTOMATED_REGISTER = true | **Command proxy: `crm_status`** | Blob `crm-status.txt` |
| HSR-007 | PREFER_SITE_TAKEOVER = true | **Command proxy: `crm_status`** | Blob `crm-status.txt` |
| HSR-008 | Pacemaker SAPHana resource state | AMS | — |
| HSR-009 | VM availability (both nodes) | Azure Monitor | — |
| HSR-010 | Data freshness (last AMS record) | AMS | — |

**Data source tagging**: Always include in output which source was used:
- `Source: ✅ Live` — command proxy batch succeeded
- `Source: ⚠️ Blob (stale, <timestamp>)` — fallback to cron-collected data

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
