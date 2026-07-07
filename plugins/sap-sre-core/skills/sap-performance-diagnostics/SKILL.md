---
name: sap-performance-diagnostics
description: "Diagnoses SAP system performance issues across HANA database, SAP application, and Azure storage layers. Covers memory pressure, disk IOPS/MBPS throttling, Write Accelerator, and HANA savepoint duration. Uses AMS telemetry; enriched with live VM data when command proxy is available."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - QueryLogAnalyticsByWorkspaceId
    - GetMetricTimeSeriesElementsForAzureResource
    - PlotAreaChartWithCorrelation
    - PlotBarChart
    - PlotScatter
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Config reads & proxy fallback**: Stored SAP/OS configs are read **directly from the `sap-configs` blob container using the agent's own Managed Identity** (`--auth-mode login`) — there is **no config proxy**. The MCP command proxy is optional and runs only **live VM commands**; if it is not deployed or errors (timeout, 5xx, unreachable), continue with stored blob configs + Azure-native sources (AMS, ARM API, Azure Monitor). Never block the skill on the proxy.

**Telemetry source adaptation**: HANA/OS/SAP-app depth depends on which telemetry source is listed in `## Deployed Infrastructure`. Adapt automatically — never hard-fail because a specific source is absent:
- **AMS (Azure Monitor for SAP) listed** → use the AMS KQL tables below (deepest HANA/OS signals).
- **No AMS, but an SAP APM connector is listed (SAP Cloud ALM / Focus Run / Dynatrace)** → invoke the SAP APM Connector for HANA/SAP-app signals; use Azure Monitor platform metrics for VM/storage.
- **No SAP telemetry source at all** → use Azure platform metrics only (Azure Monitor disk IOPS/latency, VM CPU/memory) and disclose in the header: "HANA/SAP-application telemetry is not configured; results use Azure platform metrics only. Add AMS or an APM connector for HANA depth."

## Infrastructure Requirements

This skill adapts automatically based on what infrastructure is listed in the `## Deployed Infrastructure` section of Team Onboarding.

- **No infrastructure listed** — Diagnosis is limited to AMS telemetry + Azure Monitor metrics (CPU/memory/disk IOPS, AMS HANA service stats, AMS savepoint duration). No HANA config inspection, no live SQL. Can identify "HANA service X consuming most memory" from AMS but cannot inspect `global.ini` thresholds or run `M_EXPENSIVE_STATEMENTS`. **Always disclose in the report header**: "HANA config and live SQL evidence are unavailable (no Storage Account or MCP command proxy in Deployed Infrastructure)."
- **Storage Account listed** — Also reads `global.ini`, `indexserver.ini`, `sysctl`, and other collected HANA / OS configs from the `sap-configs` blob container. Can show "your `statement_memory_limit` is set to X" alongside "the offending statement allocated Y".
- **MCP command proxy also listed** — Also runs live `hdbsql` against the system (`M_LOAD_HISTORY_HOST`, `M_EXPENSIVE_STATEMENTS`, `M_HEAP_MEMORY`) through the command proxy for the freshest evidence.

## When to Use

- "Why is SAP slow on AB1?"
- "HANA performance analysis for AB3"
- "Is storage causing the slowdown?"
- "Which HANA service is consuming the most memory?"
- "Are there blocking transactions?"
- "Disk IOPS throttling on AB1?"
- "Check dialog response time"
- "HANA savepoint duration?"

## Topology handling (all 8 system types)

Read the system's `architecture` + `deployment` from the inventory and adapt:
- **scale-out** → group HANA/OS/storage KQL by `HOST_s` and diagnose each DB node; watch for a single overloaded worker or partition skew rather than a per-SID average.
- **standalone / distributed** → skip cluster-specific analysis (no Pacemaker) — never error.
- **high-availability** → sync HSR is expected; treat sync-lag against the HA threshold.
- **disaster-recovery** → distinguish expected **async** DR-replica lag from a real problem — compare against the async baseline, not the sync HA threshold.

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

**Getting VM/HANA data (adaptive — no dead endpoints):**
- **Stored HANA/OS configs** (`global.ini`, `indexserver.ini`, `sysctl`, stripe/fstab) — read **directly** from the `sap-configs` blob with the agent's own MI: `RunAzCliReadCommands` → `az storage blob download --auth-mode login --account-name <sa> --container-name sap-configs --name <SID>/<host>/latest/...`. Available whenever a **Storage Account** is listed.
- **Live HANA SQL / OS state** (`M_EXPENSIVE_STATEMENTS`, `M_HEAP_MEMORY`, `lsblk`) — only when the **MCP command proxy** is configured: invoke the **SAP Command Runner** skill (`run_batch`) with the performance commands below. If it is not configured or errors, use the stored blob configs. Never block on it.
- **Landscape inventory** — read from the agent Knowledge Base or the `sap-configs` blob directly. There is **no** `/api/registry` endpoint.

**Standard commands for performance checks**:
```python
PERF_COMMANDS = [
    {"id": "df_hana", "cmd": "df -h /hana/data /hana/log /hana/shared 2>/dev/null || df -h /"},
    {"id": "free_mem", "cmd": "free -m"},
    {"id": "lsblk_stripe", "cmd": "lsblk -o NAME,TYPE,SIZE,MOUNTPOINT,SCHED 2>/dev/null | head -30"},
    {"id": "fstab", "cmd": "cat /etc/fstab | grep -v '^#' | grep -E 'hana|swap'"},
    {"id": "global_ini", "cmd": "cat /hana/shared/*/global/hdb/custom/config/global.ini 2>/dev/null | head -50"},
]
```

## Check Catalog

### HANA Performance (10 checks)
| ID | Check | Source | Threshold |
|----|-------|--------|-----------|
| PERF-H01 | Host CPU utilization | `SapHana_LoadHistory_CL` | >80% AMBER, >95% RED |
| PERF-H02 | Host memory utilization | `SapHana_LoadHistory_CL` | >80% AMBER, >95% RED |
| PERF-H03 | Service memory consumption | `SapHana_LoadHistory_CL` | per-service breakdown |
| PERF-H04 | SQL probe response time | `SapHana_SqlProbe_CL` | >100ms AMBER, >500ms RED |
| PERF-H05 | HANA availability | `SapHana_SystemAvailability_CL` | ACTIVE=YES GREEN |
| PERF-H06 | HANA alerts (active) | `SapHana_Alerts_CL` | any HIGH/ERROR alerts |
| PERF-H07 | Disk fragmentation | `SapHana_LoadHistory_CL` | data volume fragmentation % |
| PERF-H08 | Delta merge duration | `SapHana_LoadHistory_CL` | >60s AMBER |
| PERF-H09 | Uncommitted transactions | `SapHana_Mvcc_CL` | increasing trend |
| PERF-H10 | MVCC version count | `SapHana_Mvcc_CL` | >1M AMBER, >10M RED |

### SAP Application (3 checks)
| ID | Check | Source | Threshold |
|----|-------|--------|-----------|
| PERF-A01 | Process status | `SapNetweaver_GetProcessList_CL` | GREEN/YELLOW/GRAY |
| PERF-A02 | Work process utilization | `SapNetweaver_GetProcessList_CL` | >80% busy AMBER |
| PERF-A03 | Dialog response time | `SapNetweaver_GetProcessList_CL` | >1s AMBER, >3s RED |

### Storage Performance (10 checks)
| ID | Check | Source | Threshold |
|----|-------|--------|-----------|
| STR-001 | Data disk IOPS consumed % | Azure Monitor | >70% AMBER, >90% RED |
| STR-002 | Data disk MBPS consumed % | Azure Monitor | >70% AMBER, >90% RED |
| STR-003 | OS disk IOPS consumed % | Azure Monitor | >70% AMBER, >90% RED |
| STR-004 | Disk type + size (/hana/data, /hana/log) | ARM API | Premium SSD/Ultra/ANF |
| STR-005 | Write Accelerator enabled | ARM API (disk caching) | Enabled for /hana/log |
| STR-006 | HANA IO savepoint duration | `SapHana_IO_Savepoint_CL` | >300s AMBER, >600s RED |
| STR-007 | Stripe config (lsblk) | **MCP proxy (run_batch)** (fallback: blob) | /hana/data=256k, /hana/log=64k |
| STR-008 | fstab mount options | **MCP proxy (run_batch)** (fallback: blob) | nofail, nobarrier for data |
| STR-009 | ANF volume throughput | Azure Monitor (ANF) | provisioned vs consumed |
| STR-010 | Data freshness | MCP proxy timestamp or blob last-modified | <24h GREEN, >48h RED |

## Query Optimization

> **Run `getschema` FIRST (mandatory) on every `SapHana_*_CL` table you use** (`SapHana_LoadHistory_CL`, `SapHana_SqlProbe_CL`, `SapHana_SystemAvailability_CL`, `SapHana_Alerts_CL`, `SapHana_Mvcc_CL`, `SapHana_IO_Savepoint_CL`). **The column names in the templates below are illustrative and differ by AMS collector version** — e.g. host CPU/memory may be `CPU_d` / `MEMORY_RESIDENT_d` (not `host_cpu_d` / `host_memory_resident_d`), and SQL latency may be `LATENCY_MS_d` (not `latency_d`). Bind every field to the real name returned by getschema before running the analytic query; never carry column names over from memory or another table.

**Batch HANA checks into a single KQL query** to minimize AAU consumption and latency. Instead of running separate queries for CPU, memory, SQL probe, availability, and alerts, combine them:

```kql
// Single query covering PERF-H01 through PERF-H06
let cpu_mem = SapHana_LoadHistory_CL | where TimeGenerated > ago(4h) | summarize avg_cpu=avg(host_cpu_d), avg_mem=avg(host_memory_resident_d), max_cpu=max(host_cpu_d), max_mem=max(host_memory_resident_d) by HOST_s;
let sql_probe = SapHana_SqlProbe_CL | where TimeGenerated > ago(4h) | summarize avg_latency=avg(latency_d), max_latency=max(latency_d) by HOST_s;
let availability = SapHana_SystemAvailability_CL | where TimeGenerated > ago(4h) | summarize arg_max(TimeGenerated, *) by HOST_s;
let alerts = SapHana_Alerts_CL | where TimeGenerated > ago(4h) and alert_rating_s in ("HIGH", "ERROR") | summarize alert_count=count() by HOST_s;
cpu_mem | join kind=leftouter sql_probe on HOST_s | join kind=leftouter availability on HOST_s | join kind=leftouter alerts on HOST_s
```

Run HANA checks (H01-H06) as one query, MVCC checks (H09-H10) as a second, and Storage checks via Azure Monitor metrics API. This reduces 3-5 KQL queries to 2.

## Output Format

```
AB1 — Performance Analysis

HANA:    8/10 GREEN, 1 AMBER (SQL probe 120ms), 1 RED (MVCC 12M versions)
SAP App: 2/3 GREEN, 1 AMBER (WP utilization 82%)
Storage: 9/10 GREEN, 1 AMBER (data disk IOPS 78%)

TOP FINDING: MVCC version count at 12M — indicates long-running transaction
  holding old row versions. Check M_TRANSACTIONS for oldest active transaction.
  RECOMMENDATION: Identify and terminate the blocking transaction.
```
