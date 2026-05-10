# SAP Azure SRE Agent Skills

Azure SRE Agent skills for SAP workloads on Azure. 15 custom skills organized across 4 operational tiers: **Observe, Diagnose, Prevent, Heal**.

## Quick Start

1. **Fork this repo** to your organization
2. **Fill in** `config/config.template.yaml` → save as `config/config.<your-org>.yaml`
3. **Create an Azure SRE Agent** at [sre.azure.com](https://sre.azure.com)
4. **Upload skills** from `skills/` folder to the agent's Skill Builder
5. **Paste onboarding** from `onboarding/team-onboarding.template.md` into Team Onboarding (replace placeholders with your config values)
6. **Upload** `sap-landscape-inventory.json` to Knowledge Sources
7. **Deploy proxy functions** from `proxy/` folder to your Azure subscription

## Skill Catalog

| Skill | Tier | Agent Behavior |
|-------|------|---------------|
| SAP Landscape Discovery | T1 | Read-Only |
| SAP Deployment Readiness | T1 | Read-Only |
| SAP Operational Health | T1 | Read-Only |
| SAP Configuration Guardian | T1 + T3 | Read-Only or Semi-Auto |
| SAP Incident RCA | T2 | Read-Only + Integrations |
| SAP Performance Diagnostics | T1 | Read-Only |
| SAP HA & DR Guardian | T1 + T3 | Read-Only or Semi-Auto |
| SAP Resiliency Assessment | T1 | Read-Only |
| SAP Cost Insights | T1 | Read-Only |
| SAP Anomaly Forecaster | T3 | Semi-Autonomous |
| SAP Maintenance Autopilot | T4 | Fully Autonomous |
| SAP Self-Healing | T4 | Fully Autonomous |
| SAP Command Executor | T1-T4 | User + Agent-Internal |
| SAP ServiceNow Connector | Agent-Internal | Conditional |
| SAP APM Connector | Agent-Internal | Conditional |

## Tier Framework

| Tier | Name | Agent Behavior | What It Does |
|------|------|---------------|-------------|
| T1 | Observe & Inform | Read-Only | Instant visibility into SAP health, compliance, performance, costs |
| T2 | Diagnose & Explain | Read-Only (auto-triggered) | Cross-layer RCA in seconds when incidents occur |
| T3 | Predict & Prevent | Semi-Autonomous | Trend analysis, anomaly detection, remediation with approval |
| T4 | Act & Heal | Fully Autonomous | Time-critical scenarios with guardrailed actions |

## Repository Structure

```
├── skills/                          # 15 SRE Agent skills (generic, no hardcoded values)
│   ├── sap-landscape-discovery/
│   ├── sap-deployment-readiness/
│   ├── sap-operational-health/
│   ├── sap-configuration-guardian/
│   ├── sap-incident-rca/
│   ├── sap-performance-diagnostics/
│   ├── sap-ha-dr-guardian/
│   ├── sap-resiliency-assessment/
│   ├── sap-cost-insights/
│   ├── sap-anomaly-forecaster/
│   ├── sap-maintenance-autopilot/
│   ├── sap-self-healing/
│   ├── sap-command-executor/
│   ├── sap-servicenow-connector/
│   └── sap-apm-connector/
├── config/                          # Environment configuration templates
│   └── config.template.yaml
├── onboarding/                      # Team onboarding template
│   └── team-onboarding.template.md
├── proxy/                           # Azure Function proxy code
│   ├── sre-config-proxy/
│   └── sre-command-proxy/
└── docs/                            # Architecture and strategy docs
```

## Prerequisites

- Azure subscription with SAP workloads (HANA, NetWeaver)
- Azure Monitor for SAP Solutions (AMS) configured
- Azure SRE Agent (created at sre.azure.com)
- Two Azure Function Apps (config-proxy + command-proxy) deployed

> **Full step-by-step setup:** See [docs/deployment-guide.md](docs/deployment-guide.md) for infrastructure setup, SAP VM collector, and agent configuration.

## Security Model

- **Managed Identity (MI)** — all Azure API calls authenticate via the agent's MI
- **Proxy functions** — VM commands go through a hardened proxy with a fixed allowlist
- **Least privilege RBAC** — Reader + Log Analytics Reader + Monitoring Reader on SAP resource groups
- **No hardcoded secrets** — API keys in app settings, MI tokens at runtime
- **ServiceNow/APM integrations** — conditional, dormant until configured

## Updates

Microsoft releases skill updates as PRs to this repo. Customers review, test, and merge at their own pace.

## License

Microsoft Internal — Not for redistribution without approval.
