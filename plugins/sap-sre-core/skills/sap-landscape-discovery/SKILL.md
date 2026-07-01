---
name: sap-landscape-discovery
description: "Discovers and maintains SAP system inventory across SIDs, roles, hosts, regions, VM sizes, zones, HA topology, and monitoring coverage. Foundation data consumed by all other skills. Validates VM power state on demand. No proxy required — uses Azure Resource Graph and ARM APIs."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - QueryLogAnalyticsByWorkspaceId
    - PlotBarChart
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Config reads & proxy fallback**: Stored SAP/OS configs are read **directly from the `sap-configs` blob container using the agent's own Managed Identity** (`--auth-mode login`) — there is **no config proxy**. The MCP command proxy is optional and runs only **live VM commands**; if it is not deployed or errors (timeout, 5xx, unreachable), continue with stored blob configs + Azure-native sources (AMS, ARM API, Azure Monitor). Never block the skill on the proxy.

## When to Use

- "What SAP systems do I have?"
- "Show SAP landscape inventory"
- "Show VM power state for all systems"
- "Discover SAP systems in my subscription"
- "Add SAP system AB7 to inventory"
- "Which systems are missing HA monitoring?"

**Routing**: For full-stack status checks ("Is AB1 up?", "Is SAP running?") → route to SAP Operational Health. Use this skill only for inventory questions, VM power state queries in bulk, or system discovery.

## Modes

### Mode 1: Auto-discover from Azure
Query Azure Resource Graph for VMs with SAP-related tags and ACSS Virtual Instances.

### Mode 2: Import from CSV/conversation
Customer provides data as CSV, pasted table, or verbal description.

### Mode 3: Power state check
Live ARM API query for VM instance view (running/stopped/deallocated).

### Mode 4: Retrieve existing inventory
Read from agent Knowledge Base (primary), or read the landscape inventory blob directly from the `sap-configs` container with the agent's own MI (fallback).

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

# Landscape inventory (Mode 4): read from the agent Knowledge Base (primary), or the
# landscape-inventory blob in the sap-configs container with the agent's own MI (fallback):
#   az storage blob download --auth-mode login --account-name <sa> --container-name sap-configs \
#     --name landscape/sap-landscape-inventory.json
# There is NO /api/registry endpoint — the proxy is MCP (live commands only).
```

## Mode 1: Auto-discover

```python
# Pseudocode — use GetArmResourceAsJson or RunAzCliReadCommands tool instead
def discover_from_tags():
    query = """
    resources
    | where type == 'microsoft.compute/virtualmachines'
    | where tags contains 'SAPSystemSID' or tags contains 'sapsid' or tags contains 'SID'
    | project name, resourceGroup, location, tags,
              zones=zones, vmSize=properties.hardwareProfile.vmSize
    """
    return query_resource_graph(query)

# Pseudocode — use GetArmResourceAsJson or RunAzCliReadCommands tool instead
def discover_from_vis():
    try:
        vis_list = arm_get(f"/subscriptions/{SUB_ID}/providers/Microsoft.Workloads/sapVirtualInstances", "2023-10-01-preview")
        return vis_list.get("value", [])
    except Exception:
        return []
```

## Mode 3: Power State Check

```python
# Pseudocode — use GetArmResourceAsJson or RunAzCliReadCommands tool instead
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
