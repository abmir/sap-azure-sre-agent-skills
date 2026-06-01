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
import yaml
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


# ─── Embedded collector script (loaded at startup, base64-encoded for safe transport) ──
import base64 as _b64
_COLLECTOR_SCRIPT_B64 = ""

def _load_collector_script():
    """Load collector script at startup and base64-encode it for VM deployment."""
    global _COLLECTOR_SCRIPT_B64
    import pathlib
    script_path = pathlib.Path(__file__).parent.parent / "collector" / "collect-sap-configs.sh"
    if not script_path.exists():
        # Fallback: try same directory (Docker build may flatten structure)
        script_path = pathlib.Path(__file__).parent / "collect-sap-configs.sh"
    if script_path.exists():
        _COLLECTOR_SCRIPT_B64 = _b64.b64encode(script_path.read_bytes()).decode("ascii")
        logger.info(f"Collector script loaded: {len(_COLLECTOR_SCRIPT_B64)} bytes (base64)")
    else:
        logger.warning("Collector script not found — deploy_collector will fail")

_load_collector_script()


def build_deploy_collector_script(body):
    """Build shell script to deploy config collector + cron job to a SAP VM.
    The collector script is embedded inline (base64-encoded) — no blob download needed.
    This eliminates the storage firewall dependency for deployment."""
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
    if not _COLLECTOR_SCRIPT_B64:
        return None, "Collector script not loaded in proxy — rebuild the container image with collector/collect-sap-configs.sh"
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
mkdir -p /opt/sre/sap-configs
echo "Deploying collector script (embedded, no blob download needed)..."
echo '{_COLLECTOR_SCRIPT_B64}' | base64 -d > /opt/sre/collect-sap-configs.sh
chmod +x /opt/sre/collect-sap-configs.sh
cat > /opt/sre/sre.env << 'ENVEOF'
export SRE_STORAGE_ACCOUNT="{storage}"
export SRE_CONTAINER="{container}"
export SRE_UMI_CLIENT_ID="{umi_cid}"
ENVEOF
chmod 600 /opt/sre/sre.env
cat > /opt/sre/run-collector.sh << 'CRONEOF'
#!/bin/bash
source /opt/sre/sre.env
/opt/sre/collect-sap-configs.sh {cron_args} >> /opt/sre/collector.log 2>&1
CRONEOF
chmod +x /opt/sre/run-collector.sh
echo "0 2 * * 0 root /opt/sre/run-collector.sh" > /etc/cron.d/sre-collector
chmod 644 /etc/cron.d/sre-collector
cat > /etc/logrotate.d/sre-config-collect << 'LREOF'
/opt/sre/collector.log {{
    weekly
    rotate 12
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root root
}}
LREOF
echo "SUCCESS: Collector deployed (embedded script, no blob dependency)."
echo "Files: /opt/sre/collect-sap-configs.sh, /opt/sre/sre.env, /opt/sre/run-collector.sh"
echo "Configs: /opt/sre/sap-configs/{{SID}}/{{hostname}}/latest/"
echo "Log: /opt/sre/collector.log"
echo "Staging: /opt/sre/staging/ (temp, auto-cleaned)"
echo "Cron: /etc/cron.d/sre-collector"
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
        # Inputs already validated by validate_input() with strict regex.
        # Do NOT use shlex.quote() — it wraps values in single quotes which
        # breaks paths like /usr/sap/{sid}/HDB{instance}/HDB.
        script = cmd["script"].replace("{sidadm}", sidadm).replace("{instance}", instance).replace("{sid}", sid)

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


@app.post("/api/batch")
async def batch_commands(req: Request):
    """Execute multiple allowed commands on a single VM in one request.
    Body: {"vm": "AB1vm", "rg": "RG_SAP_CUS_AB1", "subscription_id": "...",
           "commands": [{"command_id": "uptime"}, {"command_id": "free_mem"},
                        {"command_id": "hdb_info", "sidadm": "db1adm", "sid": "DB1", "instance": "00"}]}
    Returns: {"vm": "...", "results": {"uptime": {"stdout": "..."}, "free_mem": {"stdout": "..."}, ...}}
    Max 6 commands per batch to limit execution time.
    """
    valid, caller = validate_caller(req)
    if not valid:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await req.json()
    except ValueError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    vm = body.get("vm", "").strip()
    rg = body.get("rg", "").strip()
    sub_id = body.get("subscription_id", "").strip() or SUB_ID
    commands = body.get("commands", [])

    if not vm or not rg:
        return JSONResponse({"error": "Missing vm or rg"}, status_code=400)
    if not commands or not isinstance(commands, list):
        return JSONResponse({"error": "Missing or invalid commands array"}, status_code=400)
    if len(commands) > 6:
        return JSONResponse({"error": "Max 6 commands per batch"}, status_code=400)

    # Validate all commands before executing any
    for c in commands:
        cid = c.get("command_id", "")
        if cid not in ALLOWED_COMMANDS:
            return JSONResponse({"error": f"Unknown command: {cid}", "available": list(ALLOWED_COMMANDS.keys())}, status_code=400)
        if cid == "deploy_collector":
            return JSONResponse({"error": "deploy_collector not allowed in batch"}, status_code=400)

    # Build combined script
    scripts = []
    for i, c in enumerate(commands):
        cid = c.get("command_id", "")
        cmd = ALLOWED_COMMANDS[cid]
        sidadm = c.get("sidadm", "").strip()
        instance = c.get("instance", "00").strip()
        sid = c.get("sid", "").strip().upper()

        if cmd["requires_sidadm"] and not sidadm:
            return JSONResponse({"error": f"Command '{cid}' requires sidadm"}, status_code=400)
        err = validate_input(vm, rg, sidadm, instance)
        if err:
            return JSONResponse({"error": err}, status_code=400)

        script = cmd["script"].replace("{sidadm}", sidadm).replace("{instance}", instance).replace("{sid}", sid)
        scripts.append(f'echo "===BATCH_CMD_{i}_{cid}==="\n{script}')

    combined = "\n".join(scripts)
    logger.info(json.dumps({"event": "batch_execute", "caller": caller, "vm": vm, "rg": rg, "count": len(commands)}))

    try:
        token = get_mi_token()
        url = f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{vm}/runCommand?api-version=2024-03-01"
        resp = http_requests.post(url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"commandId": "RunShellScript", "script": [combined]}, timeout=300)

        if resp.status_code == 202:
            location = resp.headers.get("Location") or resp.headers.get("Azure-AsyncOperation")
            if not location:
                return JSONResponse({"error": "202 but no Location header"}, status_code=502)
            for attempt in range(40):
                _time.sleep(5)
                poll = http_requests.get(location, headers={"Authorization": f"Bearer {token}"}, timeout=30)
                if poll.status_code == 200:
                    data = poll.json()
                    status = data.get("status", data.get("provisioningState", "Succeeded"))
                    if status.lower() in ("succeeded", ""):
                        stdout, stderr = parse_run_command_output(data)
                        # Split output by batch markers
                        results = {}
                        for i, c in enumerate(commands):
                            cid = c.get("command_id", "")
                            marker = f"===BATCH_CMD_{i}_{cid}==="
                            next_marker = f"===BATCH_CMD_{i+1}_" if i + 1 < len(commands) else None
                            start = stdout.find(marker)
                            if start == -1:
                                results[cid] = {"stdout": "", "error": "marker not found"}
                                continue
                            start += len(marker) + 1  # skip newline
                            if next_marker:
                                end = stdout.find(next_marker, start)
                                if end == -1:
                                    end = len(stdout)
                            else:
                                end = len(stdout)
                            results[cid] = {"stdout": stdout[start:end].strip()}
                        return JSONResponse({"vm": vm, "rg": rg, "count": len(commands), "results": results})
                    elif status.lower() == "failed":
                        return JSONResponse({"error": "Batch run-command failed", "detail": str(data)[:500]}, status_code=502)
                elif poll.status_code != 202:
                    return JSONResponse({"error": f"Poll returned {poll.status_code}"}, status_code=502)
            return JSONResponse({"error": "Batch timed out after 200s"}, status_code=504)

        elif resp.status_code == 200:
            stdout, stderr = parse_run_command_output(resp.json())
            results = {}
            for i, c in enumerate(commands):
                cid = c.get("command_id", "")
                marker = f"===BATCH_CMD_{i}_{cid}==="
                next_marker = f"===BATCH_CMD_{i+1}_" if i + 1 < len(commands) else None
                start = stdout.find(marker)
                if start == -1:
                    results[cid] = {"stdout": ""}
                    continue
                start += len(marker) + 1
                end = stdout.find(next_marker, start) if next_marker else len(stdout)
                if end == -1:
                    end = len(stdout)
                results[cid] = {"stdout": stdout[start:end].strip()}
            return JSONResponse({"vm": vm, "rg": rg, "count": len(commands), "results": results})
        else:
            return JSONResponse({"error": f"Batch failed: {resp.status_code}", "detail": resp.text[:500]}, status_code=502)

    except http_requests.exceptions.Timeout:
        return JSONResponse({"error": "Batch timeout — VM unresponsive"}, status_code=504)
    except Exception as e:
        logger.error(f"Batch error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════
# STAF CHECK DEFINITIONS — fetches from Azure/sap-automation-qa GitHub repo
# ═══════════════════════════════════════════════════════════════════════════

STAF_YAML_FILES = [
    "hana.yml", "sap.yml", "virtual_machine.yml", "network.yml",
    "ascs.yml", "app.yml", "high_availability.yml", "package.yml", "db2.yml",
]
STAF_BASE_URL = (
    "https://raw.githubusercontent.com/Azure/sap-automation-qa"
    "/main/src/roles/configuration_checks/tasks/files"
)
_staf_cache: dict = {"checks": None, "ts": 0}


def _parse_staf_yaml(raw_text: str):
    """Parse STAF YAML, handling enums-at-bottom anchor ordering."""
    try:
        return yaml.safe_load(raw_text)
    except yaml.YAMLError:
        # Anchors may be defined after use — move enums: to top and retry
        match = re.search(r'^\s{0,2}enums:', raw_text, re.MULTILINE)
        if match:
            pos = raw_text.rfind('\n', 0, match.start()) + 1
            return yaml.safe_load(raw_text[pos:] + '\n' + raw_text[:pos])
        raise


def _fetch_all_staf_checks():
    """Fetch and parse all 9 STAF YAML files from GitHub."""
    all_checks, errors = [], []
    for fname in STAF_YAML_FILES:
        try:
            resp = http_requests.get(f"{STAF_BASE_URL}/{fname}", timeout=30)
            if resp.status_code != 200:
                errors.append(f"{fname}: HTTP {resp.status_code}")
                continue
            parsed = _parse_staf_yaml(resp.text)
            if parsed and "checks" in parsed:
                for chk in parsed["checks"]:
                    chk["_source"] = fname
                    all_checks.append(chk)
        except Exception as e:
            errors.append(f"{fname}: {e}")
    return all_checks, errors


def _get_cached_staf_checks():
    """Return cached STAF checks, refreshing if stale (1-hour TTL)."""
    now = _time.time()
    if _staf_cache["checks"] and (now - _staf_cache["ts"]) < CACHE_TTL:
        return _staf_cache["checks"], [], "memory_cache"
    checks, errors = _fetch_all_staf_checks()
    if checks:
        _staf_cache["checks"] = checks
        _staf_cache["ts"] = now
        # Save snapshot to blob for fallback
        _save_staf_snapshot(checks)
        return checks, errors, "github_live"
    # GitHub failed — try blob snapshot
    snap_checks, snap_ts = _load_staf_snapshot()
    if snap_checks:
        _staf_cache["checks"] = snap_checks
        _staf_cache["ts"] = now
        return snap_checks, errors, f"blob_cached (synced {snap_ts})"
    return [], errors, "failed"


def _save_staf_snapshot(checks):
    """Save STAF checks snapshot to blob for fallback."""
    try:
        cc = get_container_client()
        import datetime
        snapshot = json.dumps({
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "count": len(checks),
            "checks": [{k: v for k, v in c.items() if k != "_source"} for c in checks],
        })
        cc.get_blob_client("staf-checks/staf-snapshot.json").upload_blob(
            snapshot, overwrite=True)
        logger.info(f"STAF snapshot saved: {len(checks)} checks")
    except Exception as e:
        logger.warning(f"Failed to save STAF snapshot: {e}")


def _load_staf_snapshot():
    """Load STAF checks snapshot from blob. Returns (checks, timestamp)."""
    try:
        cc = get_container_client()
        data = json.loads(cc.get_blob_client("staf-checks/staf-snapshot.json")
                          .download_blob().readall().decode("utf-8"))
        return data.get("checks", []), data.get("timestamp", "unknown")
    except Exception:
        return [], None


def _check_applicable(check, os_type, roles, db_type, storage_type, ha_type, ha_agent):
    """Return (match, filter_reason) for a single STAF check."""
    app = check.get("applicability") or {}

    # OS type
    v = app.get("os_type")
    if v and os_type:
        lst = v if isinstance(v, list) else [v]
        if os_type not in lst:
            return False, "os"

    # Role
    v = app.get("role")
    if v and roles:
        lst = v if isinstance(v, list) else [v]
        if not any(r in lst for r in roles):
            return False, "role"

    # Database type
    v = app.get("database_type")
    if v and db_type:
        lst = v if isinstance(v, list) else [v]
        if db_type not in lst:
            return False, "db_type"

    # Storage type
    v = app.get("storage_type")
    if v and storage_type:
        lst = v if isinstance(v, list) else [v]
        if storage_type not in lst:
            return False, "storage"

    # High availability
    v = app.get("high_availability")
    if v is not None:
        if ha_type == "false":
            if v is True or (isinstance(v, list) and v):
                return False, "ha"
        elif ha_type:
            if v is False:
                return False, "ha"
            if isinstance(v, list) and ha_type not in v:
                return False, "ha"

    # HA agent
    v = app.get("high_availability_agent")
    if v is not None:
        if ha_agent == "none":
            return False, "ha"
        else:
            lst = v if isinstance(v, list) else [v]
            if ha_agent not in lst:
                return False, "ha"

    return True, None


@app.get("/api/staf-checks", deprecated=True)
def get_staf_checks(req: Request):
    """[DEPRECATED] Fetch STAF check definitions from GitHub, filter by applicability, return JSON.

    DEPRECATED as of 2026-06: The rewritten sap-config-validator skill fetches STAF
    YAML files directly from GitHub in-skill (ExecutePythonCode + requests). This
    endpoint is retained only so previously-imported skill versions continue to
    work during migration. Schedule for removal once all customers have re-imported
    the new sap-config-validator skill. See README.md > Adoption Modes.

    Query params:
      os_type      — SLES_SAP | REDHAT
      roles        — comma-separated: DB,SCS,PAS,APP,ERS,WEB
      db_type      — HANA | Db2
      storage_type — Premium_LRS | UltraSSD_LRS | ANF | PremiumV2_LRS | AFS
      ha_type      — false | scale_up | scale_out
      ha_agent     — none | AFA | ISCSI
    """
    auth_err = require_auth(req)
    if auth_err:
        return auth_err

    os_type = req.query_params.get("os_type", "").strip()
    roles_raw = req.query_params.get("roles", "").strip()
    db_type = req.query_params.get("db_type", "").strip()
    storage_type = req.query_params.get("storage_type", "").strip()
    ha_type = req.query_params.get("ha_type", "false").strip()
    ha_agent = req.query_params.get("ha_agent", "none").strip()

    # Normalize roles (ASCS -> SCS to match STAF convention)
    role_alias = {"ASCS": "SCS"}
    roles = list(
        {role_alias.get(r, r) for r in (r.strip().upper() for r in roles_raw.split(",")) if r.strip()}
    ) if roles_raw else []

    all_checks, errors, _src = _get_cached_staf_checks()
    if not all_checks:
        return JSONResponse(
            {"error": "Failed to fetch STAF check definitions from GitHub", "details": errors},
            status_code=502,
        )

    applicable, seen = [], set()
    filtered = {"ha": 0, "storage": 0, "os": 0, "role": 0, "db_type": 0}

    for chk in all_checks:
        cid = chk.get("id", "")
        if cid in seen:
            continue
        ok, reason = _check_applicable(chk, os_type, roles, db_type, storage_type, ha_type, ha_agent)
        if ok:
            seen.add(cid)
            applicable.append(chk)
        elif reason:
            filtered[reason] = filtered.get(reason, 0) + 1

    validated = [c for c in applicable if c.get("report") == "check" and c.get("validator_type")]
    data_coll = [c for c in applicable if c.get("report") != "check" or not c.get("validator_type")]

    out_checks = []
    for c in applicable:
        out_checks.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "description": c.get("description"),
            "category": c.get("category"),
            "severity": c.get("severity"),
            "collector_type": c.get("collector_type"),
            "collector_args": c.get("collector_args"),
            "validator_type": c.get("validator_type"),
            "validator_args": c.get("validator_args"),
            "report": c.get("report"),
            "references": c.get("references"),
            "source_file": c.get("_source"),
        })

    logger.info(json.dumps({
        "event": "staf_checks",
        "total": len(all_checks),
        "applicable": len(applicable),
        "validated": len(validated),
        "filters": filtered,
    }))

    return JSONResponse({
        "total_staf_checks": len(all_checks),
        "filtered_out": filtered,
        "applicable": len(applicable),
        "validated": len(validated),
        "data_collection": len(data_coll),
        "checks": out_checks,
    })


# ═══════════════════════════════════════════════════════════════════════════
# FULL VALIDATION — server-side STAF check comparison
# ═══════════════════════════════════════════════════════════════════════════

def _run_collector_on_vm(vm, rg, sub_id):
    """Trigger the collector script on a VM via run command. Waits for completion.
    The collector must already be deployed at /opt/sre/run-collector.sh."""
    script = "bash /opt/sre/run-collector.sh 2>&1 | tail -5"
    try:
        token = get_mi_token()
        url = (f"https://management.azure.com/subscriptions/{sub_id}"
               f"/resourceGroups/{rg}/providers/Microsoft.Compute"
               f"/virtualMachines/{vm}/runCommand?api-version=2024-03-01")
        resp = http_requests.post(url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"commandId": "RunShellScript", "script": [script]}, timeout=180)

        if resp.status_code == 202:
            location = resp.headers.get("Location") or resp.headers.get("Azure-AsyncOperation")
            if not location:
                return {"status": "error", "error": "202 but no Location header"}
            for _ in range(30):
                _time.sleep(5)
                poll = http_requests.get(location, headers={"Authorization": f"Bearer {token}"}, timeout=30)
                if poll.status_code == 200:
                    data = poll.json()
                    prov = data.get("status", data.get("provisioningState", "Succeeded"))
                    if prov.lower() in ("succeeded", ""):
                        stdout, stderr = parse_run_command_output(data)
                        return {"status": "success", "stdout": stdout, "stderr": stderr}
                    elif prov.lower() == "failed":
                        return {"status": "error", "error": "run-command failed", "detail": str(data)[:300]}
            return {"status": "error", "error": "timeout after 150s"}
        elif resp.status_code == 200:
            stdout, stderr = parse_run_command_output(resp.json())
            return {"status": "success", "stdout": stdout, "stderr": stderr}
        else:
            return {"status": "error", "error": f"HTTP {resp.status_code}", "detail": resp.text[:300]}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def _fetch_arm_data(vm, rg, sub_id):
    """Fetch VM, disk, and NIC details from ARM API for azure_collector checks."""
    arm = {"vm": {}, "disks": {}, "nics": [], "error": None}
    try:
        token = get_mi_token()
        headers = {"Authorization": f"Bearer {token}"}
        base = f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{rg}/providers"

        # VM details
        r = http_requests.get(f"{base}/Microsoft.Compute/virtualMachines/{vm}?api-version=2024-03-01", headers=headers, timeout=30)
        if r.status_code == 200:
            arm["vm"] = r.json()

            # Fetch each data disk's details (IOPS, MBPS)
            vm_data = arm["vm"]
            storage_profile = vm_data.get("properties", {}).get("storageProfile", {})
            for disk in storage_profile.get("dataDisks", []):
                disk_id = disk.get("managedDisk", {}).get("id", "")
                if disk_id:
                    dr = http_requests.get(f"https://management.azure.com{disk_id}?api-version=2024-03-02", headers=headers, timeout=15)
                    if dr.status_code == 200:
                        dd = dr.json()
                        lun = disk.get("lun", -1)
                        arm["disks"][lun] = {
                            "name": dd.get("name", ""),
                            "sku": dd.get("sku", {}).get("name", ""),
                            "size_gb": dd.get("properties", {}).get("diskSizeGB", 0),
                            "iops": dd.get("properties", {}).get("diskIOPSReadWrite", 0),
                            "mbps": dd.get("properties", {}).get("diskMBpsReadWrite", 0),
                        }

            # Fetch NIC details
            nic_refs = vm_data.get("properties", {}).get("networkProfile", {}).get("networkInterfaces", [])
            for nic_ref in nic_refs:
                nic_id = nic_ref.get("id", "")
                if nic_id:
                    nr = http_requests.get(f"https://management.azure.com{nic_id}?api-version=2024-03-01", headers=headers, timeout=15)
                    if nr.status_code == 200:
                        arm["nics"].append(nr.json())
    except Exception as e:
        arm["error"] = str(e)
        logger.warning(f"ARM data fetch failed: {e}")
    return arm


def _extract_from_arm(check, arm_data, config_files=None):
    """Extract actual value from ARM data for azure_collector checks."""
    if not arm_data or not arm_data.get("vm"):
        return None, "no_arm_data"

    collector_args = check.get("collector_args") or {}
    resource_type = collector_args.get("resource_type", "")
    prop = collector_args.get("property", "")
    mount_point = collector_args.get("mount_point", "")
    check_id = check.get("id", "")
    vm_data = arm_data.get("vm", {})
    vm_props = vm_data.get("properties", {})

    # ── Disk performance checks (IOPS, MBPS, stripe_size) ──
    if resource_type == "disks" and mount_point:
        disks = arm_data.get("disks", {})
        if not disks:
            return None, "no_disk_data"
        # Sum IOPS/MBPS across all data disks for the mount point
        # (We can't map LUN→mount without OS data, so sum all data disks as estimate)
        total_iops = sum(d.get("iops", 0) for d in disks.values())
        total_mbps = sum(d.get("mbps", 0) for d in disks.values())
        if prop == "iops":
            return str(total_iops), None
        if prop == "mbps":
            return str(total_mbps), None
        if prop == "stripe_size":
            # Stripe size comes from OS-level LVM data, not ARM
            lvm_data = (config_files or {}).get("os/lvm-stripes.txt", "")
            if lvm_data:
                for line in lvm_data.split("\n"):
                    if line.startswith(mount_point + ":") and "stripe_size=" in line:
                        ss = line.split("stripe_size=")[1].strip().split()[0]
                        if ss and ss != "" and ss != "N/A":
                            return ss, None
            return None, "stripe_not_found"
        return None, "unknown_disk_property"

    # ── ANF volume checks ──
    if resource_type == "anf":
        return None, "anf_not_implemented"

    # ── NIC / Network checks ──
    nics = arm_data.get("nics", [])
    if resource_type in ("nic", "network") or check_id.startswith("NET-"):
        if not nics:
            return None, "no_nic_data"
        nic = nics[0]  # Primary NIC
        nic_props = nic.get("properties", {})
        ip_configs = nic_props.get("ipConfigurations", [])

        if "acceleratedNetworking" in prop or "NET-0005" == check_id:
            accel = nic_props.get("enableAcceleratedNetworking", False)
            return ("Enabled" if accel else "Disabled"), None
        if "name" in prop.lower() or "NET-0004" == check_id:
            return nic.get("name", ""), None
        if "subnet" in prop.lower() or "NET-0002" == check_id:
            if ip_configs:
                subnet_id = ip_configs[0].get("properties", {}).get("subnet", {}).get("id", "")
                return subnet_id.split("/")[-1] if subnet_id else "", None
        if "virtualNetwork" in prop or "NET-0001" == check_id:
            if ip_configs:
                subnet_id = ip_configs[0].get("properties", {}).get("subnet", {}).get("id", "")
                parts = subnet_id.split("/")
                vnet_idx = parts.index("virtualNetworks") + 1 if "virtualNetworks" in parts else -1
                return parts[vnet_idx] if vnet_idx > 0 else "", None
        if "ipConfiguration" in prop or "NET-0006" == check_id:
            return str(len(ip_configs)), None
        if "NET-0003" == check_id:
            return str(len(nics)), None
        if "NET-0007" == check_id:
            ips = [{"name": ipc.get("name"), "private": ipc.get("properties", {}).get("privateIPAddress", "")} for ipc in ip_configs]
            return json.dumps(ips), None
        return None, "unknown_net_property"

    # ── VM metadata checks ──
    if check_id.startswith("IC-"):
        if "IC-0031" == check_id:  # Availability Set
            avset = vm_props.get("availabilitySet", {}).get("id", "")
            return (avset.split("/")[-1] if avset else "None"), None
        if "IC-0032" == check_id:  # PPG
            ppg = vm_props.get("proximityPlacementGroup", {}).get("id", "")
            return (ppg.split("/")[-1] if ppg else "None"), None
        if "IC-0033" == check_id:  # PPG VMs
            ppg_id = vm_props.get("proximityPlacementGroup", {}).get("id", "")
            return (ppg_id.split("/")[-1] if ppg_id else "None"), None
        if "IC-0034" == check_id:  # VM Generation
            hw = vm_props.get("hardwareProfile", {})
            return hw.get("vmSize", ""), None
        if "IC-0036" == check_id:  # Secondary IP
            if nics and nics[0].get("properties", {}).get("ipConfigurations"):
                ips = nics[0]["properties"]["ipConfigurations"]
                secondary = [ip for ip in ips if not ip.get("properties", {}).get("primary", True)]
                return str(len(secondary)), None
            return "0", None
        if "IC-0037" == check_id:  # Security Type
            sec = vm_props.get("securityProfile", {}).get("securityType", "Standard")
            return sec, None
        if "IC-0039" == check_id:  # VMSS Flex ID
            vmss = vm_props.get("virtualMachineScaleSet", {}).get("id", "")
            return (vmss.split("/")[-1] if vmss else "None"), None
        return None, "unknown_ic_check"

    return None, "unknown_azure_resource"


def _extract_actual_value(check, config_files, arm_data=None):
    """Extract actual value from config files based on STAF collector command.
    Returns (value: str|None, skip_reason: str|None)."""
    collector_type = check.get("collector_type")
    collector_args = check.get("collector_args") or {}
    command = collector_args.get("command", "")
    report_type = check.get("report", "check")

    if report_type in ("section", "table"):
        return None, "data_collection"
    if collector_type == "azure":
        return _extract_from_arm(check, arm_data or {}, config_files)
    if not command:
        return None, "no_command"
    if not config_files:
        return None, "no_config_files"

    # ── sysctl: /sbin/sysctl <param> -n ──
    m = re.search(r'/sysctl\s+([\w.]+)', command)
    if m:
        param = m.group(1)
        data = config_files.get("os/sysctl-runtime.txt", "")
        if not data:
            return None, "no_config_file"
        for line in data.split("\n"):
            if "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == param:
                    return v.strip(), None
        return None, "param_not_found"

    # ── df filesystem type: df -T /path ──
    m = re.search(r"df -T\s+(\S+)", command)
    if m:
        mount = m.group(1)
        data = config_files.get("os/disk-usage.txt", "")
        if not data:
            return None, "no_config_file"
        for line in data.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 7 and parts[6] == mount:
                return parts[1], None
        return None, "mount_not_found"

    # ── fstrim / systemctl grep fstrim ──
    if "fstrim" in command and ("systemctl" in command or "/bin/systemctl" in command):
        data = config_files.get("os/fstrim-status.txt")
        if data is None:
            return None, "no_config_file"
        active = sum(1 for l in data.split("\n") if "active" in l.lower())
        return str(active), None

    # ── free -m swap ──
    if "free -m" in command and "Swap" in command:
        data = config_files.get("os/free-m.txt", "")
        if not data:
            return None, "no_config_file"
        for line in data.split("\n"):
            if line.strip().startswith("Swap:"):
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1], None
        return None, "param_not_found"

    # ── THP ──
    if "transparent_hugepage" in command:
        data = config_files.get("os/thp-status.txt", "")
        if not data:
            return None, "no_config_file"
        m2 = re.search(r'\[(\w+)\]', data)
        return (m2.group(1), None) if m2 else (data.strip(), None)

    # ── tuned-adm ──
    if "tuned-adm" in command:
        data = config_files.get("os/tuned-profile.txt", "")
        if not data:
            return None, "no_config_file"
        return data.strip(), None

    # ── DefaultTasksMax ──
    if "DefaultTasksMax" in command:
        data = config_files.get("os/systemd-defaults.txt", "")
        if not data:
            return None, "no_config_file"
        for line in data.split("\n"):
            if "DefaultTasksMax" in line:
                _, _, v = line.partition("=")
                return v.strip(), None
        return None, "param_not_found"

    # ── lsmod softdog ──
    if "lsmod" in command and "softdog" in command:
        data = config_files.get("os/lsmod-softdog.txt")
        if data is None:
            return None, "no_config_file"
        return (data.strip() or "not loaded"), None

    # ── stat sector/block size ──
    m = re.search(r"stat -f .+?(/\S+)", command)
    if m:
        mount = m.group(1)
        data = config_files.get("os/stat-block-size.txt", "")
        if not data:
            return None, "no_config_file"
        for line in data.split("\n"):
            if line.startswith(mount + ":"):
                val = line.split(":", 1)[1].strip()
                if val and val != "N/A":
                    return val, None
        return None, "mount_not_found"

    # ── uname -r (kernel version) ──
    if "uname -r" in command:
        data = config_files.get("os/uname-r.txt", "")
        if not data:
            return None, "no_config_file"
        return data.strip(), None

    # ── hostname ──
    if command.strip() in ("/bin/hostname", "hostname", "/usr/bin/hostname"):
        data = config_files.get("os/imds-metadata.json", "")
        if data and data.strip() != "{}":
            try:
                return json.loads(data).get("compute", {}).get("osProfile", {}).get("computerName", ""), None
            except (json.JSONDecodeError, KeyError):
                pass
        # Fallback: manifest has hostname
        manifest = config_files.get("manifest.json", "")
        if manifest:
            try:
                return json.loads(manifest).get("hostname", ""), None
            except (json.JSONDecodeError, KeyError):
                pass
        return None, "no_config_file"

    # ── IMDS metadata queries ──
    if "169.254.169.254" in command:
        data = config_files.get("os/imds-metadata.json", "")
        if not data or data.strip() == "{}":
            return None, "no_config_file"
        try:
            imds = json.loads(data)
            # Extract the IMDS path from the URL
            path_match = re.search(r'/metadata/instance/(.+?)[\?&]', command)
            if path_match:
                path = path_match.group(1)
                # Navigate nested JSON by path (e.g., "compute/vmSize")
                obj = imds
                for key in path.split("/"):
                    if isinstance(obj, dict):
                        obj = obj.get(key, "")
                    else:
                        obj = ""
                        break
                if isinstance(obj, str):
                    return obj, None
                return json.dumps(obj), None
            # Fallback: try common field names in command
            compute = imds.get("compute", {})
            if "vmSize" in command:
                return compute.get("vmSize", ""), None
            if "name" in command:
                return compute.get("name", ""), None
            if "location" in command:
                return compute.get("location", ""), None
            return json.dumps(compute), None
        except (json.JSONDecodeError, KeyError):
            return None, "parse_error"

    # ── Hypervisor KVP (IC-0009) ──
    if "kvp_pool" in command or "hyperv" in command.lower():
        return None, "hypervisor_kvp"

    # ── OS release version ──
    if "/etc/os-release" in command or "os-release" in command or "PRETTY_NAME" in command:
        data = config_files.get("os/os-release", "") or config_files.get("os/os-release.txt", "")
        if not data:
            return None, "no_config_file"
        if "PRETTY_NAME" in command:
            for line in data.split("\n"):
                if line.startswith("PRETTY_NAME"):
                    return line.split("=", 1)[1].strip().strip('"'), None
        if "VERSION_ID" in command:
            for line in data.split("\n"):
                if line.startswith("VERSION_ID"):
                    return line.split("=", 1)[1].strip().strip('"'), None
        return data.strip(), None

    # ── timedatectl / timezone / date +%Z ──
    if "timedatectl" in command or "+%Z" in command:
        data = config_files.get("os/timezone.txt", "")
        if not data:
            return None, "no_config_file"
        for line in data.split("\n"):
            if "Time zone" in line:
                return line.split(":", 1)[1].strip().split(" ")[0], None
        return data.strip(), None

    # ── KDUMP / systemctl status kdump ──
    if "kdump" in command.lower():
        data = config_files.get("os/kdump-config.txt", "")
        if not data:
            return None, "no_config_file"
        if "Active:" in command or "systemctl" in command:
            for line in data.split("\n"):
                if "Active:" in line or "=== KDUMP Service ===" in line:
                    continue
                val = line.strip()
                if val and val not in ("=== KDUMP Config ===", "=== KDUMP Service ==="):
                    return val, None
        return data.strip(), None

    # ── Security endpoint file-existence checks (FC-0002 to FC-0007) ──
    if "[ -f" in command and ("Installed" in command or "installed" in command):
        # Extract the file path being checked
        m2 = re.search(r'\[ -f (\S+) \]', command)
        if m2:
            check_path = m2.group(1)
            # Check if the package is in installed-packages.txt
            data = config_files.get("os/installed-packages.txt", "")
            # Map known paths to package names
            path_pkg_map = {
                "/opt/CrowdStrike/falconctl": "falcon-sensor",
                "/opt/sentinelone/bin/sentinelctl": "sentinelone",
                "/opt/ds_agent/dsa_control": "ds_agent",
                "/opt/sophos-spl/plugins/av/bin/avscanner": "sophos",
                "/usr/local/bin/uvscan": "trellix",
                "/usr/bin/clamscan": "clamav",
            }
            pkg = path_pkg_map.get(check_path, "")
            if pkg and data:
                matches = [l for l in data.split("\n") if pkg.lower() in l.lower()]
                return ("Installed" if matches else "Not installed"), None
            # If not in map, assume not installed
            return "Not installed", None
        return None, "unknown_command"

    # ── Package checks (rpm -qa, dpkg) ──
    if "rpm " in command or "dpkg " in command or "zypper " in command:
        data = config_files.get("os/installed-packages.txt", "")
        if not data:
            return None, "no_config_file"
        grep_match = re.search(r'grep\s+["\']?(\S+)', command)
        if grep_match:
            pkg = grep_match.group(1).strip("'\"")
            matches = [l for l in data.split("\n") if pkg.lower() in l.lower()]
            return ("\n".join(matches) if matches else "not installed"), None
        return data.strip(), None

    # ── mdatp health ──
    if "mdatp" in command:
        data = config_files.get("os/mdatp-health.txt", "")
        if not data:
            return None, "no_config_file"
        for field in ["healthy", "real_time_protection_enabled", "automatic_definition_update_enabled",
                       "definitions_status", "edr_early_preview_enabled", "conflicting_applications",
                       "supplementary_events_subsystem", "release_ring"]:
            if field in command.lower() or field.replace("_", "") in command.lower():
                for line in data.split("\n"):
                    if field in line.lower() or field.replace("_", " ") in line.lower():
                        parts = line.split(":", 1) if ":" in line else line.split("=", 1)
                        if len(parts) == 2:
                            return parts[1].strip().strip('"'), None
        return data.strip(), None

    # ── cluster commands ──
    if any(kw in command for kw in ["cibadmin", "crm ", "corosync", "SAPHanaSR", "pcs "]):
        return None, "cluster_check"

    # ── ANF/NFS specific ──
    if any(kw in command for kw in ["nfs4_disable_idmapping", "idmapd.conf", "mount -t nfs4", "sysconfig/network/dhcp"]):
        return None, "anf_check"

    return None, "unknown_command"


def _compare_values(actual, check):
    """Compare actual vs expected using STAF validator logic.
    Returns (status, details)."""
    vtype = check.get("validator_type")
    vargs = check.get("validator_args") or {}

    if not vtype:
        return "data_collection", None
    if actual is None:
        return "not_evaluated", None

    actual = str(actual).strip()

    if vtype == "string":
        expected = str(vargs.get("expected_output", "") or vargs.get("expected", "")).strip()
        # Normalize whitespace (tabs → spaces, collapse multiple)
        actual_norm = " ".join(actual.split())
        expected_norm = " ".join(expected.split())
        if actual_norm.lower() == expected_norm.lower():
            return "pass", None
        # Some STAF checks include full sysctl output: "param = value"
        # If expected contains '=', compare just the value part
        if "=" in expected_norm:
            expected_val = expected_norm.split("=", 1)[1].strip()
            if actual_norm.lower() == expected_val.lower():
                return "pass", None
        return "fail", {"actual": actual_norm, "expected": expected_norm}

    if vtype == "range":
        try:
            actual_num = int(actual)
        except (ValueError, TypeError):
            return "error", {"actual": actual, "error": "non-numeric"}
        lo = vargs.get("min")
        hi = vargs.get("max")
        if lo is not None and actual_num < int(lo):
            return "fail", {"actual": actual, "min": str(lo), "max": str(hi) if hi else None}
        if hi is not None and actual_num > int(hi):
            return "fail", {"actual": actual, "min": str(lo), "max": str(hi)}
        return "pass", None

    if vtype == "list":
        valid = vargs.get("valid_list", [])
        if actual.lower() in [str(v).lower() for v in valid]:
            return "pass", None
        return "fail", {"actual": actual, "valid_list": valid}

    return "not_evaluated", {"reason": f"unsupported validator: {vtype}"}


@app.get("/api/validate/{sid}/{hostname}", deprecated=True)
def validate_config(sid: str, hostname: str, req: Request):
    """[DEPRECATED] Full server-side STAF validation with fresh+fallback for both
    STAF checks and config data.

    DEPRECATED as of 2026-06: The rewritten sap-config-validator skill now runs
    the entire validation flow client-side (in-skill via ExecutePythonCode):
    fetches STAF YAML from GitHub, reads collected configs from blob with
    RunAzCliReadCommands, performs the comparison in Python, and formats the
    report. This endpoint is retained only for backward compat with previously
    imported skill versions — schedule for removal once all customers have
    re-imported the new skill. See README.md > Adoption Modes.

    Flow:
      1. STAF: GitHub live → save blob snapshot → fallback to blob snapshot
      2. Configs: trigger collector on VM → read fresh blob → fallback to existing blob
      3. Compare actual vs expected → structured report

    Required query params: os_type, roles, db_type, storage_type, ha_type, ha_agent, rg
    Optional: vm (defaults to hostname), subscription_id (defaults to env var)
    """
    auth_err = require_auth(req)
    if auth_err:
        return auth_err

    if not re.match(r'^[a-zA-Z0-9_.-]+$', sid) or not re.match(r'^[a-zA-Z0-9_.-]+$', hostname):
        return JSONResponse({"error": "Invalid sid or hostname"}, status_code=400)

    os_type = req.query_params.get("os_type", "").strip()
    roles_raw = req.query_params.get("roles", "").strip()
    db_type = req.query_params.get("db_type", "").strip()
    storage_type = req.query_params.get("storage_type", "").strip()
    ha_type = req.query_params.get("ha_type", "false").strip()
    ha_agent = req.query_params.get("ha_agent", "none").strip()
    vm = req.query_params.get("vm", hostname).strip()
    rg = req.query_params.get("rg", "").strip()
    sub_id = req.query_params.get("subscription_id", "").strip() or SUB_ID

    role_alias = {"ASCS": "SCS"}
    roles = list(
        {role_alias.get(r, r) for r in (r.strip().upper() for r in roles_raw.split(",")) if r.strip()}
    ) if roles_raw else []

    # ── 1. STAF checks (fresh GitHub → fallback blob snapshot) ──
    all_checks, staf_errors, staf_source = _get_cached_staf_checks()
    if not all_checks:
        return JSONResponse({"error": "Failed to fetch STAF checks", "staf_errors": staf_errors}, status_code=502)

    applicable, seen = [], set()
    filtered = {"ha": 0, "storage": 0, "os": 0, "role": 0, "db_type": 0}
    for chk in all_checks:
        cid = chk.get("id", "")
        if cid in seen:
            continue
        ok, reason = _check_applicable(chk, os_type, roles, db_type, storage_type, ha_type, ha_agent)
        if ok:
            seen.add(cid)
            applicable.append(chk)
        elif reason:
            filtered[reason] = filtered.get(reason, 0) + 1

    # ── 2. Config data (fresh collector → fallback existing blob) ──
    config_source = "none"
    collector_result = None

    # Try fresh collection if we have VM details
    if rg and sub_id:
        logger.info(f"validate: triggering fresh collection on {vm} in {rg}")
        collector_result = _run_collector_on_vm(vm, rg, sub_id)
        if collector_result.get("status") == "success":
            config_source = "freshly_collected"
            logger.info(f"validate: collector succeeded on {vm}")
        else:
            logger.warning(f"validate: collector failed on {vm}: {collector_result.get('error', 'unknown')}")
            config_source = "cached_fallback"

    # Read blob configs (fresh from collector or existing fallback)
    config_files = {}
    config_timestamp = None
    config_error = None
    try:
        cc = get_container_client()
        prefix = f"{sid}/{hostname}/latest/"
        for blob in cc.list_blobs(name_starts_with=prefix):
            if blob.name.endswith("/") or "manifest.json" in blob.name:
                continue
            rel_path = blob.name[len(prefix):]
            if config_timestamp is None:
                config_timestamp = str(blob.last_modified) if hasattr(blob, 'last_modified') else None
            try:
                config_files[rel_path] = cc.get_blob_client(blob.name).download_blob().readall().decode("utf-8", errors="replace")
            except Exception:
                pass
        if config_files:
            if config_source == "none":
                config_source = "cached"
        else:
            config_source = "no_data"
    except Exception as e:
        config_error = str(e)
        logger.warning(f"validate: failed to read configs for {sid}/{hostname}: {e}")

    # ── 3. Fetch ARM data for azure_collector checks ──
    arm_data = {}
    if rg and sub_id:
        arm_data = _fetch_arm_data(vm, rg, sub_id)

    # ── 4. Validate each check ──
    results = []
    summary = {"pass": 0, "fail": 0, "not_evaluated": 0, "data_collection": 0, "error": 0}
    failures = []

    for chk in applicable:
        actual, skip_reason = _extract_actual_value(chk, config_files, arm_data)

        if skip_reason == "data_collection":
            status, details = "data_collection", None
        elif actual is not None:
            status, details = _compare_values(actual, chk)
        else:
            status = "not_evaluated"
            details = {"reason": skip_reason} if skip_reason else None

        summary[status] = summary.get(status, 0) + 1

        entry = {
            "id": chk.get("id"),
            "name": chk.get("name"),
            "severity": chk.get("severity"),
            "status": status,
        }
        if details:
            entry["details"] = details
        if chk.get("references"):
            entry["references"] = chk.get("references")
        results.append(entry)

        if status == "fail":
            failures.append(entry)

    logger.info(json.dumps({
        "event": "validate",
        "sid": sid,
        "hostname": hostname,
        "staf_source": staf_source,
        "config_source": config_source,
        "config_files": len(config_files),
        "applicable": len(applicable),
        "summary": summary,
    }))

    return JSONResponse({
        "sid": sid,
        "hostname": hostname,
        "data_sources": {
            "staf": {"source": staf_source, "total": len(all_checks), "errors": staf_errors or None},
            "config": {
                "source": config_source,
                "file_count": len(config_files),
                "timestamp": config_timestamp,
                "collector_result": collector_result if collector_result else None,
                "error": config_error,
            },
        },
        "staf": {
            "total": len(all_checks),
            "filtered_out": filtered,
            "applicable": len(applicable),
        },
        "summary": summary,
        "failures": failures,
        "results": results,
    })