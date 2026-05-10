import azure.functions as func
import logging
import json
import os
import re
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()

# Configuration from environment
STORAGE_ACCOUNT = os.environ.get("STORAGE_ACCOUNT_NAME", "")
if not STORAGE_ACCOUNT:
    raise ValueError("STORAGE_ACCOUNT_NAME app setting is required.")
CONTAINER = os.environ.get("CONTAINER_NAME", "sap-configs")
ALLOWED_PRINCIPALS = os.environ.get("ALLOWED_PRINCIPALS", "").split(",")  # SRE agent MI principal IDs

# Initialize blob client with MI
credential = DefaultAzureCredential()
blob_service = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=credential
)
container_client = blob_service.get_container_client(CONTAINER)


def validate_caller(req: func.HttpRequest) -> tuple:
    """Validate the caller using x-api-key header against per-agent keys in app settings.
    Agent keys are stored as app settings: AGENT_KEY_sre3=<key>, AGENT_KEY_sre4=<key>, etc.
    Returns (is_valid, agent_id)."""
    api_key = req.headers.get("x-api-key", "") or req.params.get("code", "")
    if not api_key:
        return False, None
    # Check against all AGENT_KEY_* settings
    for key, value in os.environ.items():
        if key.startswith("AGENT_KEY_") and value == api_key:
            agent_id = key.replace("AGENT_KEY_", "")
            return True, agent_id
    return False, None


def require_auth(req: func.HttpRequest) -> func.HttpResponse:
    """Returns 401 response if caller is not authenticated, None if OK."""
    valid, agent_id = validate_caller(req)
    if not valid:
        return func.HttpResponse(
            json.dumps({"error": "Unauthorized. Provide valid x-api-key header or code parameter."}),
            status_code=401, mimetype="application/json"
        )
    return None


@app.route(route="registry", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_registry(req: func.HttpRequest) -> func.HttpResponse:
    """Return the SAP landscape inventory JSON."""
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    try:
        blob_client = container_client.get_blob_client("sap-landscape-inventory.json")
        data = blob_client.download_blob().readall().decode("utf-8")
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
        return func.HttpResponse(
            json.dumps({"error": "Missing sid, hostname, or filepath"}),
            status_code=400, mimetype="application/json"
        )

    # Validate path components to prevent directory traversal
    if not re.match(r'^[a-zA-Z0-9_.-]+$', sid) or not re.match(r'^[a-zA-Z0-9_.-]+$', hostname):
        return func.HttpResponse(
            json.dumps({"error": "Invalid sid or hostname"}),
            status_code=400, mimetype="application/json"
        )
    if '..' in filepath or filepath.startswith('/'):
        return func.HttpResponse(
            json.dumps({"error": "Invalid filepath"}),
            status_code=400, mimetype="application/json"
        )
    if not re.match(r'^[a-zA-Z0-9/_.\-]+$', filepath):
        return func.HttpResponse(
            json.dumps({"error": "Invalid filepath characters"}),
            status_code=400, mimetype="application/json"
        )

    blob_path = f"{sid}/{hostname}/latest/{filepath}"
    try:
        blob_client = container_client.get_blob_client(blob_path)
        data = blob_client.download_blob().readall().decode("utf-8")
        return func.HttpResponse(data, mimetype="text/plain", status_code=200)
    except Exception as e:
        if "BlobNotFound" in str(e) or "404" in str(e):
            return func.HttpResponse("", status_code=404)
        logging.error(f"Failed to read {blob_path}: {e}")
        return func.HttpResponse(json.dumps({"error": "Failed to read config file"}), status_code=500, mimetype="application/json")


@app.route(route="configs/{sid}/{hostname}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_all_configs(req: func.HttpRequest) -> func.HttpResponse:
    """Return ALL config files for a SID/hostname as a single JSON bundle.
    Reduces ~15 HTTP calls per VM to 1.
    Response: { "files": { "os/sysctl-runtime.txt": "content...", "hana/global.ini": "content...", ... } }
    """
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    sid = req.route_params.get("sid", "")
    hostname = req.route_params.get("hostname", "")

    if not all([sid, hostname]):
        return func.HttpResponse(
            json.dumps({"error": "Missing sid or hostname"}),
            status_code=400, mimetype="application/json"
        )

    prefix = f"{sid}/{hostname}/latest/"
    files = {}
    try:
        blobs = container_client.list_blobs(name_starts_with=prefix)
        for blob in blobs:
            # Skip directories and manifest
            if blob.name.endswith("/") or "manifest.json" in blob.name:
                continue
            # Get relative path (e.g., "os/sysctl-runtime.txt")
            rel_path = blob.name[len(prefix):]
            try:
                blob_client = container_client.get_blob_client(blob.name)
                content = blob_client.download_blob().readall().decode("utf-8", errors="replace")
                files[rel_path] = content
            except Exception as e:
                logging.warning(f"Failed to read {blob.name}: {e}")
                files[rel_path] = None

        return func.HttpResponse(
            json.dumps({"sid": sid, "hostname": hostname, "file_count": len(files), "files": files}),
            mimetype="application/json", status_code=200
        )
    except Exception as e:
        logging.error(f"Failed to list/read configs for {sid}/{hostname}: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")


@app.route(route="configs/{sid}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_system_configs(req: func.HttpRequest) -> func.HttpResponse:
    """Return ALL config files for ALL VMs in a SID as a single JSON bundle.
    Response: { "sid": "AB1", "vms": { "AB1vm": { "files": {...} }, ... } }
    """
    auth_err = require_auth(req)
    if auth_err:
        return auth_err
    sid = req.route_params.get("sid", "")
    if not sid:
        return func.HttpResponse(
            json.dumps({"error": "Missing sid"}),
            status_code=400, mimetype="application/json"
        )

    prefix = f"{sid}/"
    vms = {}
    try:
        blobs = list(container_client.list_blobs(name_starts_with=prefix))

        # Group by hostname
        for blob in blobs:
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
                blob_client = container_client.get_blob_client(blob.name)
                content = blob_client.download_blob().readall().decode("utf-8", errors="replace")
                vms[hostname][rel_path] = content
            except Exception as e:
                logging.warning(f"Failed to read {blob.name}: {e}")
                vms[hostname][rel_path] = None

        result = {
            "sid": sid,
            "vm_count": len(vms),
            "vms": {h: {"file_count": len(f), "files": f} for h, f in vms.items()}
        }
        return func.HttpResponse(json.dumps(result), mimetype="application/json", status_code=200)

    except Exception as e:
        logging.error(f"Failed to list/read configs for {sid}: {e}")
        return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500, mimetype="application/json")
