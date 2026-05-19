"""SAP SRE Agent Proxy — unified config + command proxy (Container App edition).

Config endpoints:  read SAP config files and landscape inventory from blob storage.
Command endpoints: execute pre-approved commands on SAP VMs via Azure Run Command API.
"""
import json
import logging
import os
import re
import shlex
import time as _time

import requests as http_requests
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

app = FastAPI(title="SAP SRE Agent Proxy", docs_url=None, redoc_url=None)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Shared credential ──────────────────────────────────────────────────────
_credential = None

def get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential

# ─── Auth ────────────────────────────────────────────────────────────────────
def validate_caller(req: Request):
    """Validate caller identity. Supports two methods:
    1. Entra ID (primary): Easy Auth validates the bearer token at the infrastructure level
       and injects X-MS-CLIENT-PRINCIPAL-ID header. No token validation needed in code.
    2. API Key (fallback): x-api-key header matched against AGENT_KEY_* env vars.
    """
    # Method 1: Entra ID — Easy Auth already validated the token
    principal_id = req.headers.get("x-ms-client-principal-id", "")
    if principal_id:
        caller = req.headers.get("x-ms-client-principal-name", principal_id)
        logger.info(json.dumps({"event": "auth", "method": "entra_id", "principal": principal_id}))
        return True, caller

    # Method 2: API Key fallback
    api_key = req.headers.get("x-api-key", "") or req.query_params.get("code", "")
    if not api_key:
        return False, None
    for key, value in os.environ.items():
        if key.startswith("AGENT_KEY_") and value == api_key:
            logger.info(json.dumps({"event": "auth", "method": "api_key", "key_name": key}))
            return True, key.replace("AGENT_KEY_", "")
    return False, None

def require_auth(req: Request):
    valid, _ = validate_caller(req)
    if not valid:
        return JSONResponse(
            {"error": "Unauthorized. Provide Entra ID bearer token or valid x-api-key header."},
            status_code=401)
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

# Lazy blob client
_blob_service = None
_container_client = None

def get_container_client():
    global _blob_service, _container_client
    if _container_client is None:
        if not STORAGE_ACCOUNT:
            raise ValueError("STORAGE_ACCOUNT_NAME env var is required for config endpoints.")
        _blob_service = BlobServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
            credential=get_credential())
        _container_client = _blob_service.get_container_client(CONTAINER)
    return _container_client


@app.get("/api/registry")
def get_registry(req: Request):
    """Return the SAP landscape inventory JSON."""
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    try:
        cached = cache_get("registry")
        if cached:
            logger.info(json.dumps({"event": "registry_read", "source": "cache"}))
            return Response(content=cached, media_type="application/json")
        cc = get_container_client()
        data = cc.get_blob_client("sap-landscape-inventory.json").download_blob().readall().decode("utf-8")
        cache_set("registry", data)
        logger.info(json.dumps({"event": "registry_read", "source": "blob"}))
        return Response(content=data, media_type="application/json")
    except Exception as e:
        logger.error(f"Failed to read registry: {e}")
        return JSONResponse({"error": "Failed to read registry"}, status_code=500)


@app.get("/api/config/{sid}/{hostname}/{filepath:path}")
def get_config_file(sid: str, hostname: str, filepath: str, req: Request):
    """Return a single config file for a given SID/hostname/filepath."""
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    if not all([sid, hostname, filepath]):
        return JSONResponse({"error": "Missing sid, hostname, or filepath"}, status_code=400)
    if not re.match(r'^[a-zA-Z0-9_.-]+$', sid) or not re.match(r'^[a-zA-Z0-9_.-]+$', hostname):
        return JSONResponse({"error": "Invalid sid or hostname"}, status_code=400)
    if '..' in filepath or filepath.startswith('/'):
        return JSONResponse({"error": "Invalid filepath"}, status_code=400)
    if not re.match(r'^[a-zA-Z0-9/_.\-]+$', filepath):
        return JSONResponse({"error": "Invalid filepath characters"}, status_code=400)
    blob_path = f"{sid}/{hostname}/latest/{filepath}"
    try:
        cc = get_container_client()
        data = cc.get_blob_client(blob_path).download_blob().readall().decode("utf-8")
        return PlainTextResponse(data)
    except Exception as e:
        if "BlobNotFound" in str(e) or "404" in str(e):
            return PlainTextResponse("", status_code=404)
        logger.error(f"Failed to read {blob_path}: {e}")
        return JSONResponse({"error": "Failed to read config file"}, status_code=500)


@app.get("/api/configs/{sid}/{hostname}")
def get_all_configs(sid: str, hostname: str, req: Request):
    """Return ALL config files for a SID/hostname as a single JSON bundle."""
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    if not all([sid, hostname]):
        return JSONResponse({"error": "Missing sid or hostname"}, status_code=400)
    prefix = f"{sid}/{hostname}/latest/"
    try:
        cache_key = f"configs:{sid}:{hostname}"
        cached = cache_get(cache_key)
        if cached:
            logger.info(json.dumps({"event": "configs_read", "sid": sid, "hostname": hostname, "source": "cache"}))
            return Response(content=cached, media_type="application/json")
        cc = get_container_client()
        files = {}
        for blob in cc.list_blobs(name_starts_with=prefix):
            if blob.name.endswith("/") or "manifest.json" in blob.name:
                continue
            rel_path = blob.name[len(prefix):]
            try:
                files[rel_path] = cc.get_blob_client(blob.name).download_blob().readall().decode("utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"Failed to read {blob.name}: {e}")
                files[rel_path] = None
        result_json = json.dumps({"sid": sid, "hostname": hostname, "file_count": len(files), "files": files})
        cache_set(cache_key, result_json)
        logger.info(json.dumps({"event": "configs_read", "sid": sid, "hostname": hostname, "source": "blob", "file_count": len(files)}))
        return Response(content=result_json, media_type="application/json")
    except Exception as e:
        logger.error(f"Failed to list/read configs for {sid}/{hostname}: {e}")
        return JSONResponse({"error": "Failed to read configs"}, status_code=500)


@app.get("/api/configs/{sid}")
def get_system_configs(sid: str, req: Request):
    """Return ALL config files for ALL VMs in a SID as a single JSON bundle."""
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    if not sid:
        return JSONResponse({"error": "Missing sid"}, status_code=400)
    prefix = f"{sid}/"
    try:
        cache_key = f"system_configs:{sid}"
        cached = cache_get(cache_key)
        if cached:
            logger.info(json.dumps({"event": "system_configs_read", "sid": sid, "source": "cache"}))
            return Response(content=cached, media_type="application/json")
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
                logger.warning(f"Failed to read {blob.name}: {e}")
                vms[hostname][rel_path] = None
        result = {"sid": sid, "vm_count": len(vms), "vms": {h: {"file_count": len(f), "files": f} for h, f in vms.items()}}
        result_json = json.dumps(result)
        cache_set(cache_key, result_json)
        logger.info(json.dumps({"event": "system_configs_read", "sid": sid, "source": "blob", "vm_count": len(vms)}))
        return Response(content=result_json, media_type="application/json")
    except Exception as e:
        logger.error(f"Failed to list/read configs for {sid}: {e}")
        return JSONResponse({"error": "Failed to read system configs"}, status_code=500)


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
# Idempotent: safe to re-run if /opt/sre already exists
if [ -d /opt/sre ]; then
    echo "NOTICE: /opt/sre exists — updating scripts and config (logs preserved)"
fi
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


@app.get("/api/commands")
def list_commands(req: Request):
    valid, caller = validate_caller(req)
    if not valid:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    cmds = {k: {"description": v["description"], "requires_sidadm": v["requires_sidadm"]} for k, v in ALLOWED_COMMANDS.items()}
    return JSONResponse({"commands": cmds, "count": len(cmds)})


@app.get("/api/diag")
def diagnostics(req: Request):
    """Diagnostic endpoint to test MI token and ARM connectivity."""
    valid, caller = validate_caller(req)
    if not valid:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    results = {}
    try:
        t1 = _time.time()
        token = get_mi_token()
        results["mi_token"] = {"status": "OK", "time_ms": int((_time.time()-t1)*1000), "token_prefix": token[:20]+"..."}
    except Exception as e:
        results["mi_token"] = {"status": "FAIL", "error": str(e)}
        return JSONResponse(results)
    try:
        t2 = _time.time()
        r = http_requests.get(f"https://management.azure.com/subscriptions/{SUB_ID}?api-version=2022-12-01",
            headers={"Authorization": f"Bearer {token}"}, timeout=30)
        results["arm_api"] = {"status": "OK" if r.status_code == 200 else f"HTTP {r.status_code}", "time_ms": int((_time.time()-t2)*1000)}
    except Exception as e:
        results["arm_api"] = {"status": "FAIL", "error": str(e)}
    try:
        vm = req.query_params.get("vm", "")
        rg = req.query_params.get("rg", "")
        if not vm or not rg:
            results["vm_check"] = {"status": "SKIP", "error": "Provide ?vm=<name>&rg=<rg> to test VM access"}
        else:
            t3 = _time.time()
            r = http_requests.get(f"https://management.azure.com/subscriptions/{SUB_ID}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{vm}?api-version=2024-03-01",
                headers={"Authorization": f"Bearer {token}"}, timeout=30)
            results["vm_check"] = {"status": "OK" if r.status_code == 200 else f"HTTP {r.status_code}", "time_ms": int((_time.time()-t3)*1000), "vm": vm}
    except Exception as e:
        results["vm_check"] = {"status": "FAIL", "error": str(e)}
    return JSONResponse(results)


@app.post("/api/command")
async def execute_command(req: Request):
    valid, caller = validate_caller(req)
    if not valid:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await req.json()
    except ValueError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    vm, rg = body.get("vm", "").strip(), body.get("rg", "").strip()
    command_id = body.get("command_id", "").strip()
    sidadm, instance = body.get("sidadm", "").strip(), body.get("instance", "00").strip()
    sid = body.get("sid", "").strip().upper()
    sub_id = body.get("subscription_id", "").strip() or SUB_ID

    if not sub_id:
        return JSONResponse({"error": "Missing subscription_id in request body or SUBSCRIPTION_ID env var"}, status_code=400)
    if command_id not in ALLOWED_COMMANDS:
        return JSONResponse({"error": f"Unknown: {command_id}", "available": list(ALLOWED_COMMANDS.keys())}, status_code=400)
    if not vm or not rg:
        return JSONResponse({"error": "Missing vm or rg"}, status_code=400)

    cmd = ALLOWED_COMMANDS[command_id]
    if cmd["requires_sidadm"] and not sidadm:
        return JSONResponse({"error": f"'{command_id}' requires sidadm"}, status_code=400)

    err = validate_input(vm, rg, sidadm, instance)
    if err:
        return JSONResponse({"error": err}, status_code=400)

    if command_id == "deploy_collector":
        script, build_err = build_deploy_collector_script(body)
        if build_err:
            return JSONResponse({"error": build_err}, status_code=400)
    else:
        script = cmd["script"].replace("{sidadm}", shlex.quote(sidadm)).replace("{instance}", shlex.quote(instance)).replace("{sid}", shlex.quote(sid))

    logger.info(json.dumps({"event": "command_execute", "caller": caller, "command_id": command_id, "vm": vm, "rg": rg, "sid": sid, "sidadm": sidadm}))

    try:
        token = get_mi_token()
        url = f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{vm}/runCommand?api-version=2024-03-01"
        resp = http_requests.post(url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"commandId": "RunShellScript", "script": [script]}, timeout=180)

        logger.info(f"runCommand initial response: {resp.status_code}")

        if resp.status_code == 202:
            location = resp.headers.get("Location") or resp.headers.get("Azure-AsyncOperation")
            if not location:
                return JSONResponse({"error": "202 but no Location header", "headers": dict(resp.headers)}, status_code=502)
            for attempt in range(30):
                _time.sleep(5)
                poll = http_requests.get(location, headers={"Authorization": f"Bearer {token}"}, timeout=30)
                logger.info(f"Poll attempt {attempt}: status={poll.status_code}")
                if poll.status_code == 200:
                    data = poll.json()
                    status = data.get("status", data.get("provisioningState", "Succeeded"))
                    if status.lower() in ("succeeded", ""):
                        stdout, stderr = parse_run_command_output(data)
                        logger.info(json.dumps({"event": "command_result", "caller": caller, "command_id": command_id, "vm": vm, "status": "success", "stdout_len": len(stdout)}))
                        return JSONResponse({"vm": vm, "rg": rg, "command_id": command_id, "description": cmd["description"], "stdout": stdout, "stderr": stderr if stderr and stderr.strip() else None})
                    elif status.lower() == "failed":
                        return JSONResponse({"error": "Run-command failed", "detail": str(data)[:500]}, status_code=502)
                elif poll.status_code != 202:
                    return JSONResponse({"error": f"Poll returned {poll.status_code}"}, status_code=502)
            return JSONResponse({"error": "Run-command timed out after 150s polling"}, status_code=504)

        elif resp.status_code == 200:
            stdout, stderr = parse_run_command_output(resp.json())
            logger.info(json.dumps({"event": "command_result", "caller": caller, "command_id": command_id, "vm": vm, "status": "success", "stdout_len": len(stdout)}))
            return JSONResponse({"vm": vm, "rg": rg, "command_id": command_id, "description": cmd["description"], "stdout": stdout, "stderr": stderr if stderr and stderr.strip() else None})
        else:
            return JSONResponse({"error": f"Run-command failed: {resp.status_code}", "detail": resp.text[:500]}, status_code=502)

    except http_requests.exceptions.Timeout:
        logger.warning(json.dumps({"event": "command_result", "caller": caller, "command_id": command_id, "vm": vm, "status": "timeout"}))
        return JSONResponse({"error": "Timeout — VM Run Command took too long (>180s). The VM may be unresponsive or under heavy load."}, status_code=504)
    except Exception as e:
        logger.error(json.dumps({"event": "command_result", "caller": caller, "command_id": command_id, "vm": vm, "status": "error", "error": str(e)}))
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/health")
def health():
    """Health check endpoint for Container Apps probes."""
    return JSONResponse({"status": "healthy"})
