---
name: sap-command-runner
description: "Runs allowlisted read-only commands on SAP VMs via a secure MCP command proxy (Container App + Azure VM Run Command API). 14 read-only commands — zero changes to SAP environment. Users invoke directly for live VM queries; other skills invoke it for data collection."
tools:
    - ExecutePythonCode
---

## Environment Configuration

All environment-specific values (MCP endpoint URL, API key, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Data Reuse (AAU Optimization)**: If the same command was already run on this VM earlier in this conversation, return the cached result instead of re-executing. Only re-execute if the user explicitly asks to refresh or re-run.

## Infrastructure Requirements

This skill **requires the MCP command proxy** (live-command broker), registered as the **`sap-sre-proxy` MCP connector** (from the `sap-sre-proxy-ops` plugin's `.mcp.json`).

- **Connector configured** — call its tools directly: `list_allowed_commands`, `run_command`, `run_batch`. The agent invokes these natively; no Python/HTTP needed. The MCP server enforces the allowlist and runs commands under its own managed identity.
- **Connector missing** — Respond exactly: "Live VM commands require the MCP command proxy. Deploy it with `infra/deploy-mcp-proxy.ps1`, add the `sap-sre-proxy` MCP connector, then re-paste team onboarding. Until then, this skill is unavailable." Then stop.

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

Call the `sap-sre-proxy` MCP connector's tools directly:
- `list_allowed_commands()` — discover the allowlist.
- `run_command(vm, resource_group, command_id, subscription_id, sid, sidadm, instance)` — run one command. Always pass `subscription_id` — the proxy runs in a different subscription than the SAP VMs.
- `run_batch(vm, resource_group, command_ids, ...)` — run up to 6 commands.

Show the returned output **verbatim**. The MCP server enforces the same allowlist and runs commands under its own managed identity.

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

If the MCP command proxy is unreachable or returns an error, inform the user:
- Authentication failure: "Command proxy authentication failed. Check the MCP connector's API key in Team Onboarding."
- Timeout: "VM did not respond within timeout. The VM may be stopped or unresponsive."
- Connection error: "Command proxy is unreachable. Verify the `sap-sre-proxy` MCP connector and that the Container App is running."
