# SRE Proxy — OPTIONAL (Phase 2)

> **This whole folder is optional.** The core solution (agent + core skills + config store) works
> **without** it. Deploy this only when you want **live read-only VM commands** and **self-healing**.

The SRE Proxy is a small FastAPI service (Azure Container App, VNet-integrated) that brokers an
**allowlisted set of read-only VM commands** through the ARM run-command API. It backs the
`sap-sre-proxy-ops` plugin (`sap-command-runner`, `sap-self-healing`).

## What it is NOT

- It is **not** in the config-read path. Stored configs are read **directly** from the `sap-configs`
  blob by the SRE Agent's own Managed Identity. The proxy has **no storage role**.
- It is **not** required by any other skill. Removing it only disables command-runner + self-healing.

## Deployment

Deployed by `../infra/deploy-sre-infra.ps1 -Mode Full` into its own resource group. See the
repository [README](../README.md) → *Phase 2*. Its identity (`sre-proxy-umi`) gets only the custom
command RBAC role on the SAP resource groups.

## Roadmap → MCP server

The target state for this proxy is a standalone **MCP server** registered as an SRE Agent
**connector** and shipped as a `.mcp.json` in the `sap-sre-proxy-ops` plugin (so customers "Add as
connector" from their fork). That makes the live-command tools natively discoverable and governed
(approval gates, tool selection) instead of a bespoke REST + API-key surface the skills call by hand.
Until that rebuild lands, this REST proxy remains the working, optional Phase-2 option.
