# sap-sre-proxy-ops

Live read-only VM command execution and guard-railed self-healing remediation for SAP on Azure.
These skills reach SAP VMs through a brokered **SRE Proxy** (a VNet-integrated Container App that
calls the ARM run-command API behind a 14-command read-only allowlist).

This is **Tier 3 (+ Live Proxy)** of the SAP Azure SRE Agent.

## Requires

- The **SRE Proxy** Container App deployed (see repository [README](../../README.md) → *Phase 2 —
  Infrastructure (+ Live Proxy)*).
- The **proxy URL** and **API key** supplied through **Team Onboarding** (Settings → Team
  Onboarding), not through an MCP connector — the proxy is a custom REST API authenticated with an
  `X-API-Key` header.
- The proxy UMI granted the custom **SAP SRE Agent Operator** RBAC role on each SAP resource group.

Install [`sap-sre-core`](../sap-sre-core) (and usually [`sap-sre-config`](../sap-sre-config)) first.

> **MCP connector (scaffold):** this plugin ships an [`.mcp.json`](.mcp.json) that describes the
> proxy's live-command MCP server. A scaffold server lives in [`proxy-mcp/`](../../proxy-mcp) — the
> target state is to run it as a connector so the live-command tools are natively discoverable and
> governed (approval gates, tool selection). Until it's deployed, the existing REST proxy (URL + API
> key via Team Onboarding) remains the working path.

## Skills

| Skill | Purpose |
|-------|---------|
| `sap-command-runner` | 14 read-only VM commands via the proxy |
| `sap-self-healing` | Log-volume / backup / sysctl-drift remediation within strict guardrails |

## Install

**Builder → Plugins → Install from URL** (or register the marketplace and pick this plugin):

```
https://github.com/<your-org>/sap-azure-sre-agent  →  plugin: sap-sre-proxy-ops
```

Each install is pinned to the exact commit. See the repository [README](../../README.md) for the
update model.
