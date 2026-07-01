---
name: sap-cost-analysis
description: "Analyzes Azure costs for SAP systems. Per-system cost breakdown, RI coverage, deallocated VM savings, rightsizing opportunities, and SRE agent operating cost. No proxy required — uses Azure Cost Management APIs."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - QueryLogAnalyticsByWorkspaceId
    - PlotPieChart
    - PlotBarChart
    - PlotAreaChartWithCorrelation
    - CreateScheduledMonitoringTask
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Config reads & proxy fallback**: Stored SAP/OS configs are read **directly from the `sap-configs` blob container using the agent's own Managed Identity** (`--auth-mode login`) — there is **no config proxy**. The MCP command proxy is optional and runs only **live VM commands**; if it is not deployed or errors (timeout, 5xx, unreachable), continue with stored blob configs + Azure-native sources (AMS, ARM API, Azure Monitor). Never block the skill on the proxy.

## When to Use

- "How much do our SAP systems cost?"
- "Cost breakdown by system"
- "Any savings from deallocated VMs?"
- "RI coverage for SAP VMs?"
- "How much does the SRE agent cost to run?"

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

# Landscape inventory: read from the agent Knowledge Base (primary) or the sap-configs blob
# directly with the agent's own MI. There is NO /api/registry endpoint (proxy is MCP, live commands only).
```

## Cost Query — Azure Cost Management

Use **RunAzCliReadCommands** (NOT ExecutePythonCode) for all cost queries. Example commands:

```bash
# Per-RG cost breakdown (month to date)
az cost management query --type ActualCost --timeframe MonthToDate --scope "subscriptions/{SUB_ID}" --query "[].{rg:ResourceGroup, cost:Cost}" -o table

# Cost for specific SAP resource groups
az costmanagement query --type ActualCost --timeframe MonthToDate --dataset-grouping name=ResourceGroup type=Dimension --dataset-filter "{\"dimensions\":{\"name\":\"ResourceGroup\",\"operator\":\"In\",\"values\":[\"RG_SAP_CUS_AB1\",\"mrg-AB1-48f3f2\"]}}" --scope "subscriptions/{SUB_ID}" -o json
```

If `az costmanagement` is unavailable, use **GetArmResourceAsJson** with the Cost Management REST API:
- URL: `/subscriptions/{SUB_ID}/providers/Microsoft.CostManagement/query?api-version=2023-03-01`
- Method: POST
- Body: `{"type":"ActualCost","timeframe":"MonthToDate","dataset":{"granularity":"Daily","aggregation":{"totalCost":{"name":"Cost","function":"Sum"}},"grouping":[{"type":"Dimension","name":"ResourceGroup"}]}}`

## Azure Advisor Cost Recommendations

**ALWAYS check Advisor for cost savings.** Use **RunAzCliReadCommands**:

```bash
# Get all cost recommendations for the subscription
az advisor recommendation list --subscription {SUB_ID} --category Cost -o json

# Filter for SAP-related resources
az advisor recommendation list --subscription {SUB_ID} --category Cost --query "[?contains(resourceGroup,'SAP') || contains(resourceGroup,'sap') || contains(resourceGroup,'mrg-')]" -o json
```

Or use **GetArmResourceAsJson**:
- URL: `/subscriptions/{SUB_ID}/providers/Microsoft.Advisor/recommendations?api-version=2023-01-01&$filter=Category eq 'Cost'`

Advisor provides: rightsizing recommendations, RI purchase suggestions, shutdown recommendations for idle VMs, and unused resource cleanup.

## Reservation (RI) Coverage

Use **RunAzCliReadCommands**:

```bash
# List active reservations
az reservations reservation-order list -o json

# Check reservation utilization
az consumption reservation summary list --reservation-order-id {ORDER_ID} --grain monthly -o json
```

## Analysis Areas

### 1. Per-System Cost Breakdown
Map RGs to SAP SIDs via landscape registry, query Cost Management, group by system.

### 2. Deallocated VM Savings
Check VM power state via ARM instance view. If stopped/deallocated, calculate savings vs running.

### 3. SRE Agent Operating Cost
Query costs for RG_SAP_SRE_Agent and RG_SRE_OPS (agent resources + functions + storage).

### 4. RI Coverage & Advisor Recommendations
Query `Reservations` API to check if SAP VM SKUs have active reservations. Always check Azure Advisor Cost category for rightsizing, RI purchase, and shutdown recommendations.

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
