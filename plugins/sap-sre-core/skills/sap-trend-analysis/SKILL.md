---
name: sap-trend-analysis
description: "Analyzes trends in HANA memory, disk utilization, CPU, and replication lag using AMS telemetry. Projects resource exhaustion timelines via linear regression. No proxy required — uses AMS and Azure Monitor data."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - QueryLogAnalyticsByWorkspaceId
    - GetMetricTimeSeriesElementsForAzureResource
    - CreateScheduledMonitoringTask
    - PlotAreaChartWithCorrelation
    - PlotScatter
    - PlotBarChart
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Config reads & proxy fallback**: Stored SAP/OS configs are read **directly from the `sap-configs` blob container using the agent's own Managed Identity** (`--auth-mode login`) — there is **no config proxy**. The SRE Proxy is optional and runs only **live VM commands**; if it is not deployed or errors (timeout, 5xx, unreachable), continue with stored blob configs + Azure-native sources (AMS, ARM API, Azure Monitor). Never block the skill on the proxy.

## Tier: T3 — Predictive Prevention (Semi-Autonomous)

## Mode Selection

- **User asks** "Analyze memory trends for AB1" → On-demand analysis
- **Scheduled** (every 6 hours via CreateScheduledMonitoringTask) → Continuous monitoring
- **Threshold crossed** → Alert via Teams + recommend action + await approval

## When to Use

- "Is HANA running out of memory?"
- "Predict when AB1 will hit disk capacity"
- "Memory trend analysis for AB3"
- "Any anomalies on HSO?"
- "When will /hana/log fill up?"

## Authentication

**IMPORTANT — Azure API Access:** Do NOT use IMDS tokens (169.254.169.254) or ManagedIdentityCredential — they are not available in the agent sandbox. Instead:
- For Azure Resource Manager queries: Use the built-in `GetArmResourceAsJson` or `RunAzCliReadCommands` tools
- For Log Analytics queries: Use the built-in `QueryLogAnalyticsByWorkspaceId` tool  
- For metrics: Use the built-in `GetMetricTimeSeriesElementsForAzureResource` tool
- For proxy HTTP calls: Use `ExecutePythonCode` with `X-API-Key` header (API key from Team Onboarding)

```python
# Only use ExecutePythonCode for proxy HTTP calls. Use built-in tools for Azure API access.
import requests, json, math
from datetime import datetime, timedelta, timezone

# SUB_ID: Use subscription_id from Team Onboarding
# AMS_WORKSPACE_ID: Use ams_workspace_id from Team Onboarding

# PROXY_URL / PROXY_KEY: OPTIONAL — only when the SRE Proxy is deployed (live VM commands).
#   Use proxy_url and proxy_api_key from Team Onboarding. Do NOT read stored configs here —
#   read them directly from the sap-configs blob (`az storage blob ... --auth-mode login`).

# COMMAND_PROXY_URL: Use command_proxy_url from Team Onboarding
# COMMAND_PROXY_KEY: Use command_proxy_api_key from Team Onboarding

# VM commands are executed via the SAP Command Executor skill (never directly)
# When T3 mode needs to act on a recommendation, invoke SAP Command Executor
```

## Forecasting Metrics

| Metric | Primary Source | Fallback Source | Query Window | Alert Threshold |
|--------|---------------|-----------------|-------------|-----------------|
| HANA memory utilization | `SapHana_LoadHistory_CL` | — | 7 days | Projected OOM within 72h |
| /hana/log utilization | **Command proxy batch: `df -h`** | Azure Monitor disk % or blob `df` | 7 days | Projected full within 48h |
| /hana/data utilization | Azure Monitor disk % | — | 7 days | Projected full within 72h |
| Host CPU trend | `SapHana_LoadHistory_CL` | — | 7 days | Sustained >90% for 4h+ |
| HSR replication lag | `SapHana_SystemReplication_CL` | — | 24h | Increasing trend (lag growing) |
| MVCC version count | `SapHana_Mvcc_CL` | — | 24h | Monotonically increasing |

## Trend Analysis — Linear Regression

```python
def linear_regression(data_points):
    """Simple linear regression on (timestamp, value) pairs.
    Returns slope, intercept, r_squared, and projected crossover time."""
    n = len(data_points)
    if n < 10:
        return None  # Insufficient data

    x = [p[0] for p in data_points]  # timestamps as epoch seconds
    y = [p[1] for p in data_points]  # metric values

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi ** 2 for xi in x)

    denom = n * sum_x2 - sum_x ** 2
    if denom == 0:
        return None

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # R-squared
    y_mean = sum_y / n
    ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    return {"slope": slope, "intercept": intercept, "r_squared": r_squared}

def project_crossover(regression, threshold, current_time_epoch):
    """Project when a metric will cross a threshold."""
    if regression is None or regression["slope"] <= 0:
        return None  # No upward trend
    crossover_epoch = (threshold - regression["intercept"]) / regression["slope"]
    hours_until = (crossover_epoch - current_time_epoch) / 3600
    return hours_until if hours_until > 0 else None
```

## HANA Memory Trend Query

```
SapHana_LoadHistory_CL
| where TimeGenerated > ago(7d)
| where SID_s == "<SID>"
| summarize avg_mem_pct = avg(MEMORY_USED_d / MEMORY_SIZE_d * 100) by bin(TimeGenerated, 1h)
| order by TimeGenerated asc
```

## Action Recommendations

| Projected Event | Time Horizon | Recommended Action | Auto-Executable |
|----------------|-------------|-------------------|-----------------|
| HANA OOM | <72h | Identify top memory consumers, recommend service restart | Yes (with approval) |
| HANA OOM | <12h | Emergency: restart non-critical services | Yes (with approval) + P1 escalation |
| /hana/log full | <48h | Trigger log backup + catalog cleanup | Yes (with approval) |
| /hana/log full | <12h | Emergency log backup | Yes (via T4 Self-Healing) |
| CPU sustained >90% | >4h | Identify top consumers, check for runaway queries | No (investigation) |
| MVCC growing | >24h trend | Identify long-running transaction | No (investigation) |

## T3 Approval Flow

```python
def recommend_action(metric, projection, system):
    """Generate recommendation for approval."""
    recommendation = {
        "system": system,
        "metric": metric,
        "current_value": projection["current"],
        "projected_threshold_hours": projection["hours_until"],
        "recommended_action": projection["action"],
        "risk_level": "LOW" if projection["hours_until"] > 48 else "MEDIUM",
        "auto_executable": projection["auto_executable"]
    }
    # In production: send Teams Adaptive Card via Teams connector
    # In demo: display in chat and ask for approval
    return recommendation
```

## Output Format

```
SAP Anomaly Forecast — AB1

📈 HANA Memory: 72% → trending +1.5%/day (R²=0.89)
   Projected OOM: ~18 days (May 25)
   Status: 🟡 WATCH (no immediate action needed)

📈 /hana/log: 45% → stable (slope ≈ 0)
   Status: 🟢 STABLE

📈 CPU: 34% avg → stable
   Status: 🟢 STABLE

📈 MVCC versions: 45,000 → stable
   Status: 🟢 STABLE

No immediate actions required. Next check in 6 hours.
```

Or when threshold is crossed:
```
🔴 SAP Anomaly Alert — AB1

📈 HANA Memory: 89% → trending +3.2%/day (R²=0.94)
   Projected OOM: ~36 hours (May 9 15:00 UTC)

   TOP MEMORY CONSUMERS:
   1. indexserver: 28 GB (62% of allocated)
   2. preprocessor: 4 GB (9%)
   3. compileserver: 2 GB (4%)

   RECOMMENDED ACTION: Restart preprocessor service (4 GB freed)
   Risk: LOW (non-critical service, auto-restarts)

   [Approve Restart] [Deny] [Show Full Analysis]
```
