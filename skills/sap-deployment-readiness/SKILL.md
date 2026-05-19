---
name: sap-deployment-readiness
description: "Pre-flight validation for SAP VM deployments and migrations. Checks VM SKU catalog availability, zone support, subscription quota, restrictions, and SAP/HANA certification against SAP Notes 1928533 and 2235581. No proxy required."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## When to Use

- "Can I deploy Standard_M32ts in centralus?"
- "Check quota for E-series in eastus"
- "What HANA-certified VMs are available in uksouth?"
- "Deployment readiness check for new SAP system"
- "Can I resize my VM to E16ds_v5?"
- "VM SKU migration readiness across regions"

## What This Skill Validates

### CAN validate (via Azure ARM APIs):
1. SKU exists in region catalog
2. Which availability zones support the SKU
3. Subscription-level restrictions (SKU blocked)
4. vCPU quota: limit vs. current usage per family per region
5. SAP certification (SAP Note 1928533)
6. HANA certification (SAP Note 2235581)
7. SKU capabilities (accelerated networking, premium storage, etc.)

### CANNOT validate:
1. Physical deployment capacity (no public API)
2. Customer subscription restrictions (agent's sub only)
3. Real-time capacity constraints

**Always include this caveat:**
> "This report validates SKU catalog availability and subscription quota. Actual deployment capacity cannot be confirmed via API. For 50+ VMs, contact your Microsoft account team or attempt a capacity reservation."

## Authentication

**IMPORTANT — Azure API Access:** Do NOT use IMDS tokens (169.254.169.254) or ManagedIdentityCredential — they are not available in the agent sandbox. Instead:
- For Azure Resource Manager queries: Use the built-in `GetArmResourceAsJson` or `RunAzCliReadCommands` tools
- For Log Analytics queries: Use the built-in `QueryLogAnalyticsByWorkspaceId` tool  
- For metrics: Use the built-in `GetMetricTimeSeriesElementsForAzureResource` tool
- For proxy HTTP calls: Use `ExecutePythonCode` with `X-API-Key` header (API key from Team Onboarding)

```python
# Only use ExecutePythonCode for proxy HTTP calls. Use built-in tools for Azure API access.
import requests, json

# SUB_ID: Use subscription_id from Team Onboarding
```

## Check 1: SKU Catalog

```python
# Pseudocode — use GetArmResourceAsJson or RunAzCliReadCommands tool instead
def check_sku_in_region(target_sku, region):
    skus = arm_get(f"/subscriptions/{SUB_ID}/providers/Microsoft.Compute/skus?$filter=location eq '{region}'", "2021-07-01")
    for sku in skus.get("value", []):
        if sku["name"] == target_sku and sku["resourceType"] == "virtualMachines":
            zones = []
            for loc in sku.get("locationInfo", []):
                zones = loc.get("zones", [])
            restrictions = sku.get("restrictions", [])
            return {
                "exists": True, "zones": sorted(zones),
                "restricted": len(restrictions) > 0,
                "capabilities": {c["name"]: c["value"] for c in sku.get("capabilities", [])}
            }
    return {"exists": False, "zones": [], "restricted": False}
```

## Check 2: Quota

```python
# Pseudocode — use GetArmResourceAsJson or RunAzCliReadCommands tool instead
def check_quota(region, family_name):
    usages = arm_get(f"/subscriptions/{SUB_ID}/providers/Microsoft.Compute/locations/{region}/usages")
    for usage in usages.get("value", []):
        if usage["name"]["value"] == family_name:
            return {"family": family_name, "region": region,
                    "limit": usage["limit"], "used": usage["currentValue"],
                    "available": usage["limit"] - usage["currentValue"]}
    return {"family": family_name, "region": region, "limit": 0, "used": 0, "available": 0}
```

## Check 3: SAP/HANA Certification

```python
SAP_CERTIFIED_FAMILIES = [
    "standardMSFamily", "standardMSv2Family", "standardMDSv2MedMemFamily",
    "standardMBSv3Family", "standardMSv3MedMemFamily",
    "standardESv3Family", "standardEDSv4Family", "standardEDSv5Family",
    "standardEASv4Family", "standardEASv5Family", "standardEBSv5Family",
    "standardDSv3Family", "standardDDSv4Family", "standardDDSv5Family",
    "standardDASv4Family", "standardDASv5Family",
]

HANA_CERTIFIED_SKUS = [
    "Standard_M32ts", "Standard_M32ls", "Standard_M64ls", "Standard_M64s",
    "Standard_M64ms", "Standard_M128s", "Standard_M128ms",
    "Standard_M208s_v2", "Standard_M208ms_v2",
    "Standard_M416s_v2", "Standard_M416ms_v2",
    "Standard_E16ds_v4", "Standard_E20ds_v4", "Standard_E32ds_v4",
    "Standard_E48ds_v4", "Standard_E64ds_v4", "Standard_E96ds_v4",
    "Standard_E16ds_v5", "Standard_E20ds_v5", "Standard_E32ds_v5",
    "Standard_E48ds_v5", "Standard_E64ds_v5", "Standard_E96ds_v5",
    "Standard_E104ids_v5",
    "Standard_E16bds_v5", "Standard_E32bds_v5", "Standard_E48bds_v5",
    "Standard_E64bds_v5", "Standard_E96bds_v5",
]
```

## Output Format

Structured go/no-go report:
- SKU: exists / not found, zones supported
- Quota: X available of Y limit (PASS/FAIL for requested count)
- SAP certified: YES/NO
- HANA certified: YES/NO
- Capabilities: AccelNet, PremiumStorage, etc.
