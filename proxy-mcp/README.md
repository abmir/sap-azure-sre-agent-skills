# SAP SRE Proxy — MCP server (scaffold)

The **target state** of the optional SRE Proxy: a standalone **MCP server** the Azure SRE Agent
calls through a **connector**, instead of the bespoke REST + `X-API-Key` proxy in [`../proxy`](../proxy).

It exposes the same **allowlisted, read-only** SAP VM commands as MCP tools:

| Tool | What it does |
|------|--------------|
| `list_allowed_commands` | Lists the read-only command allowlist |
| `run_command` | Runs one allowlisted command on a VM (Azure run-command API) |
| `run_batch` | Runs up to 6 allowlisted commands in one call |

> **Scope:** live VM commands only. It does **not** touch storage — config reads are done by the
> SRE Agent's own Managed Identity reading the `sap-configs` blob directly.

## Status — SCAFFOLD

`server.py` runs and exposes the tools (FastMCP, Streamable-HTTP), mirroring
[`../proxy/app.py`](../proxy/app.py) `ALLOWED_COMMANDS`. Deploy it with
[`../infra/deploy-mcp-proxy.ps1`](../infra/deploy-mcp-proxy.ps1):

```powershell
../infra/deploy-mcp-proxy.ps1 -SubscriptionId <sub> -SapResourceGroups RG_SAP_CUS_AB1,RG_SAP_AB2
```

That script creates the resource group, a managed identity with the custom **"SAP SRE Agent
Operator"** role on your SAP RGs, an ACR image build from this folder, a VNet-integrated Container
App, and prints the **MCP URL + API key** to register as a connector. Before relying on it in
production, still review: ingress auth hardening, timeouts, structured logging, and tests.

## Run locally

```bash
pip install -r requirements.txt
SUBSCRIPTION_ID="<sub-id>" python server.py
# serves Streamable-HTTP on http://localhost:8000/mcp
```

## Register with the SRE Agent

Install the **`sap-sre-proxy-ops`** plugin (it ships [`.mcp.json`](../plugins/sap-sre-proxy-ops/.mcp.json)),
then **Builder → Connectors → Add connector → MCP Server**, using your deployed server URL and the
API key/header. Once **Connected**, `sap-command-runner` and `sap-self-healing` use these tools.

## Migration note

The REST proxy in [`../proxy`](../proxy) keeps working during the transition. Once this MCP server is
deployed and validated, point the `sap-sre-proxy-ops` skills at the MCP tools and retire the REST
endpoints.
