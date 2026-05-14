"""SAP SRE Agent Proxy — unified config + command proxy.

Config endpoints:  read SAP config files and landscape inventory from blob storage.
Command endpoints: execute pre-approved commands on SAP VMs via Azure Run Command API.
"""
import azure.functions as func
import logging
import json
import os
import re
import shlex
import time as _time
import requests
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()

# ─── Shared credential ──────────────────────────────────────────────────────
_credential = None
def get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential

# ─── Auth ────────────────────────────────────────────────────────────────────
def validate_caller(req):
    """Validate using x-api-key header against AGENT_KEY_* app settings."""
    api_key = req.headers.get("x-api-key", "") or req.params.get("code", "")
    if not api_key:
        return False, None
    for key, value in os.environ.items():
        if key.startswith("AGENT_KEY_") and value == api_key:
            return True, key.replace("AGENT_KEY_", "")
    return False, None

def require_auth(req):
    valid, _ = validate_caller(req)
    if not valid:
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized. Provide valid x-api-key header or code parameter."}),
            status_code=401, mimetype="application/json")
    return None

# ═══════════════════════════════════════════════════════════════════════════
# CONFIG PROXY — blob storage endpoints
# ═══════════════════════════════════════════════════════════════════════════

STORAGE_ACCOUNT = os.environ.get("STORAGE_ACCOUNT_NAME", "")
CONTAINER = os.environ.get("CONTAINER_NAME", "sap-configs")

# In-memory response cache (1-hour TTL)
_cache = {}
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "3600"))

def cache_get(key):
    if key in _cache and (_time.time() - _cache[key][0]) < CACHE_TTL:
        return _cache[key][1]
    return None

def cache_set(key, data):
    _cache[key] = (_time.time(), data)

# Lazy blob client — only initialized when config endpoints are called
_blob_service = None
_container_client = None

def get_container_client():
    global _blob_service, _container_client
    if _container_client is None:
        if not STORAGE_ACCOUNT:
            raise ValueError("STORAGE_ACCOUNT_NAME app setting is required for config endpoints.")
        _blob_service = BlobServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
            credential=get_credential())
        _container_client = _blob_service.get_container_client(CONTAINER)
    return _container_client


@app.route(route="registry", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_registry(req: func.HttpRequest) -> func.HttpResponse:
    """Return the SAP landscape inventory JSON."""
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    try:
        cached = cache_get("registry")
        if cached:
            logging.info(json.dumps({"event": "registry_read", "source": "cache"}))
            return func.HttpResponse(cached, mimetype="application/json", status_code=200)
        cc = get_container_client()
        data = cc.get_blob_client("sap-landscape-inventory.json").download_blob().readall().decode("utf-8")
        cache_set("registry", data)
        logging.info(json.dumps({"event": "registry_read", "source": "blob"}))
        return func.HttpResponse(data, mimetype="application/json", status_code=200)
    except Exception as e:
        logging.error(f"Failed to read registry: {e}")
        return func.HttpResponse(json.dumps({"error": "Failed to read registry"}), status_code=500, mimetype="application/json")


@app.route(route="config/{sid}/{hostname}/{*filepath}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_config_file(req: func.HttpRequest) -> func.HttpResponse:
    """Return a single config file for a given SID/hostname/filepath."""
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    sid = req.route_params.get("sid", "")
    hostname = req.route_params.get("hostname", "")
    filepath = req.route_params.get("filepath", "")
    if not all([sid, hostname, filepath]):
        return func.HttpResponse(json.dumps({"error": "Missing sid, hostname, or filepath"}), status_code=400, mimetype="application/json")
    if not re.match(r'^[a-zA-Z0-9_.-]+$', sid) or not re.match(r'^[a-zA-Z0-9_.-]+$', hostname):
        return func.HttpResponse(json.dumps({"error": "Invalid sid or hostname"}), status_code=400, mimetype="application/json")
    if '..' in filepath or filepath.startswith('/'):
        return func.HttpResponse(json.dumps({"error": "Invalid filepath"}), status_code=400, mimetype="application/json")
    if not re.match(r'^[a-zA-Z0-9/_.\-]+$', filepath):
        return func.HttpResponse(json.dumps({"error": "Invalid filepath characters"}), status_code=400, mimetype="application/json")
    blob_path = f"{sid}/{hostname}/latest/{filepath}"
    try:
        cc = get_container_client()
        data = cc.get_blob_client(blob_path).download_blob().readall().decode("utf-8")
        return func.HttpResponse(data, mimetype="text/plain", status_code=200)
    except Exception as e:
        if "BlobNotFound" in str(e) or "404" in str(e):
            return func.HttpResponse("", status_code=404)
        logging.error(f"Failed to read {blob_path}: {e}")
        return func.HttpResponse(json.dumps({"error": "Failed to read config file"}), status_code=500, mimetype="application/json")


@app.route(route="configs/{sid}/{hostname}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_all_configs(req: func.HttpRequest) -> func.HttpResponse:
    """Return ALL config files for a SID/hostname as a single JSON bundle."""
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    sid = req.route_params.get("sid", "")
    hostname = req.route_params.get("hostname", "")
    if not all([sid, hostname]):
        return func.HttpResponse(json.dumps({"error": "Missing sid or hostname"}), status_code=400, mimetype="application/json")
    prefix = f"{sid}/{hostname}/latest/"
    try:
        cache_key = f"configs:{sid}:{hostname}"
        cached = cache_get(cache_key)
        if cached:
            logging.info(json.dumps({"event": "configs_read", "sid": sid, "hostname": hostname, "source": "cache"}))
            return func.HttpResponse(cached, mimetype="application/json", status_code=200)
        cc = get_container_client()
        files = {}
        for blob in cc.list_blobs(name_starts_with=prefix):
            if blob.name.endswith("/") or "manifest.json" in blob.name:
                continue
            rel_path = blob.name[len(prefix):]
            try:
                files[rel_path] = cc.get_blob_client(blob.name).download_blob().readall().decode("utf-8", errors="replace")
            except Exception as e:
                logging.warning(f"Failed to read {blob.name}: {e}")
                files[rel_path] = None
        result_json = json.dumps({"sid": sid, "hostname": hostname, "file_count": len(files), "files": files})
        cache_set(cache_key, result_json)
        logging.info(json.dumps({"event": "configs_read", "sid": sid, "hostname": hostname, "source": "blob", "file_count": len(files)}))
        return func.HttpResponse(result_json, mimetype="application/json", status_code=200)
    except Exception as e:
        logging.error(f"Failed to list/read configs for {sid}/{hostname}: {e}")
        return func.HttpResponse(json.dumps({"error": "Failed to read configs"}), status_code=500, mimetype="application/json")


@app.route(route="configs/{sid}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_system_configs(req: func.HttpRequest) -> func.HttpResponse:
    """Return ALL config files for ALL VMs in a SID as a single JSON bundle."""
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    sid = req.route_params.get("sid", "")
    if not sid:
        return func.HttpResponse(json.dumps({"error": "Missing sid"}), status_code=400, mimetype="application/json")
    prefix = f"{sid}/"
    try:
        cache_key = f"system_configs:{sid}"
        cached = cache_get(cache_key)
        if cached:
            logging.info(json.dumps({"event": "system_configs_read", "sid": sid, "source": "cache"}))
            return func.HttpResponse(cached, mimetype="application/json", status_code=200)
        cc = get_container_client()
        vms = {}
        for blob in cc.list_blobs(name_starts_with=prefix):
            parts = blob.name.split("/")
            if len(parts) < 4 or parts[2] != "latest":
                continue
            hostname = parts[1]
            rel_path = "/".join(parts[3:])
            if "manifest.json" in rel_path or blob.name.endswith("/"):
                continue
            if hostname not in vms:
                vms[hostname] = {}
            try:
                vms[hostname][rel_path] = cc.get_blob_client(blob.name).download_blob().readall().decode("utf-8", errors="replace")
            except Exception as e:
                logging.warning(f"Failed to read {blob.name}: {e}")
                vms[hostname][rel_path] = None
        result = {"sid": sid, "vm_count": len(vms), "vms": {h: {"file_count": len(f), "files": f} for h, f in vms.items()}}
        result_json = json.dumps(result)
        cache_set(cache_key, result_json)
        logging.info(json.dumps({"event": "system_configs_read", "sid": sid, "source": "blob", "vm_count": len(vms)}))
        return func.HttpResponse(result_json, mimetype="application/json", status_code=200)
    except Exception as e:
        logging.error(f"Failed to list/read configs for {sid}: {e}")
        return func.HttpResponse(json.dumps({"error": "Failed to read system configs"}), status_code=500, mimetype="application/json")


# ═══════════════════════════════════════════════════════════════════════════
# COMMAND PROXY — VM run-command endpoints
# ═══════════════════════════════════════════════════════════════════════════

SUB_ID = os.environ.get("SUBSCRIPTION_ID", "")

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
    "deploy_collector": {"script": "__SPECIAL__", "description": "Deploy config collector script and cron job to this VM (one-time setup)", "requires_sidadm": False},
}


def get_mi_token():
    """Get ARM bearer token via DefaultAzureCredential."""
    return get_credential().get_token("https://management.azure.com/.default").token


def validate_input(vm, rg, sidadm, instance):
    if not re.match(r'^[a-zA-Z0-9\-_]{1,64}$', vm): return f"Invalid VM: {vm}"
    if not re.match(r'^[a-zA-Z0-9\-_]{1,90}$', rg): return f"Invalid RG: {rg}"
    if sidadm and not re.match(r'^[a-z][a-z0-9]{2}adm$', sidadm): return f"Invalid sidadm: {sidadm}"
    if instance and not re.match(r'^[0-9]{2}$', instance): return f"Invalid instance: {instance}"
    return ""


def build_deploy_collector_script(body):
    """Build shell script to deploy config collector + cron job to a SAP VM."""
    storage = body.get("storage_account", "").strip()
    umi_cid = body.get("umi_client_id", "").strip()
    sid = body.get("sid", "").strip().upper()
    db_sid = body.get("db_sid", "").strip().upper() or sid
    roles = body.get("roles", "").strip()
    hana_inst = body.get("hana_inst", "").strip()
    ascs_inst = body.get("ascs_inst", "").strip()
    app_inst = body.get("app_inst", "").strip()
    container = body.get("container", "sap-configs").strip()

    if not all([storage, umi_cid, roles]):
        return None, "deploy_collector requires: storage_account, umi_client_id, roles"
    if roles != "sbd" and not sid:
        return None, "deploy_collector requires: sid (unless roles=sbd)"
    for name, val in [("storage_account", storage), ("umi_client_id", umi_cid), ("sid", sid), ("roles", roles)]:
        if val and not re.match(r'^[a-zA-Z0-9_,\-]+$', val):
            return None, f"Invalid characters in {name}"

    cron_args = f"--sid {shlex.quote(sid)} --db-sid {shlex.quote(db_sid)} --roles {shlex.quote(roles)}"
    if hana_inst: cron_args += f" --hana-inst {shlex.quote(hana_inst)}"
    if ascs_inst: cron_args += f" --ascs-inst {shlex.quote(ascs_inst)}"
    if app_inst: cron_args += f" --app-inst {shlex.quote(app_inst)}"

    script = f"""#!/bin/bash
set -euo pipefail
echo "Creating /opt/sre directory..."
mkdir -p /opt/sre
echo "Downloading collector script..."
az login --identity --username {shlex.quote(umi_cid)} --output none 2>/dev/null
az storage blob download \\
    --account-name {shlex.quote(storage)} \\
    --container-name {shlex.quote(container)} \\
    --name scripts/collect-sap-configs.sh \\
    --file /opt/sre/collect-sap-configs.sh \\
    --auth-mode login --output none 2>/dev/null
chmod +x /opt/sre/collect-sap-configs.sh
cat > /opt/sre/sre.env << 'ENVEOF'
SRE_STORAGE_ACCOUNT="{storage}"
SRE_CONTAINER="{container}"
SRE_UMI_CLIENT_ID="{umi_cid}"
ENVEOF
chmod 600 /opt/sre/sre.env
cat > /opt/sre/run-collector.sh << 'CRONEOF'
#!/bin/bash
source /opt/sre/sre.env
/opt/sre/collect-sap-configs.sh {cron_args} >> /var/log/sre-config-collect.log 2>&1
CRONEOF
chmod +x /opt/sre/run-collector.sh
echo "0 2 * * 0 root /opt/sre/run-collector.sh" > /etc/cron.d/sre-collector
chmod 644 /etc/cron.d/sre-collector
cat > /etc/logrotate.d/sre-config-collect << 'LREOF'
/var/log/sre-config-collect.log {{
    weekly
    rotate 12
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root root
}}
LREOF
echo "SUCCESS: Collector deployed. Cron set for Sunday 2:00 AM."
echo "Files: /opt/sre/collect-sap-configs.sh, /opt/sre/sre.env, /opt/sre/run-collector.sh"
echo "Cron: /etc/cron.d/sre-collector | Log: /var/log/sre-config-collect.log"
"""
    return script, None


def parse_run_command_output(data):
    """Parse stdout/stderr from ARM runCommand response."""
    stdout = stderr = ""
    for item in data.get("value", []):
        code = item.get("code", "")
        msg = item.get("message", "")
        if "StdOut" in code:
            stdout = msg
        elif "StdErr" in code:
            stderr = msg
        elif "ProvisioningState" in code and "[stdout]" in msg:
            parts = msg.split("[stdout]", 1)
            if len(parts) > 1:
                rest = parts[1]
                if "[stderr]" in rest:
                    stdout = rest.split("[stderr]", 1)[0].strip()
                    stderr = rest.split("[stderr]", 1)[1].strip()
                else:
                    stdout = rest.strip()
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
    valid, caller = validate_caller(req)
    if not valid:
        return func.HttpResponse(json.dumps({"error": "Unauthorized"}), status_code=401, mimetype="application/json")
    cmds = {k: {"description": v["description"], "requires_sidadm": v["requires_sidadm"]} for k, v in ALLOWED_COMMANDS.items()}
    return func.HttpResponse(json.dumps({"commands": cmds, "count": len(cmds)}, indent=2), mimetype="application/json")


@app.route(route="diag", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def diagnostics(req):
    """Diagnostic endpoint to test MI token and ARM connectivity."""
    valid, caller = validate_caller(req)
    if not valid:
        return func.HttpResponse(json.dumps({"error": "Unauthorized"}), status_code=401, mimetype="application/json")
    results = {}
    try:
        t1 = _time.time()
        token = get_mi_token()
        results["mi_token"] = {"status": "OK", "time_ms": int((_time.time()-t1)*1000), "token_prefix": token[:20]+"..."}
    except Exception as e:
        results["mi_token"] = {"status": "FAIL", "error": str(e)}
        return func.HttpResponse(json.dumps(results, indent=2), mimetype="application/json")
    try:
        t2 = _time.time()
        r = requests.get(f"https://management.azure.com/subscriptions/{SUB_ID}?api-version=2022-12-01",
            headers={"Authorization": f"Bearer {token}"}, timeout=30)
        results["arm_api"] = {"status": "OK" if r.status_code == 200 else f"HTTP {r.status_code}", "time_ms": int((_time.time()-t2)*1000)}
    except Exception as e:
        results["arm_api"] = {"status": "FAIL", "error": str(e)}
    try:
        vm = req.params.get("vm", "")
        rg = req.params.get("rg", "")
        if not vm or not rg:
            results["vm_check"] = {"status": "SKIP", "error": "Provide ?vm=<name>&rg=<rg> to test VM access"}
        else:
            t3 = _time.time()
            r = requests.get(f"https://management.azure.com/subscriptions/{SUB_ID}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{vm}?api-version=2024-03-01",
                headers={"Authorization": f"Bearer {token}"}, timeout=30)
            results["vm_check"] = {"status": "OK" if r.status_code == 200 else f"HTTP {r.status_code}", "time_ms": int((_time.time()-t3)*1000), "vm": vm}
    except Exception as e:
        results["vm_check"] = {"status": "FAIL", "error": str(e)}
    return func.HttpResponse(json.dumps(results, indent=2), mimetype="application/json")


@app.route(route="command", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def execute_command(req):
    valid, caller = validate_caller(req)
    if not valid:
        return func.HttpResponse(json.dumps({"error": "Unauthorized"}), status_code=401, mimetype="application/json")
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON"}), status_code=400, mimetype="application/json")

    vm, rg = body.get("vm", "").strip(), body.get("rg", "").strip()
    command_id = body.get("command_id", "").strip()
    sidadm, instance = body.get("sidadm", "").strip(), body.get("instance", "00").strip()
    sid = body.get("sid", "").strip().upper()
    sub_id = body.get("subscription_id", "").strip() or SUB_ID

    if not sub_id:
        return func.HttpResponse(json.dumps({"error": "Missing subscription_id in request body or SUBSCRIPTION_ID app setting"}), status_code=400, mimetype="application/json")
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

    if command_id == "deploy_collector":
        script, build_err = build_deploy_collector_script(body)
        if build_err:
            return func.HttpResponse(json.dumps({"error": build_err}), status_code=400, mimetype="application/json")
    else:
        script = cmd["script"].replace("{sidadm}", shlex.quote(sidadm)).replace("{instance}", shlex.quote(instance)).replace("{sid}", shlex.quote(sid))

    logging.info(json.dumps({"event": "command_execute", "caller": caller, "command_id": command_id, "vm": vm, "rg": rg, "sid": sid, "sidadm": sidadm}))

    try:
        token = get_mi_token()
        url = f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{vm}/runCommand?api-version=2024-03-01"
        resp = requests.post(url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"commandId": "RunShellScript", "script": [script]}, timeout=180)

        logging.info(f"runCommand initial response: {resp.status_code}")

        if resp.status_code == 202:
            location = resp.headers.get("Location") or resp.headers.get("Azure-AsyncOperation")
            if not location:
                return func.HttpResponse(json.dumps({"error": "202 but no Location header", "headers": dict(resp.headers)}), status_code=502, mimetype="application/json")
            for attempt in range(30):
                _time.sleep(5)
                poll = requests.get(location, headers={"Authorization": f"Bearer {token}"}, timeout=30)
                logging.info(f"Poll attempt {attempt}: status={poll.status_code}")
                if poll.status_code == 200:
                    data = poll.json()
                    status = data.get("status", data.get("provisioningState", "Succeeded"))
                    if status.lower() in ("succeeded", ""):
                        stdout, stderr = parse_run_command_output(data)
                        logging.info(json.dumps({"event": "command_result", "caller": caller, "command_id": command_id, "vm": vm, "status": "success", "stdout_len": len(stdout)}))
                        return func.HttpResponse(json.dumps({"vm": vm, "rg": rg, "command_id": command_id, "description": cmd["description"], "stdout": stdout, "stderr": stderr if stderr and stderr.strip() else None}), mimetype="application/json")
                    elif status.lower() == "failed":
                        return func.HttpResponse(json.dumps({"error": "Run-command failed", "detail": str(data)[:500]}), status_code=502, mimetype="application/json")
                elif poll.status_code != 202:
                    return func.HttpResponse(json.dumps({"error": f"Poll returned {poll.status_code}"}), status_code=502, mimetype="application/json")
            return func.HttpResponse(json.dumps({"error": "Run-command timed out after 150s polling"}), status_code=504, mimetype="application/json")

        elif resp.status_code == 200:
            stdout, stderr = parse_run_command_output(resp.json())
            logging.info(json.dumps({"event": "command_result", "caller": caller, "command_id": command_id, "vm": vm, "status": "success", "stdout_len": len(stdout)}))
            return func.HttpResponse(json.dumps({"vm": vm, "rg": rg, "command_id": command_id, "description": cmd["description"], "stdout": stdout, "stderr": stderr if stderr and stderr.strip() else None}), mimetype="application/json")
        else:
            return func.HttpResponse(json.dumps({"error": f"Run-command failed: {resp.status_code}", "detail": resp.text[:500]}), status_code=502, mimetype="application/json")

    except requests.exceptions.Timeout:
        logging.warning(json.dumps({"event": "command_result", "caller": caller, "command_id": command_id, "vm": vm, "status": "timeout"}))
        return func.HttpResponse(json.dumps({"error": "Timeout — VM Run Command took too long (>180s). The VM may be unresponsive or under heavy load."}), status_code=504, mimetype="application/json")
    except Exception as e:
        logging.error(json.dumps({"event": "command_result", "caller": caller, "command_id": command_id, "vm": vm, "status": "error", "error": str(e)}))
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")
