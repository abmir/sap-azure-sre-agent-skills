# sap-sre-core

10 Azure-native SAP on Azure SRE skills. **No extra infrastructure required** — every skill
works immediately using Azure APIs and Azure Monitor for SAP Solutions (AMS) telemetry. All
skills are read-only.

This is **Tier 1 (Azure-Native)** of the SAP Azure SRE Agent. Install it on any SRE Agent that
has `Reader` + `Monitoring Reader` on your SAP resource groups and a Log Analytics connector to
your AMS workspace.

## Skills

| Skill | Purpose |
|-------|---------|
| `sap-landscape-discovery` | System inventory from Azure Resource Graph |
| `sap-operational-health` | 5-layer health dashboard (VM, SAP, HANA, AMS, ARM) |
| `sap-cost-analysis` | Cost breakdown + savings (Cost Management API) |
| `sap-trend-analysis` | AMS time-series trend projection |
| `sap-resiliency-assessment` | Advisor + ACSS reliability checks |
| `sap-deployment-readiness` | SKU / quota / HANA certification checks |
| `sap-incident-analysis` | Cross-layer RCA (enhanced when a Storage Account is present) |
| `sap-performance-diagnostics` | HANA memory / disk / savepoint analysis (enhanced with Storage) |
| `sap-ha-cluster-health` | Pacemaker / HSR status (enhanced with Storage) |
| `sap-maintenance-handler` | Graceful maintenance handling (enhanced with Storage) |

The last four skills automatically light up additional capability when the
[`sap-sre-config`](../sap-sre-config) plugin and a customer Storage Account are also present.

## Install

**Builder → Plugins → Install from URL** (or register the marketplace and pick this plugin):

```
https://github.com/<your-org>/sap-azure-sre-agent  →  plugin: sap-sre-core
```

Each install is pinned to the exact commit. See the repository [README](../../README.md) for the
full setup guide and the update model.
