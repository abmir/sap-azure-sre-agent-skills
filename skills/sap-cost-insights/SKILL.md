---
name: sap-cost-insights
description: "Analyzes Azure costs for SAP systems and SRE agent infrastructure. Per-system cost breakdown, RI coverage, deallocated VM savings, and agent operating cost."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
    - SearchMemory
    - SearchIncidentKnowledge
    - MCP-MSLearnDocs_microsoft_docs_search
    - MCP-MSLearnDocs_microsoft_docs_fetch
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - GetMetricTimeSeriesElementsForAzureResource
    - PlotPieChart
    - PlotBarChart
    - PlotAreaChartWithCorrelation
    - CreateScheduledMonitoringTask
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## When to Use

- "How much do our SAP systems cost?"
- "Cost breakdown by system"
- "Any savings from deallocated VMs?"
- "RI coverage for SAP VMs?"
- "How much does the SRE agent cost to run?"

## Authentication

```python
import requests, json
from datetime import datetime, timedelta, timezone

# SUB_ID: Use subscription_id from Team Onboarding

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

def get_landscape_registry():
    resp = requests.get(f"{PROXY_URL}/registry", headers={"x-api-key": PROXY_KEY}, timeout=30)
    return resp.json() if resp.status_code == 200 else None
```

## Cost Query — Azure Cost Management API

```python
def query_costs(rg_list, timeframe="MonthToDate"):
    token = get_mi_token("https://management.azure.com/")
    url = f"https://management.azure.com/subscriptions/{SUB_ID}/providers/Microsoft.CostManagement/query?api-version=2023-03-01"
    body = {
        "type": "ActualCost",
        "timeframe": timeframe,
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "ResourceGroup"}],
            "filter": {
                "dimensions": {"name": "ResourceGroup", "operator": "In", "values": rg_list}
            }
        }
    }
    resp = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body, timeout=120)
    resp.raise_for_status()
    return resp.json()
```

## Analysis Areas

### 1. Per-System Cost Breakdown
Map RGs to SAP SIDs via landscape registry, query Cost Management, group by system.

### 2. Deallocated VM Savings
Check VM power state via ARM instance view. If stopped/deallocated, calculate savings vs running.

### 3. SRE Agent Operating Cost
Query costs for RG_SAP_SRE_Agent and RG_SRE_OPS (agent resources + functions + storage).

### 4. RI Coverage
Query `Reservations` API to check if SAP VM SKUs have active reservations.

## Output Format

```
SAP Cost Summary — May 2026 (MTD)

| System | RGs | MTD Cost | Daily Avg | Status |
|--------|-----|----------|-----------|--------|
| AB1 | RG_SAP_CUS_AB1 + mrg-AB1 | $1,234 | $178 | Running |
| AB3 | RG_SAP_AB3 + mrg-AB3 | $2,456 | $351 | Running |
| HSO | RG_SAP_CUS | $4,567 | $652 | Running |
| SRE Agent | RG_SAP_SRE_Agent + RG_SRE_OPS | $89 | $13 | Running |

SAVINGS OPPORTUNITIES:
  None identified (all VMs running)

RI COVERAGE:
  AB1 (E16ds_v5): No RI — potential savings $X/mo with 1-yr RI
```
