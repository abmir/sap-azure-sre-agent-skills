"""
SAP SRE command proxy — MCP server
======================================================

Exposes the SAP live-command allowlist as **MCP tools** so the Azure SRE Agent can
call them natively through a **connector** (Streamable-HTTP).

Read-only, allowlisted commands only. This does NOT touch storage — config reads are done
by the SRE Agent's own MI reading blob directly. This server only runs live VM commands.

Deploy it with ../infra/deploy-mcp-proxy.ps1, which:
  • Deploys it as a VNet-integrated Azure Container App.
  • Assigns it a managed identity with the custom "SAP SRE Agent Operator" role on the SAP RGs.
  • Wires the connector auth header (below). Before relying on it in production, also
    consider Entra ID / managed identity auth at the ingress, and harden timeouts,
    logging, and error handling.

Run locally:  SUBSCRIPTION_ID=<sub> MCP_API_KEY=<key> python server.py
Then register the URL as an MCP connector in the SRE Agent (see .mcp.json in the
sap-sre-proxy-ops plugin).
"""
import os
import re
import time
import json
import logging

import requests
from azure.identity import DefaultAzureCredential
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sap-sre-mcp")

SUB_ID = os.environ.get("SUBSCRIPTION_ID", "")
ARM = "https://management.azure.com"
API_VERSION = "2023-09-01"

_cred = DefaultAzureCredential()


def _arm_token() -> str:
    return _cred.get_token(f"{ARM}/.default").token


# ─────────────────────────────────────────────────────────────────────────────
# Allowlist — read-only commands only. Collector deployment (`deploy_collector`) is
# intentionally NOT exposed here — it is an operator task (az vm run-command), not an
# agent tool.
# ─────────────────────────────────────────────────────────────────────────────
ALLOWED_COMMANDS = {
    "crm_mon": {"script": "crm_mon -r -1 2>&1", "description": "Pacemaker cluster status", "requires_sidadm": False},
    "crm_status": {"script": "crm status 2>&1", "description": "Pacemaker resource status", "requires_sidadm": False},
    "saphanasr_showattr": {"script": "SAPHanaSR-showAttr 2>&1", "description": "HSR site attributes", "requires_sidadm": False},
    "sapcontrol_getprocesslist": {"script": "su - {sidadm} -c 'sapcontrol -nr {instance} -function GetProcessList' 2>&1", "description": "SAP process list", "requires_sidadm": True},
    "hdb_info": {"script": "su - {sidadm} -c '/usr/sap/{sid}/HDB{instance}/HDB info' 2>&1", "description": "HANA process info", "requires_sidadm": True},
    "hdb_version": {"script": "su - {sidadm} -c '/usr/sap/{sid}/HDB{instance}/HDB version' 2>&1 | head -10", "description": "HANA version", "requires_sidadm": True},
    "hsr_state": {"script": "su - {sidadm} -c '/usr/sap/{sid}/HDB{instance}/exe/hdbnsutil -sr_state' 2>&1", "description": "HSR replication state", "requires_sidadm": True},
    "systemctl_cluster": {"script": "systemctl status pacemaker corosync sbd 2>&1 | head -30", "description": "Cluster service status", "requires_sidadm": False},
    "df_hana": {"script": "df -h /hana/data /hana/log /hana/shared /usr/sap 2>&1", "description": "HANA filesystem usage", "requires_sidadm": False},
    "free_mem": {"script": "free -h 2>&1", "description": "Memory usage", "requires_sidadm": False},
    "uptime": {"script": "uptime 2>&1", "description": "System uptime and load", "requires_sidadm": False},
    "os_release": {"script": "cat /etc/os-release 2>&1", "description": "OS version", "requires_sidadm": False},
    "sapcontrol_getinstancelist": {"script": "su - {sidadm} -c 'sapcontrol -nr {instance} -function GetSystemInstanceList' 2>&1", "description": "SAP instance list", "requires_sidadm": True},
    "landscape_host_config": {"script": "su - {sidadm} -c 'python /usr/sap/{sid}/HDB{instance}/exe/python_support/landscapeHostConfiguration.py' 2>&1 | head -20", "description": "HANA landscape config", "requires_sidadm": True},
}


def _validate(vm: str, rg: str, sidadm: str, instance: str) -> str:
    if not re.match(r"^[a-zA-Z0-9\-_]{1,64}$", vm):
        return f"Invalid VM name: {vm}"
    if not re.match(r"^[a-zA-Z0-9\-_]{1,90}$", rg):
        return f"Invalid resource group: {rg}"
    if sidadm and not re.match(r"^[a-z][a-z0-9]{2}adm$", sidadm):
        return f"Invalid sidadm: {sidadm}"
    if instance and not re.match(r"^[0-9]{2}$", instance):
        return f"Invalid instance number: {instance}"
    return ""


def _extract_output(output_obj) -> str:
    """Pull stdout/stderr message text out of an ARM run-command output blob."""
    try:
        if isinstance(output_obj, dict):
            values = output_obj.get("value") or output_obj.get("output", {}).get("value")
            if values:
                return "\n".join(v.get("message", "") for v in values).strip()
        return json.dumps(output_obj)[:4000]
    except Exception as e:  # noqa: BLE001
        return f"(could not parse output: {e})"


def _run_shell(vm: str, rg: str, script: str) -> str:
    """POST an ARM RunShellScript run-command and poll the async operation to completion."""
    if not SUB_ID:
        return "ERROR: SUBSCRIPTION_ID env var is not set on the MCP server."
    token = _arm_token()
    url = (f"{ARM}/subscriptions/{SUB_ID}/resourceGroups/{rg}/providers/Microsoft.Compute"
           f"/virtualMachines/{vm}/runCommand?api-version={API_VERSION}")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"commandId": "RunShellScript", "script": [script]}
    r = requests.post(url, headers=headers, json=body, timeout=60)
    if r.status_code == 200 and r.text:
        return _extract_output(r.json())
    if r.status_code not in (200, 201, 202):
        return f"ERROR: ARM run-command returned {r.status_code}: {r.text[:500]}"
    poll_url = r.headers.get("Azure-AsyncOperation") or r.headers.get("Location")
    if not poll_url:
        return "ERROR: no async-operation URL returned by ARM."
    for _ in range(60):  # up to ~5 min
        time.sleep(5)
        pr = requests.get(poll_url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        j = pr.json()
        status = j.get("status")
        if status == "Succeeded":
            return _extract_output(j.get("properties", {}).get("output", j))
        if status in ("Failed", "Canceled"):
            return f"ERROR: run-command {status}: {json.dumps(j)[:500]}"
    return "ERROR: run-command timed out after ~5 minutes."


# ─────────────────────────────────────────────────────────────────────────────
# MCP server + tools
# ─────────────────────────────────────────────────────────────────────────────
mcp = FastMCP("sap-sre-proxy")


@mcp.tool()
def list_allowed_commands() -> list[dict]:
    """List the allowlisted, read-only SAP VM commands this server can run."""
    return [
        {"command_id": cid, "description": meta["description"], "requires_sidadm": meta["requires_sidadm"]}
        for cid, meta in ALLOWED_COMMANDS.items()
    ]


@mcp.tool()
def run_command(vm: str, resource_group: str, command_id: str,
                sid: str = "", sidadm: str = "", instance: str = "") -> str:
    """Run ONE allowlisted read-only command on a SAP VM via the Azure run-command API.

    Args:
        vm: VM name (e.g. "AB1vm").
        resource_group: the VM's resource group.
        command_id: one of the ids from list_allowed_commands().
        sid: SAP/HANA SID (needed for HANA/SAP commands, e.g. "DB1").
        sidadm: the <sid>adm OS user (needed when requires_sidadm=True, e.g. "db1adm").
        instance: 2-digit instance number (e.g. "00").
    """
    meta = ALLOWED_COMMANDS.get(command_id)
    if not meta:
        return f"ERROR: '{command_id}' is not allowlisted. Call list_allowed_commands()."
    err = _validate(vm, resource_group, sidadm, instance)
    if err:
        return f"ERROR: {err}"
    if meta["requires_sidadm"] and not (sidadm and instance and sid):
        return f"ERROR: '{command_id}' requires sid, sidadm, and instance."
    script = meta["script"].format(sidadm=sidadm, instance=instance, sid=sid.upper())
    logger.info("run_command %s on %s/%s", command_id, resource_group, vm)
    return _run_shell(vm, resource_group, script)


@mcp.tool()
def run_batch(vm: str, resource_group: str, command_ids: list[str],
              sid: str = "", sidadm: str = "", instance: str = "") -> dict:
    """Run up to 6 allowlisted commands on a VM in one call. Returns {command_id: output}."""
    results: dict[str, str] = {}
    for cid in command_ids[:6]:
        results[cid] = run_command(vm, resource_group, cid, sid, sidadm, instance)
    return results


if __name__ == "__main__":
    # Streamable-HTTP transport → register the resulting URL as an MCP connector.
    mcp.run(transport="streamable-http")
