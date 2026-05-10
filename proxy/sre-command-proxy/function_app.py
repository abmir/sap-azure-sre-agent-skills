import azure.functions as func
import logging
import json
import os
import re
import requests
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

app = func.FunctionApp()

# Singleton credential — reused across requests, handles token caching/refresh
_credential = None
def get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential

ALLOWED_COMMANDS = {
    "crm_mon": {"script": "crm_mon -r -1 2>&1", "description": "Pacemaker cluster status", "requires_sidadm": False},
    "crm_status": {"script": "crm status 2>&1", "description": "Pacemaker resource status", "requires_sidadm": False},
    "saphanasr_showattr": {"script": "SAPHanaSR-showAttr 2>&1", "description": "HSR site attributes", "requires_sidadm": False},
    "sapcontrol_getprocesslist": {"script": "su - {sidadm} -c 'sapcontrol -nr {instance} -function GetProcessList' 2>&1", "description": "SAP process list", "requires_sidadm": True},
    "hdb_info": {"script": "su - {sidadm} -c 'HDB info' 2>&1", "description": "HANA process info", "requires_sidadm": True},
    "hdb_version": {"script": "su - {sidadm} -c 'HDB version' 2>&1 | head -10", "description": "HANA version", "requires_sidadm": True},
    "hsr_state": {"script": "su - {sidadm} -c 'hdbnsutil -sr_state' 2>&1", "description": "HSR replication state", "requires_sidadm": True},
    "systemctl_cluster": {"script": "systemctl status pacemaker corosync sbd 2>&1 | head -30", "description": "Cluster service status", "requires_sidadm": False},
    "df_hana": {"script": "df -h /hana/data /hana/log /hana/shared /usr/sap 2>&1", "description": "HANA filesystem usage", "requires_sidadm": False},
    "free_mem": {"script": "free -h 2>&1", "description": "Memory usage", "requires_sidadm": False},
    "uptime": {"script": "uptime 2>&1", "description": "System uptime and load", "requires_sidadm": False},
    "os_release": {"script": "cat /etc/os-release 2>&1", "description": "OS version", "requires_sidadm": False},
    "sapcontrol_getinstancelist": {"script": "su - {sidadm} -c 'sapcontrol -nr {instance} -function GetSystemInstanceList' 2>&1", "description": "SAP instance list", "requires_sidadm": True},
    "landscape_host_config": {"script": "su - {sidadm} -c 'python /usr/sap/{sid}/HDB{instance}/exe/python_support/landscapeHostConfiguration.py' 2>&1 | head -20", "description": "HANA landscape config", "requires_sidadm": True},
}

SUB_ID = os.environ.get("SUBSCRIPTION_ID", "40050ff9-81f0-4654-9bd4-34551fe455df")

def get_mi_token():
    """Get ARM bearer token via DefaultAzureCredential (handles App Service MI, IMDS, CLI)."""
    token = get_credential().get_token("https://management.azure.com/.default")
    return token.token

def validate_caller(req):
    api_key = req.headers.get("x-api-key", "") or req.params.get("code", "")
    if not api_key:
        return False
    for key, value in os.environ.items():
        if key.startswith("AGENT_KEY_") and value == api_key:
            return True
    return False

def validate_input(vm, rg, sidadm, instance):
    if not re.match(r'^[a-zA-Z0-9\-_]{1,64}$', vm): return f"Invalid VM: {vm}"
    if not re.match(r'^[a-zA-Z0-9\-_]{1,90}$', rg): return f"Invalid RG: {rg}"
    if sidadm and not re.match(r'^[a-z][a-z0-9]{2}adm$', sidadm): return f"Invalid sidadm: {sidadm}"
    if instance and not re.match(r'^[0-9]{2}$', instance): return f"Invalid instance: {instance}"
    return ""

def parse_run_command_output(data):
    """Parse stdout/stderr from ARM runCommand response.
    Handles two formats:
    1. ComponentStatus format: separate items with StdOut/StdErr in code field
    2. ProvisioningState format: single item with [stdout]/[stderr] markers in message
    """
    stdout = stderr = ""
    for item in data.get("value", []):
        code = item.get("code", "")
        msg = item.get("message", "")
        if "StdOut" in code:
            stdout = msg
        elif "StdErr" in code:
            stderr = msg
        elif "ProvisioningState" in code and "[stdout]" in msg:
            # Parse embedded stdout/stderr from message
            parts = msg.split("[stdout]", 1)
            if len(parts) > 1:
                rest = parts[1]
                if "[stderr]" in rest:
                    stdout = rest.split("[stderr]", 1)[0].strip()
                    stderr = rest.split("[stderr]", 1)[1].strip()
                else:
                    stdout = rest.strip()
    # Fallback: check properties.output (async polling responses)
    if not stdout and "properties" in data:
        props = data["properties"]
        out = props.get("output", {})
        if isinstance(out, dict):
            return parse_run_command_output(out)
        elif isinstance(out, str):
            stdout = out
    return stdout, stderr

@app.route(route="commands", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def list_commands(req):
    if not validate_caller(req):
        return func.HttpResponse(json.dumps({"error": "Unauthorized"}), status_code=401, mimetype="application/json")
    cmds = {k: {"description": v["description"], "requires_sidadm": v["requires_sidadm"]} for k, v in ALLOWED_COMMANDS.items()}
    return func.HttpResponse(json.dumps({"commands": cmds, "count": len(cmds)}, indent=2), mimetype="application/json")

@app.route(route="diag", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def diagnostics(req):
    """Diagnostic endpoint to test MI token and ARM connectivity."""
    if not validate_caller(req):
        return func.HttpResponse(json.dumps({"error": "Unauthorized"}), status_code=401, mimetype="application/json")
    import time
    results = {}
    # Test 1: MI token
    try:
        t1 = time.time()
        token = get_mi_token()
        results["mi_token"] = {"status": "OK", "time_ms": int((time.time()-t1)*1000), "token_prefix": token[:20]+"..."}
    except Exception as e:
        results["mi_token"] = {"status": "FAIL", "error": str(e)}
        return func.HttpResponse(json.dumps(results, indent=2), mimetype="application/json")
    # Test 2: ARM reachability
    try:
        t2 = time.time()
        r = requests.get(f"https://management.azure.com/subscriptions/{SUB_ID}?api-version=2022-12-01",
            headers={"Authorization": f"Bearer {token}"}, timeout=30)
        results["arm_api"] = {"status": "OK" if r.status_code == 200 else f"HTTP {r.status_code}", "time_ms": int((time.time()-t2)*1000)}
    except Exception as e:
        results["arm_api"] = {"status": "FAIL", "error": str(e)}
    # Test 3: VM existence
    try:
        vm = req.params.get("vm", "AB1vm")
        rg = req.params.get("rg", "RG_SAP_CUS_AB1")
        t3 = time.time()
        r = requests.get(f"https://management.azure.com/subscriptions/{SUB_ID}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{vm}?api-version=2024-03-01",
            headers={"Authorization": f"Bearer {token}"}, timeout=30)
        results["vm_check"] = {"status": "OK" if r.status_code == 200 else f"HTTP {r.status_code}", "time_ms": int((time.time()-t3)*1000), "vm": vm}
    except Exception as e:
        results["vm_check"] = {"status": "FAIL", "error": str(e)}
    return func.HttpResponse(json.dumps(results, indent=2), mimetype="application/json")

@app.route(route="command", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def execute_command(req):
    if not validate_caller(req):
        return func.HttpResponse(json.dumps({"error": "Unauthorized"}), status_code=401, mimetype="application/json")
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON"}), status_code=400, mimetype="application/json")

    vm, rg = body.get("vm", "").strip(), body.get("rg", "").strip()
    command_id = body.get("command_id", "").strip()
    sidadm, instance = body.get("sidadm", "").strip(), body.get("instance", "00").strip()
    sid = body.get("sid", "").strip().upper()

    if command_id not in ALLOWED_COMMANDS:
        return func.HttpResponse(json.dumps({"error": f"Unknown: {command_id}", "available": list(ALLOWED_COMMANDS.keys())}), status_code=400, mimetype="application/json")
    if not vm or not rg:
        return func.HttpResponse(json.dumps({"error": "Missing vm or rg"}), status_code=400, mimetype="application/json")

    cmd = ALLOWED_COMMANDS[command_id]
    if cmd["requires_sidadm"] and not sidadm:
        return func.HttpResponse(json.dumps({"error": f"'{command_id}' requires sidadm"}), status_code=400, mimetype="application/json")

    err = validate_input(vm, rg, sidadm, instance)
    if err:
        return func.HttpResponse(json.dumps({"error": err}), status_code=400, mimetype="application/json")

    script = cmd["script"].replace("{sidadm}", sidadm).replace("{instance}", instance).replace("{sid}", sid)

    try:
        import time
        token = get_mi_token()
        vm_id = f"/subscriptions/{SUB_ID}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{vm}"
        url = f"https://management.azure.com{vm_id}/runCommand?api-version=2024-03-01"

        resp = requests.post(url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"commandId": "RunShellScript", "script": [script]}, timeout=180)

        logging.info(f"runCommand initial response: {resp.status_code}")

        # Handle async 202 response with polling
        if resp.status_code == 202:
            location = resp.headers.get("Location") or resp.headers.get("Azure-AsyncOperation")
            if not location:
                return func.HttpResponse(json.dumps({"error": "202 but no Location header", "headers": dict(resp.headers)}),
                    status_code=502, mimetype="application/json")

            for attempt in range(30):  # Poll up to 150 seconds
                time.sleep(5)
                poll = requests.get(location, headers={"Authorization": f"Bearer {token}"}, timeout=30)
                logging.info(f"Poll attempt {attempt}: status={poll.status_code}")

                if poll.status_code == 200:
                    data = poll.json()
                    status = data.get("status", data.get("provisioningState", "Succeeded"))
                    if status.lower() in ("succeeded", ""):
                        stdout, stderr = parse_run_command_output(data)
                        return func.HttpResponse(json.dumps({
                            "vm": vm, "rg": rg, "command_id": command_id, "description": cmd["description"],
                            "stdout": stdout, "stderr": stderr if stderr and stderr.strip() else None
                        }), mimetype="application/json")
                    elif status.lower() == "failed":
                        return func.HttpResponse(json.dumps({"error": f"Run-command failed", "detail": str(data)[:500]}),
                            status_code=502, mimetype="application/json")
                elif poll.status_code != 202:
                    return func.HttpResponse(json.dumps({"error": f"Poll returned {poll.status_code}"}),
                        status_code=502, mimetype="application/json")

            return func.HttpResponse(json.dumps({"error": "Run-command timed out after 150s polling"}),
                status_code=504, mimetype="application/json")

        elif resp.status_code == 200:
            stdout, stderr = parse_run_command_output(resp.json())
            return func.HttpResponse(json.dumps({
                "vm": vm, "rg": rg, "command_id": command_id, "description": cmd["description"],
                "stdout": stdout, "stderr": stderr if stderr and stderr.strip() else None
            }), mimetype="application/json")
        else:
            return func.HttpResponse(json.dumps({"error": f"Run-command failed: {resp.status_code}", "detail": resp.text[:500]}),
                status_code=502, mimetype="application/json")
    except requests.exceptions.Timeout:
        return func.HttpResponse(json.dumps({"error": "Timeout — VM Run Command took too long (>180s). The VM may be unresponsive or under heavy load."}), status_code=504, mimetype="application/json")
    except Exception as e:
        logging.error(f"Command execution error: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")
