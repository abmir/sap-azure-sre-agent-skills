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

**Config reads & proxy fallback**: Stored SAP/OS configs are read **directly from the `sap-configs` blob container using the agent's own Managed Identity** (`--auth-mode login`) — there is **no config proxy**. The MCP command proxy is optional and runs only **live VM commands**; if it is not deployed or errors (timeout, 5xx, unreachable), continue with stored blob configs + Azure-native sources (AMS, ARM API, Azure Monitor). Never block the skill on the proxy.

## Infrastructure Requirements

This section covers what **deployed infrastructure** (Storage Account / MCP command proxy) enhances this skill — not to be confused with the operational tier modes (T1 / T3) in the `## Mode Selection` section below. The skill adapts automatically based on what is listed in the `## Deployed Infrastructure` section of Team Onboarding.

- **No infrastructure listed** — Cluster state is inferred only from Internal Load Balancer backend pool health probes + AMS `Prometheus_HaClusterExporter_CL` + Activity Log (fencing = VM deallocate events). Cannot inspect `corosync.conf`, `SAPHanaSR-showAttr` output, SBD status, or SR hooks in `global.ini`. **Always disclose in the report header**: "Cluster diagnosis is approximate (ILB probes + AMS only). Deploy a config store for corosync / SBD / SR-hook visibility, or add the MCP command proxy for live `crm_mon`."
- **Storage Account listed** — Also reads collected `crm-status.txt`, `saphanasr-showattr.txt`, `corosync.conf`, `sbd-config.txt`, and `global.ini` from the `sap-configs` blob container. Full HSR sync state + SOK / SFAIL visibility.
- **MCP command proxy also listed** — Also pulls live `crm_mon`, live `SAPHanaSR-showAttr`, and live HSR state through the command proxy. Best fidelity for "what is happening right now" questions.

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

**Cluster-bearing systems only** — apply to any system whose `deployment` in the landscape inventory is **`high-availability`** or **`disaster-recovery`** (these have Pacemaker + HSR). **Skip** systems whose `deployment` is `standalone` or `distributed` (no cluster). Works for both `scale-up` and `scale-out` architectures; for `scale-out`, also check standby-node readiness and that every worker node is present in the cluster. If asked about a non-cluster system, reply that cluster health doesn't apply to a `<type>` system rather than erroring.

## Data Sources

| Source | Primary/Fallback | Freshness | Used For |
|--------|-----------------|-----------|----------|
| **MCP command proxy (run_batch)** | **Primary when configured** | **Live** | **crm_mon output, SAPHanaSR-showAttr, global.ini HA hooks, cluster properties** |
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
- For live VM commands: invoke the **SAP Command Runner** skill (the `sap-sre-proxy` MCP connector) — never make direct HTTP or proxy calls

```python
# Use the built-in tools above for Azure API access.
import json, re
from datetime import datetime, timedelta, timezone

# SUB_ID / AMS_WORKSPACE_ID: from Team Onboarding (AMS only if it is listed)

# Live cluster/HSR data comes ONLY from the MCP command proxy, invoked via the SAP Command
# Runner skill (run_batch). Never call the proxy directly. When the proxy is not configured,
# fall back to blob configs + AMS (see the Data Sources table). When T3 mode needs to
# remediate, invoke the SAP Command Runner skill.

# Allowlisted read-only commands to request via SAP Command Runner (run_batch):
HA_COMMANDS = [
    {"id": "crm_status", "cmd": "crm_mon -1 --output-as=text 2>/dev/null || crm status"},
    {"id": "saphanasr_showattr", "cmd": "SAPHanaSR-showAttr 2>/dev/null || echo 'N/A'"},
    {"id": "global_ini_hooks", "cmd": "grep -A5 '\\[ha_dr_provider' /hana/shared/*/global/hdb/custom/config/global.ini 2>/dev/null || echo 'N/A'"},
    {"id": "hsr_state", "cmd": "su - $(ls /hana/shared/ | head -1 | tr '[:upper:]' '[:lower:]')adm -c 'python /usr/sap/*/HDB*/exe/python_support/systemReplicationStatus.py' 2>/dev/null || echo 'N/A'"},
    {"id": "crm_config", "cmd": "cibadmin --query --scope crm_config 2>/dev/null | grep -E 'stonith-enabled|stonith-action|stonith-timeout|concurrent-fencing' || echo 'N/A'"},
]

# Stored fallback configs (crm-status.txt, saphanasr-showattr.txt, corosync.conf, global.ini)
# are read directly from the sap-configs blob with the agent MI:
#   az storage blob download --auth-mode login --account-name <sa> --container-name sap-configs \
#     --name <SID>/<host>/latest/cluster/<file>
```

## T1 Mode: Cluster + HSR Status

> **Run `getschema` FIRST (mandatory).** Before the queries below, run `Prometheus_HaClusterExporter_CL | getschema` and `SapHana_SystemReplication_CL | getschema`. The `ha_cluster_*` and HSR column names/suffixes vary by AMS collector version — the KQL below is a **template**; bind it to the real column names from getschema, never assume them from memory or another table.

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
| HSR-003 | SAPHanaSR-showAttr sync_state (SOK/SFAIL) | **MCP proxy: `saphanasr_showattr`** | Blob `saphanasr-showattr.txt` |
| HSR-004 | Operation mode (logreplay/delta_datashipping) | **MCP proxy: `saphanasr_showattr`** | Blob |
| HSR-005 | SR hook provider in global.ini | **MCP proxy: `global_ini_hooks`** | Blob `global.ini` |
| HSR-006 | AUTOMATED_REGISTER = true | **MCP proxy: `crm_status`** | Blob `crm-status.txt` |
| HSR-007 | PREFER_SITE_TAKEOVER = true | **MCP proxy: `crm_status`** | Blob `crm-status.txt` |
| HSR-008 | Pacemaker SAPHana resource state | AMS | — |
| HSR-009 | VM availability (both nodes) | Azure Monitor | — |
| HSR-010 | Data freshness (last AMS record) | AMS | — |

**Data source tagging**: Always include in output which source was used:
- `Source: ✅ Live` — MCP command proxy (run_batch) succeeded
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
5. **Execute**: On approval, invoke the **SAP Command Runner** skill:
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
