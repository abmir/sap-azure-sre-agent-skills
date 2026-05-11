# SAP Azure SRE Agent Skills

Azure SRE Agent skills for SAP workloads on Azure. 15 custom skills organized across 4 operational tiers: **Observe, Diagnose, Prevent, Heal**.

## Why Tiers?

Traditional monitoring tools generate alerts — but alerts alone don't fix problems. The tier model progressively increases agent autonomy while maintaining human control:

```
T1  Observe & Inform     →  "What's happening?"      Agent reads, reports, never changes anything
T2  Diagnose & Explain   →  "Why did it break?"      Agent correlates logs, metrics, events into RCA
T3  Predict & Prevent    →  "What's about to break?" Agent detects drift/trends, recommends fix, WAITS for approval
T4  Act & Heal           →  "Fix it now"             Agent executes guardrailed actions in time-critical scenarios
```

**Key design principle:** T1 and T2 are always safe (read-only). T3 requires human approval before any change. T4 runs autonomously but is rate-limited (e.g., max 5 actions/day) and restricted to a hardcoded allowlist of safe operations.

This matters for **AAU (Agent Activity Unit) cost optimization**: most user queries are T1 (4–6 API calls, ~500 tokens). Only incident investigations (T2) or remediation workflows (T3/T4) consume more. A well-tuned agent spends 80% of its AAU budget on T1 observability queries.

## Quick Start

1. **Fork this repo** to your organization
2. **Deploy infrastructure** — follow [docs/deployment-guide.md](docs/deployment-guide.md) (storage, function apps, managed identity, RBAC)
3. **Fill in** `config/config.template.yaml` → save as `config/config.<your-org>.yaml`
4. **Fill in** `config/sap-landscape-inventory.template.json` → save as `config/sap-landscape-inventory.json`
5. **Set up SAP VM collectors** — deploy `collector/collect-sap-configs.sh` + cron job on each SAP VM
6. **Create an Azure SRE Agent** at [sre.azure.com](https://sre.azure.com)
7. **Connect Knowledge Sources** — add this GitHub repo as a Knowledge Source (agent reads configs, inventory, and docs automatically)
8. **Install skills via Plugin Marketplace** — add this repo as a marketplace source, browse all 15 skills, and import with one click. Alternatively, upload each `SKILL.md` from `skills/` to Skill Builder manually.
9. **Configure Team Onboarding** — fill in `onboarding/team-onboarding.template.md` with your config values and paste into the agent's Team Onboarding

> **Plugin Marketplace:** This repo includes a `.github/plugin/marketplace.json` manifest. Register it as a [Plugin Marketplace source](https://learn.microsoft.com/en-us/azure/sre-agent/plugin-marketplace) to browse, install, and update all 15 skills with version tracking and SHA-256 content hashing — no manual copy-pasting.

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
├── collector/                       # SAP VM config collector + cron setup
│   ├── collect-sap-configs.sh
│   └── deploy-and-collect.ps1
├── infra/                           # Automated infrastructure deployment
│   └── deploy-sre-infra.ps1
└── docs/                            # Architecture, strategy, deployment guide
```

## Prerequisites

- Azure subscription with SAP workloads (HANA, NetWeaver)
- Azure Monitor for SAP Solutions (AMS) configured
- Azure SRE Agent (created at sre.azure.com)
- Two Azure Function Apps (config-proxy + command-proxy) deployed

> **Full step-by-step setup:** See [docs/deployment-guide.md](docs/deployment-guide.md) for infrastructure setup, SAP VM collector, and agent configuration.

## Security Model

- **Managed Identity (MI)** — all Azure API calls authenticate via the agent's MI. No passwords or service principals.
- **Command allowlist** — VM commands go through a hardened proxy with 14 pre-approved read-only commands. No arbitrary script execution on SAP VMs.
- **Input sanitization** — all user-supplied parameters (`sidadm`, `instance`, `sid`, `filepath`) are validated with strict regex patterns and shell-quoted (`shlex.quote`) before execution.
- **Path traversal protection** — config proxy validates blob paths against directory traversal attacks (`../`, absolute paths, invalid characters).
- **Least privilege RBAC** — agent MI gets Reader only. Proxy MI gets a custom role limited to `virtualMachines/runCommand/action` + `virtualMachines/read`.
- **Per-agent API keys** — each SRE Agent instance authenticates with its own `AGENT_KEY_*` app setting. Rotate by updating the setting.
- **No hardcoded secrets** — all environment-specific values come from app settings or Team Onboarding. No credentials in code or skill files.
- **Network isolation** — storage account firewall set to Deny by default. Only VNet-integrated function apps and SAP VM subnets can access config data.
- **Generic error responses** — proxy functions return sanitized error messages to prevent leaking internal infrastructure details.
- **Audit trail** — every command execution is logged to Application Insights with structured JSON: caller ID, command, target VM, result, and latency.

## Cost Optimization (AAU Efficiency)

- **Data reuse** — all 15 skills include instructions to reuse landscape registry, VM configs, and AMS query results already in the conversation context. No redundant API calls.
- **Batch queries** — Performance Diagnostics batches 6 HANA checks into a single KQL query instead of running them individually.
- **Response caching** — config proxy caches blob responses in-memory (1-hour TTL). Repeat queries within the same hour hit cache, not storage.
- **Tiered autonomy** — T1/T2 skills are read-only (4–6 API calls each). T3/T4 skills only activate when needed and are rate-limited (max 5 autonomous actions/day).
- **System criticality tagging** — landscape inventory supports `criticality` field (`production`, `non-production`, `dev`) so T4 skills can apply stricter guardrails on production systems.

## Reliability & Resilience

- **Proxy fallback** — all skills include fallback instructions: if config or command proxy is unreachable, continue with Azure-native data sources (AMS, ARM API, Azure Monitor) and inform the user.
- **Collector robustness** — all HANA and cluster commands in the collector script are wrapped with `timeout 15` to prevent hangs when services are down. Hostname validation prevents malformed blob paths.
- **Idempotent collection** — weekly cron uploads are idempotent. Re-running the collector overwrites `latest/` with fresh data; historical snapshots are preserved in dated directories.
- **Log rotation** — collector log file (`/var/log/sre-config-collect.log`) uses logrotate with 12-week retention to prevent disk fill on SAP VMs.
- **Offline resilience** — config snapshots in blob storage remain accessible even when SAP VMs are shut down (auto-shutdown schedules, maintenance windows), enabling RCA on unavailable systems.

## Updates

Microsoft releases skill updates as PRs to this repo. Customers review, test, and merge at their own pace.

## License

Microsoft Internal — Not for redistribution without approval.
