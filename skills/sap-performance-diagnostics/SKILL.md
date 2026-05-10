---
name: sap-performance-diagnostics
description: "Diagnoses SAP system performance issues across HANA database, SAP application, and Azure storage layers. Covers memory pressure, blocking transactions, long-running SQL, work process utilization, dialog response time, disk IOPS/MBPS throttling, Write Accelerator, and HANA savepoint duration."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
    - SearchMemory
    - SearchIncidentKnowledge
    - MCP-MSLearnDocs_microsoft_docs_search
    - MCP-MSLearnDocs_microsoft_docs_fetch
    - GetMetricTimeSeriesElementsForAzureResource
    - ListAvailableMetrics
    - GetDimensionNames
    - QueryLogAnalyticsByWorkspaceId
    - QueryLogAnalyticsByResourceId
    - ValidateQuery
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - PlotScatter
    - PlotAreaChartWithCorrelation
    - PlotBarChart
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## When to Use

- "Why is SAP slow on AB1?"
- "HANA performance analysis for AB3"
- "Is storage causing the slowdown?"
- "Which HANA service is consuming the most memory?"
- "Are there blocking transactions?"
- "Disk IOPS throttling on AB1?"
- "Check dialog response time"
- "HANA savepoint duration?"

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
| STR-007 | Stripe config (lsblk) | Config proxy | /hana/data=256k, /hana/log=64k |
| STR-008 | fstab mount options | Config proxy | nofail, nobarrier for data |
| STR-009 | ANF volume throughput | Azure Monitor (ANF) | provisioned vs consumed |
| STR-010 | Data freshness | Config proxy last-modified | <24h GREEN, >48h RED |

## Query Optimization

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
