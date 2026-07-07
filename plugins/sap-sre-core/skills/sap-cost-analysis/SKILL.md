---
name: sap-cost-analysis
description: "Analyzes Azure costs for SAP systems. Per-system cost breakdown, RI coverage, deallocated VM savings, rightsizing opportunities, and SRE agent operating cost. Prefers Azure Cost Management for actuals; falls back to a retail-pricing estimate when Cost Management is unavailable in the sandbox. No proxy required."
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

## Cost data source — try in order, NEVER stall

The agent sandbox usually does **not** have the Cost Management CLI extension (`az costmanagement`), and the Cost Management REST API may be blocked by policy (e.g. MCAP). Do **not** loop or retry it — fall straight through this chain and **always disclose which method you used** in the report header:

1. **Actual cost (preferred) — lead with the REST API, not the CLI.** Make **one** attempt at the Cost Management REST API via **`GetArmResourceAsJson`** (built-in, needs no extension — see body below) for month-to-date actuals. Do **not** start with `az costmanagement` (the CLI extension is frequently absent in the sandbox and wastes a turn discovering that). If the REST call returns 401/403/404, abandon immediately and go to step 2. No retries.
2. **Retail-price estimate (reliable fallback)** — build the resource inventory from ARM / Azure Resource Graph (VM size, managed-disk SKUs + sizes, snapshots), then price it with the **Azure Retail Prices API** (`https://prices.azure.com/api/retail/prices?$filter=armRegionName eq '<region>' and armSkuName eq '<sku>'`). Compute VM (hourly rate × 730h), each disk by its tier (S6/S10/S15/P10…), and snapshots (~$0.05/GB-month).
3. **Web-search prices (last resort)** — if the Retail Prices API is network-blocked in the sandbox, web-search current PAYG rates for the VM size and disk tiers in the region.

**Header every estimate with the method**, e.g. *"Cost Management API unavailable — estimate uses Azure Retail Pricing (PAYG, centralus)."* An estimate that is delivered is far more useful than stalling on an unavailable actual-cost API.

## Cost Query — Azure Cost Management (step 1, best-effort only)

**Primary attempt — REST API via `GetArmResourceAsJson`** (no CLI extension required, works in customer tenants and the sandbox):
- URL: `/subscriptions/{SUB_ID}/providers/Microsoft.CostManagement/query?api-version=2023-03-01`
- Method: POST
- Body: `{"type":"ActualCost","timeframe":"MonthToDate","dataset":{"granularity":"Daily","aggregation":{"totalCost":{"name":"Cost","function":"Sum"}},"grouping":[{"type":"Dimension","name":"ResourceGroup"}]}}`

Only if the REST API is unavailable AND the CLI extension is known to be present, you may try `az costmanagement query` via **RunAzCliReadCommands** (one attempt, no retries) — otherwise skip straight to the retail-price estimate:

```bash
# One-shot only; skip entirely if the extension isn't installed
az costmanagement query --type ActualCost --timeframe MonthToDate --dataset-grouping name=ResourceGroup type=Dimension --dataset-filter "{\"dimensions\":{\"name\":\"ResourceGroup\",\"operator\":\"In\",\"values\":[\"<RG1>\",\"<RG2>\"]}}" --scope "subscriptions/{SUB_ID}" -o json
```

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
