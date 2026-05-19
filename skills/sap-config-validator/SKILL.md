---
name: sap-config-validator
description: "Validates SAP system configurations against Microsoft's SAP Testing Automation Framework (STAF) check definitions. Fetches current check definitions from the Azure/sap-automation-qa GitHub repo, compares against collected config snapshots from blob storage. Every check comes from STAF — nothing custom. Read-only."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - QueryLogAnalyticsByWorkspaceId
    - PlotBarChart
    - PlotHeatmap
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse**: Before calling any API, check if the data was already retrieved earlier in this conversation. Reuse STAF check definitions and config file data from context.

## When to Use

- "Run config checks for AB1" / "Validate configuration for AB3"
- "STAF checks for HSO" / "Run all configuration checks"
- "Check OS parameters" / "Check cluster configuration"

## Architecture

This skill does NOT hardcode expected values. Instead:

1. **Expected values** come from the [SAP Testing Automation Framework (STAF)](https://github.com/Azure/sap-automation-qa) YAML check definitions, fetched from GitHub at runtime.
2. **Actual values** come from three sources (tried in order):
   - **Azure ARM API** — for infrastructure checks (VM SKU, NICs, disks, Advisor). Always available.
   - **Command proxy** — for live OS/HANA data (sysctl, df, HDB info, sapcontrol). Available when proxy is deployed.
   - **Blob config files** — for offline STAF checks (sysctl, kernel params, HANA global.ini). Available when collector is deployed.
   - If none of the above provides data for a check, report it as "NOT EVALUATED".

Every check in this skill comes from Microsoft's official STAF. Nothing custom.

## Data Sources

| Category | Primary Source | Fallback |
|----------|---------------|----------|
| Check definitions (expected values) | GitHub: `Azure/sap-automation-qa` YAML files | — |
| Infrastructure (VM SKU, AccelNet, disks) | Azure ARM API via built-in tools | — |
| OS/SAP parameters (sysctl, memory, disk space) | Command proxy (`/api/command`) for live data | Blob config files via `/api/configs` |
| HANA config (version, processes) | Command proxy (`hdb_info`, `hdb_version`) | Blob config files |
| Cluster config (stonith, corosync) | Command proxy (`crm_mon`) | Blob config files |

## Proxy Authentication

**IMPORTANT:** Do NOT use IMDS tokens or ManagedIdentityCredential — they are not available in the sandbox. Use API key from Team Onboarding context.

```python
import requests, json

# Values from Team Onboarding context:
PROXY_URL = "..."     # Proxy URL from Data Sources
API_KEY = "..."       # API key from Proxy auth section  
SAP_SUB = "..."       # SAP Subscription from Agent Identity

def run_proxy_command(command_id, vm="AB1vm", rg="RG_SAP_CUS_AB1", sidadm=None, instance="00", sid=None):
    """Run a single command via the proxy. Always pass subscription_id."""
    body = {"vm": vm, "rg": rg, "command_id": command_id, "subscription_id": SAP_SUB}
    if sidadm: body["sidadm"] = sidadm
    if instance: body["instance"] = instance
    if sid: body["sid"] = sid
    resp = requests.post(f"{PROXY_URL}/api/command",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        json=body, timeout=180)
    if resp.status_code == 200:
        return resp.json().get("stdout", "")
    return None

def get_blob_configs(sid, hostname):
    """Get collected config files from blob storage via config proxy."""
    resp = requests.get(f"{PROXY_URL}/api/configs/{sid}/{hostname}",
        headers={"X-API-Key": API_KEY}, timeout=60)
    if resp.status_code == 200:
        return resp.json().get("files", {})
    return {}
```

## Execution Steps

### Step 1: Fetch STAF Check Definitions from GitHub

Use `ExecutePythonCode` to fetch and parse STAF YAML files. The Python code does the heavy lifting — fetches, parses, filters by OS/role/DB/HA type — and returns only the applicable checks as compact JSON. Raw YAML never enters the LLM context.

```python
import requests, yaml, json

STAF_BASE = "https://raw.githubusercontent.com/Azure/sap-automation-qa/main/src/roles/configuration_checks/tasks/files"
STAF_FILES = ["sap.yml", "hana.yml", "virtual_machine.yml", "network.yml",
              "ascs.yml", "app.yml", "high_availability.yml", "package.yml"]

def fetch_staf_checks(os_type, role, db_type, ha_type, ha_agent):
    """Fetch STAF YAML check definitions from GitHub, filter applicable checks.
    Returns compact list: [{id, name, command, expected, validator_type, severity}]"""
    applicable = []
    for filename in STAF_FILES:
        try:
            resp = requests.get(f"{STAF_BASE}/{filename}", timeout=15)
            if resp.status_code != 200:
                continue
            content = yaml.safe_load(resp.text)
            checks = content.get("checks", content if isinstance(content, list) else [])
            for check in checks:
                if not isinstance(check, dict):
                    continue
                # Filter by applicability
                app = check.get("applicability", {})
                if app.get("os_type") and os_type not in app["os_type"] and app["os_type"] != "all":
                    continue
                if app.get("role") and role not in app["role"]:
                    continue
                if app.get("database_type") and db_type not in app["database_type"]:
                    continue
                # Extract compact check info
                collector = check.get("collector_args", {})
                validator = check.get("validator_args", {})
                expected = validator.get("expected_output", validator.get("expected", ""))
                if check.get("validator_type") == "range":
                    expected = f"min={validator.get('min','')},max={validator.get('max','')}"
                applicable.append({
                    "id": check.get("id", "?"),
                    "name": check.get("name", "?"),
                    "cmd": collector.get("command", ""),
                    "expected": expected,
                    "validator": check.get("validator_type", "string"),
                    "severity": check.get("severity", "WARNING"),
                    "category": check.get("category", ""),
                    "collector_type": check.get("collector_type", "command"),
                })
        except Exception:
            continue  # Skip file on error, continue with others
    return json.dumps(applicable)  # Return compact JSON to LLM
```

**Context needed for filtering** (from landscape registry):
- `os_type`: "SLES_SAP" or "REDHAT"
- `role`: "DB", "SCS", "ERS", "APP", "PAS"
- `db_type`: "HANA", "Db2"
- `ha_type`: "scale_up", "scale_out", or "false"
- `ha_agent`: "AFA" (Azure Fence Agent) or "ISCSI" (SBD)

### Step 2: Get Actual Values from Blob Config Files

Read the collected config files for the target system via the config proxy:

```python
def get_configs(sid, hostname):
    resp = requests.get(f"{PROXY_URL}/api/configs/{sid}/{hostname}",
        headers={"x-api-key": PROXY_KEY}, timeout=60)
    if resp.status_code == 200:
        return resp.json().get("files", {})
    return {}
```

Map STAF check commands to the corresponding config file content. For example:
- `sysctl -n vm.swappiness` → look in `os/sysctl-runtime.txt`
- `corosync-cmapctl -g runtime.config.totem.token` → look in `cluster/corosync.conf`
- HANA global.ini params → look in `hana/global.ini`

If config files are unavailable, report the check as "NOT EVALUATED — config data not collected".

### Step 3: Compare Actual vs Expected

For each check, compare the config file value against the STAF expected value using the validator type:

| Validator | Logic |
|-----------|-------|
| `string` | Exact match (case-insensitive, whitespace-trimmed) |
| `range` | Actual value within [min, max] |
| `list` | Actual value in allowed list |
| `min_list` | Each value in space-separated list >= corresponding minimum |

### Step 4: Output

```
AB1 — STAF Config Compliance

  Data Source: Config files (collected 2025-05-18T02:00Z)
  STAF Checks: 47 applicable (from github.com/Azure/sap-automation-qa)

══════════════════════════════════════════

  OS/SAP Params:  20/22 ❌ (vm.swappiness=60, THP=always)
  Cluster:        15/15 ✅
  HANA:            8/8  ✅
  Network:         4/4  ✅

══════════════════════════════════════════
  FAILURES:

  ❌ SAP-OS-001 vm.swappiness: actual=60, expected=10 (CRITICAL)
    Fix: echo 'vm.swappiness=10' > /etc/sysctl.d/sap.conf && sysctl --system

  ❌ SAP-OS-005 THP: actual=always, expected=never (CRITICAL)
    Fix: echo never > /sys/kernel/mm/transparent_hugepage/enabled

══════════════════════════════════════════
  SUMMARY: 47/49 PASS, 0 WARN, 2 FAIL
```

## STAF Check Categories

The STAF YAML files cover these categories (fetched dynamically, not hardcoded):

| YAML File | Category | Example Checks |
|-----------|----------|----------------|
| `sap.yml` | Cluster + Pacemaker | stonith-enabled, corosync token/consensus, SBD, fencing agent, softdog |
| `hana.yml` | HANA Database | global.ini params, log_mode, basepath, HA/DR hooks, memory allocation |
| `virtual_machine.yml` | VM Infrastructure | VM SKU certification, accelerated networking, disk config |
| `network.yml` | Network | TCP params, port ranges, backlog settings |
| `ascs.yml` | Central Services | ASCS/ERS config, virtual hostname, mount options |
| `app.yml` | Application Server | SAP profiles, work process config |
| `high_availability.yml` | HA Config | LB settings, zone placement, resource constraints |
| `package.yml` | Package Versions | Required package versions |

## References
- [SAP Testing Automation Framework (STAF)](https://github.com/Azure/sap-automation-qa)
- [STAF Configuration Checks Guide](https://github.com/Azure/sap-automation-qa/blob/main/docs/CONFIGURATION_CHECKS.md)
- [SAP on Azure Best Practices](https://learn.microsoft.com/en-us/azure/sap/workloads/sap-high-availability-architecture-scenarios)
- [STAF Check Definitions (source of truth)](https://github.com/Azure/sap-automation-qa/tree/main/src/roles/configuration_checks/tasks/files)
