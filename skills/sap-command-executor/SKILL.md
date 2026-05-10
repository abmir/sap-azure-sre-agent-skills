---
name: sap-command-executor
description: "Executes allowlisted commands on SAP VMs via a secure command proxy (Azure Function + VM Run Command). Supports 24 allowlisted commands (14 read-only + 10 write). The ONLY skill with command proxy credentials. Used directly by users for live VM queries, and invoked by other skills (Config Guardian, HA Guardian, Maintenance Autopilot, Self-Healing) for remediation actions."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
    - SearchMemory
    - SearchIncidentKnowledge
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

## When to Use

### User-facing (direct invocation):
- "Run crm_mon on vm01" / "Show cluster status on HSO"
- "Show SAP process list on AB1vm"
- "Get HANA version on ab3dbvm"
- "Check HSR replication state on vm01"
- "Show memory usage on AB1vm" / "Check filesystem usage"
- "List available commands"

### Agent-internal (invoked by other skills):
- SAP Configuration Guardian (T3): `sysctl_apply` to fix config drift
- SAP HA & DR Guardian (T3): `crm_cleanup`, `crm_maintenance_on/off` for cluster remediation
- SAP Maintenance Autopilot (T4): `sap_stop_graceful`, `sap_start`, `hdb_stop`, `hdb_start`
- SAP Self-Healing (T4): `hana_log_backup`, `hana_log_cleanup`, `sysctl_apply`

### When NOT to Use
- "Is AB1 running?" → Use SAP Landscape Discovery (power state check)
- "Is SAP system up?" → Use SAP Landscape Discovery
- General health/status questions → Use SAP Operational Health

## Command Proxy

```python
# COMMAND_PROXY_URL: Use command_proxy_url from Team Onboarding
# COMMAND_PROXY_KEY: Use command_proxy_api_key from Team Onboarding

def run_command(vm_name, rg, command_id, sidadm=None, instance="00", sid=None):
    body = {"vm": vm_name, "rg": rg, "command_id": command_id}
    if sidadm: body["sidadm"] = sidadm
    if instance: body["instance"] = instance
    if sid: body["sid"] = sid
    resp = requests.post(f"{COMMAND_PROXY_URL}/command",
        headers={"x-api-key": COMMAND_PROXY_KEY, "Content-Type": "application/json"},
        json=body, timeout=120)
    if resp.status_code == 200:
        return resp.json().get("stdout", "")
    return f"ERROR: {resp.status_code} — {resp.text}"

def list_available_commands():
    resp = requests.get(f"{COMMAND_PROXY_URL}/commands",
        headers={"x-api-key": COMMAND_PROXY_KEY}, timeout=30)
    return resp.json().get("commands", {}) if resp.status_code == 200 else {}
```

## Available Commands (24 total)

### Read-Only Commands (14 — existing)
| Command ID | Description | Requires sidadm |
|---|---|---|
| `crm_mon` | Pacemaker cluster status | No |
| `crm_status` | Pacemaker resource status | No |
| `saphanasr_showattr` | HANA SR site attributes | No |
| `sapcontrol_getprocesslist` | SAP process list | Yes |
| `hdb_info` | HANA process information | Yes |
| `hdb_version` | HANA version/revision | Yes |
| `hsr_state` | HSR replication state | Yes |
| `systemctl_cluster` | Pacemaker/corosync/SBD status | No |
| `df_hana` | HANA filesystem usage | No |
| `free_mem` | Memory usage | No |
| `uptime` | System uptime and load | No |
| `os_release` | OS version | No |
| `sapcontrol_getinstancelist` | SAP instance list | Yes |
| `landscape_host_config` | HANA landscape config | Yes |

### Write Commands (10 — new, for T3/T4)
| Command ID | Description | Requires sidadm | Used By |
|---|---|---|---|
| `sysctl_apply` | Apply sysctl.d config | No | Config Guardian, Self-Healing |
| `sap_stop_graceful` | `sapcontrol -function StopSystem` | Yes | Maintenance Autopilot |
| `sap_start` | `sapcontrol -function StartSystem` | Yes | Maintenance Autopilot |
| `hdb_stop` | `HDB stop` | Yes | Maintenance Autopilot |
| `hdb_start` | `HDB start` | Yes | Maintenance Autopilot |
| `hana_log_backup` | Trigger HANA log backup via hdbsql | Yes | Self-Healing |
| `hana_log_cleanup` | Purge old backup catalog | Yes | Self-Healing |
| `crm_maintenance_on` | `crm node standby <node>` | No | HA & DR Guardian |
| `crm_maintenance_off` | `crm node online <node>` | No | HA & DR Guardian |
| `crm_cleanup` | `crm resource cleanup <rsc>` | No | HA & DR Guardian |

### Planned Commands (for Scheduled Events)
| Command ID | Description |
|---|---|
| `scheduled_events` | Read Azure metadata scheduled events API |
