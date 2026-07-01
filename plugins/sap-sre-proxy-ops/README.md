# sap-sre-proxy-ops

Live read-only VM command execution and guard-railed self-healing remediation for SAP on Azure.
These skills reach SAP VMs through a brokered **MCP command proxy** (a VNet-integrated Container App
that calls the ARM run-command API behind a 14-command read-only allowlist), registered as the
`sap-sre-proxy` MCP connector.

This is **Tier 3 (+ Live Commands)** of the SAP Azure SRE Agent.

## Requires

- The **MCP command proxy** Container App deployed (see repository [README](../../README.md) →
  *Phase 2 — Live commands*), via [`../../infra/deploy-mcp-proxy.ps1`](../../infra/deploy-mcp-proxy.ps1).
- The **`sap-sre-proxy` MCP connector** registered in the SRE Agent, using the MCP endpoint URL and
  the `x-api-key` header. This plugin ships the [`.mcp.json`](.mcp.json) that describes it; the API
  key comes from the `SAP_SRE_MCP_API_KEY` setting.
- The MCP proxy UMI granted the custom **SAP SRE Agent Operator** RBAC role on each SAP resource group.

Install [`sap-sre-core`](../sap-sre-core) (and usually [`sap-sre-config`](../sap-sre-config)) first.

## Skills

| Skill | Purpose |
|-------|---------|
| `sap-command-runner` | 14 read-only VM commands via the MCP proxy |
| `sap-self-healing` | Log-volume / backup / sysctl-drift remediation within strict guardrails |

## Install

**Builder → Plugins → Install from URL** (or register the marketplace and pick this plugin):

```
https://github.com/<your-org>/sap-azure-sre-agent  →  plugin: sap-sre-proxy-ops
```

Each install is pinned to the exact commit. See the repository [README](../../README.md) for the
update model.
