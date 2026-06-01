---
name: sap-command-runner
description: "Runs allowlisted read-only commands on SAP VMs via a secure command proxy (Container App + Azure VM Run Command API). 14 read-only commands — zero changes to SAP environment. Users invoke directly for live VM queries; other skills invoke it for data collection."
tools:
    - ExecutePythonCode
---

## Environment Configuration

All environment-specific values (proxy URL, API key, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Data Reuse (AAU Optimization)**: If the same command was already run on this VM earlier in this conversation, return the cached result instead of re-executing. Only re-execute if the user explicitly asks to refresh or re-run.

## Infrastructure Requirements

This skill **requires an SRE Proxy** in the `## Deployed Infrastructure` section of Team Onboarding.

- **If no SRE Proxy is listed** — Respond exactly: "Live VM commands require the SRE Proxy (Container App). No SRE Proxy is listed in Deployed Infrastructure. Run `infra/deploy-sre-infra.ps1 -Mode Full` to deploy the proxy, then re-paste team onboarding with the proxy URL and API key. Until then, this skill is unavailable." Then stop. Do NOT attempt to call any proxy URL.
- **If SRE Proxy is listed** — Run the full flow below using the proxy URL and API key from Team Onboarding.

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

The proxy uses **API key authentication** via the `X-API-Key` header. The API key is provided in Team Onboarding context. **IMPORTANT:** Always include `subscription_id` in the request body — the proxy runs in a different subscription than the SAP VMs.

```python
import requests
import json

# These values come from Team Onboarding context:
# PROXY_URL = proxy URL from Data Sources section
# API_KEY = API key from proxy auth section
# SAP_SUB = SAP Subscription ID from Agent Identity section

def run_command(command_id, vm="AB1vm", rg="RG_SAP_CUS_AB1", sidadm=None, instance="00", sid=None):
    body = {
        "vm": vm,
        "rg": rg,
        "command_id": command_id,
        "subscription_id": SAP_SUB  # REQUIRED — proxy is in a different subscription
    }
    if sidadm: body["sidadm"] = sidadm
    if instance: body["instance"] = instance
    if sid: body["sid"] = sid
    resp = requests.post(f"{PROXY_URL}/api/command",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        json=body, timeout=180)
    return {"status": resp.status_code, "output": resp.text}

def list_available_commands():
    resp = requests.get(f"{PROXY_URL}/api/commands",
        headers={"X-API-Key": API_KEY}, timeout=30)
    return resp.json().get("commands", {}) if resp.status_code == 200 else {}
```

### Critical: Split SID Systems

Some systems have different SIDs for SAP and HANA. Check the landscape inventory for `sap_sidadm` and `hana_sidadm` fields:
- **HANA commands** (`hdb_info`, `hdb_version`, `hsr_state`, `landscape_host_config`): use `hana_sidadm` and `db_sid`
- **SAP commands** (`sapcontrol_getprocesslist`, `sapcontrol_getinstancelist`): use `sap_sidadm` and `sid`
- Example: AB1 system has `sap_sidadm=ab1adm` (SID=AB1) and `hana_sidadm=db1adm` (DB SID=DB1)

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
- **vm**: VM hostname (e.g., `AB1vm`)
- **rg**: Resource group containing the VM (e.g., `RG_SAP_CUS_AB1`)
- **subscription_id**: SAP Subscription from Team Onboarding Agent Identity section (e.g., `40050ff9-...`)
- **sidadm**: Check `hana_sidadm` vs `sap_sidadm` in the inventory (e.g., `db1adm` for HANA, `ab1adm` for SAP)
- **instance**: HANA instance number (usually `00`) or ASCS instance (usually `01`)
- **sid**: SAP SID or DB SID in uppercase depending on the command type

When the user says "run crm_mon on AB1", look up AB1 in the landscape inventory to find the VM name, resource group, and subscription, then call `run_command()`.

## Error Handling

If the command proxy is unreachable or returns an error, inform the user:
- 401: "Command proxy authentication failed. Check the API key in Team Onboarding."
- 502/504: "VM did not respond within timeout. The VM may be stopped or unresponsive."
- Connection error: "Command proxy is unreachable. Verify the proxy URL and that the Container App is running."
