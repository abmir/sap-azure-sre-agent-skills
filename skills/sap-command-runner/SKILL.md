---
name: sap-command-runner
description: "Runs allowlisted read-only commands on SAP VMs via a secure command proxy (Container App + Azure VM Run Command API). 14 read-only commands — zero changes to SAP environment. Users invoke directly for live VM queries; other skills invoke it for data collection."
tools:
    - ExecutePythonCode
---

## Environment Configuration

All environment-specific values (proxy URL, API key, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

## When to Use

- "Run crm_mon on vm01" / "Show cluster status on vm01"
- "Show SAP process list on AB1vm"
- "Get HANA version on ab3dbvm"
- "Check HSR replication state on vm01"
- "Show memory usage on AB1vm" / "Check filesystem usage on ab2dbvm"
- "What commands are available?" / "List commands"

### When NOT to Use
- "Is AB1 running?" → Use SAP Landscape Discovery (VM power state via ARM API)
- "Is everything healthy?" → Use SAP Operational Health
- General health/status questions → Use SAP Operational Health

## Output Format

Display command output **exactly as it appears on the VM** — preserve formatting, alignment, and whitespace. Do not summarize or reformat unless the user explicitly asks for interpretation.

## Command Proxy

```python
import requests

# PROXY_URL and PROXY_KEY come from Team Onboarding context

def run_command(vm_name, rg, command_id, sidadm=None, instance="00", sid=None):
    body = {"vm": vm_name, "rg": rg, "command_id": command_id}
    if sidadm: body["sidadm"] = sidadm
    if instance: body["instance"] = instance
    if sid: body["sid"] = sid
    resp = requests.post(f"{PROXY_URL}/api/command",
        headers={"x-api-key": PROXY_KEY, "Content-Type": "application/json"},
        json=body, timeout=120)
    if resp.status_code == 200:
        return resp.json().get("stdout", "")
    return f"ERROR: {resp.status_code} — {resp.text}"

def list_available_commands():
    resp = requests.get(f"{PROXY_URL}/api/commands",
        headers={"x-api-key": PROXY_KEY}, timeout=30)
    return resp.json().get("commands", {}) if resp.status_code == 200 else {}
```

## Available Commands (14 — all read-only)

All commands are read-only. None modify SAP state, HANA data, cluster config, or OS settings.

| Command ID | Description | Requires sidadm |
|---|---|---|
| `crm_mon` | Pacemaker cluster status (`crm_mon -r -1`) | No |
| `crm_status` | Pacemaker resource status (`crm status`) | No |
| `saphanasr_showattr` | HANA SR site attributes (`SAPHanaSR-showAttr`) | No |
| `sapcontrol_getprocesslist` | SAP process list (`sapcontrol -function GetProcessList`) | Yes |
| `hdb_info` | HANA process information (`HDB info`) | Yes |
| `hdb_version` | HANA version/revision (`HDB version`) | Yes |
| `hsr_state` | HSR replication state (`hdbnsutil -sr_state`) | Yes |
| `systemctl_cluster` | Pacemaker/corosync/SBD service status | No |
| `df_hana` | HANA filesystem usage (`df -h /hana/*`) | No |
| `free_mem` | Memory usage (`free -h`) | No |
| `uptime` | System uptime and load average | No |
| `os_release` | OS version (`cat /etc/os-release`) | No |
| `sapcontrol_getinstancelist` | SAP instance list (`sapcontrol -function GetSystemInstanceList`) | Yes |
| `landscape_host_config` | HANA landscape host configuration | Yes |

## Identifying VM and Parameters

Use the SAP landscape inventory (Knowledge Source) to resolve:
- **vm**: VM hostname (e.g., `ab1vm`, `vm01`, `ab3dbvm`)
- **rg**: Resource group containing the VM (e.g., `RG_SAP_AB1`)
- **sidadm**: `<sid>adm` in lowercase (e.g., `ab1adm`, `db1adm`, `hsoadm`)
- **instance**: HANA instance number, usually `00`
- **sid**: SAP SID in uppercase (e.g., `AB1`, `HSO`)

When the user says "run crm_mon on AB1", look up AB1 in the landscape inventory to find the VM name and resource group, then call `run_command()`.

## Error Handling

If the command proxy is unreachable or returns an error, inform the user:
- 401: "Command proxy authentication failed. Check the API key in Team Onboarding."
- 502/504: "VM did not respond within timeout. The VM may be stopped or unresponsive."
- Connection error: "Command proxy is unreachable. Verify the proxy URL and that the Container App is running."
