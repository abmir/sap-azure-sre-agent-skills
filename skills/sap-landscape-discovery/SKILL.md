---
name: sap-landscape-discovery
description: "Discovers and maintains a trustworthy SAP system inventory across SIDs, roles, hosts, regions, VM sizes, zones, HA/DR topology, and monitoring coverage. Foundation data consumed by all other skills. Also validates VM power state on demand."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
    - SearchMemory
    - SearchIncidentKnowledge
    - MCP-MSLearnDocs_microsoft_docs_search
    - MCP-MSLearnDocs_microsoft_docs_fetch
    - CheckIfResourceExists
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - QueryLogAnalyticsByWorkspaceId
    - GetMetricTimeSeriesElementsForAzureResource
    - PlotBarChart
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## When to Use

- "What SAP systems do I have?"
- "Show SAP landscape inventory"
- "Is AB1 running?" / "Show VM power state for all systems"
- "Discover SAP systems in my subscription"
- "Add SAP system AB7 to inventory"
- "Which systems are missing HA monitoring?"

**Routing**: When a user asks "is X running?" or "is SAP up?" — handle it HERE (Mode 3 power state check). Do NOT route to the Command Executor internal tool.

## Modes

### Mode 1: Auto-discover from Azure
Query Azure Resource Graph for VMs with SAP-related tags and ACSS Virtual Instances.

### Mode 2: Import from CSV/conversation
Customer provides data as CSV, pasted table, or verbal description.

### Mode 3: Power state check
Live ARM API query for VM instance view (running/stopped/deallocated).

### Mode 4: Retrieve existing inventory
Read from agent Knowledge Base (primary) or config proxy registry (fallback).

## Authentication

```python
import requests, json

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

def query_resource_graph(query):
    token = get_mi_token("https://management.azure.com/")
    resp = requests.post(
        "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "subscriptions": [SUB_ID]}, timeout=60)
    resp.raise_for_status()
    return resp.json().get("data", [])

def get_landscape_from_proxy():
    resp = requests.get(f"{PROXY_URL}/registry", headers={"x-api-key": PROXY_KEY}, timeout=30)
    return resp.json() if resp.status_code == 200 else None
```

## Mode 1: Auto-discover

```python
def discover_from_tags():
    query = """
    resources
    | where type == 'microsoft.compute/virtualmachines'
    | where tags contains 'SAPSystemSID' or tags contains 'sapsid' or tags contains 'SID'
    | project name, resourceGroup, location, tags,
              zones=zones, vmSize=properties.hardwareProfile.vmSize
    """
    return query_resource_graph(query)

def discover_from_vis():
    try:
        vis_list = arm_get(f"/subscriptions/{SUB_ID}/providers/Microsoft.Workloads/sapVirtualInstances", "2023-10-01-preview")
        return vis_list.get("value", [])
    except Exception:
        return []
```

## Mode 3: Power State Check

```python
def check_power_state(vm_name, rg):
    iv = arm_get(f"/subscriptions/{SUB_ID}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{vm_name}/instanceView")
    statuses = iv.get("statuses", [])
    for s in statuses:
        if s.get("code", "").startswith("PowerState/"):
            return s["code"].replace("PowerState/", "")
    return "unknown"
```

## Output Format

Display inventory as a structured table:
| SID | Type | RG | VMs | Roles | VM Size | Zones | HA |

For power state: append a column with GREEN (running), RED (stopped/deallocated).
