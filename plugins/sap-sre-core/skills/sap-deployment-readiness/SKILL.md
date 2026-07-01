---
name: sap-deployment-readiness
description: "Pre-flight validation for SAP VM deployments and migrations. Checks VM SKU catalog availability, zone support, subscription quota, restrictions, and SAP/HANA certification against SAP Notes 1928533 and 2235581. Certified families/SKUs are fetched live from the repo's knowledge/sap-certified-vms.json (with an embedded fallback). No proxy required."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Config reads & proxy fallback**: Stored SAP/OS configs are read **directly from the `sap-configs` blob container using the agent's own Managed Identity** (`--auth-mode login`) — there is **no config proxy**. The MCP command proxy is optional and runs only **live VM commands**; if it is not deployed or errors (timeout, 5xx, unreachable), continue with stored blob configs + Azure-native sources (AMS, ARM API, Azure Monitor). Never block the skill on the proxy.

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
- For live VM commands: invoke the **SAP Command Runner** skill (the `sap-sre-proxy` MCP connector) — never make direct HTTP or proxy calls

```python
# Use the built-in tools above for Azure API access.
import json

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

The certified SAP families and HANA SKUs are **fetched live** from the repo's single source of
truth — [`knowledge/sap-certified-vms.json`](../../../../knowledge/sap-certified-vms.json) — so the
cert data updates the moment a maintainer merges a new SAP Note 1928533 / 2235581 revision, with no
plugin re-install. If the fetch fails, fall back to the embedded snapshot below.

**Onboarding value (optional):** `Knowledge base raw URL` — the raw base of the customer's fork,
e.g. `https://raw.githubusercontent.com/<org>/sap-azure-sre-agent/main`. If absent, default to the
upstream `mcaps-microsoft/sap-azure-sre-agent` on `main`.

```python
import requests

# Raw base of the connected repo. Read "Knowledge base raw URL" from Team Onboarding if provided.
KB_RAW_BASE = ONBOARDING.get("knowledge_base_raw_url",
    "https://raw.githubusercontent.com/mcaps-microsoft/sap-azure-sre-agent/main")

# Embedded fallback snapshot — used ONLY if the live fetch fails. Keep in sync with the repo file.
SAP_CERTIFIED_FAMILIES_FALLBACK = [
    "standardMSFamily", "standardMSv2Family", "standardMDSv2MedMemFamily",
    "standardMBSv3Family", "standardMSv3MedMemFamily",
    "standardESv3Family", "standardEDSv4Family", "standardEDSv5Family",
    "standardEASv4Family", "standardEASv5Family", "standardEBSv5Family",
    "standardDSv3Family", "standardDDSv4Family", "standardDDSv5Family",
    "standardDASv4Family", "standardDASv5Family",
]
HANA_CERTIFIED_SKUS_FALLBACK = [
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

def load_certification():
    """Fetch the live cert reference from the repo; fall back to the embedded snapshot."""
    try:
        r = requests.get(f"{KB_RAW_BASE}/knowledge/sap-certified-vms.json", timeout=30)
        if r.status_code == 200:
            data = r.json()
            fams = data.get("sap_certified_families") or []
            skus = data.get("hana_certified_skus") or []
            if fams and skus:
                ver = data.get("_metadata", {}).get("version", "unknown")
                return fams, skus, f"github_live (knowledge/sap-certified-vms.json @ {ver})"
    except Exception as e:
        print(f"cert fetch failed, using embedded fallback: {e}")
    return (SAP_CERTIFIED_FAMILIES_FALLBACK, HANA_CERTIFIED_SKUS_FALLBACK,
            "embedded_fallback (live fetch unavailable)")

SAP_CERTIFIED_FAMILIES, HANA_CERTIFIED_SKUS, CERT_SOURCE = load_certification()
# Surface CERT_SOURCE in the report so the user knows whether cert data was live or fallback.
```

## Output Format

Structured go/no-go report:
- SKU: exists / not found, zones supported
- Quota: X available of Y limit (PASS/FAIL for requested count)
- SAP certified: YES/NO
- HANA certified: YES/NO
- Capabilities: AccelNet, PremiumStorage, etc.
- Cert data source: value of `CERT_SOURCE` (`github_live ...` or `embedded_fallback ...`) so the user knows whether the certification check used the live repo reference or the embedded snapshot.
