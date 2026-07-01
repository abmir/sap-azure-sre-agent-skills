# Architecture

## Architecture

![Azure SRE Agent for SAP Workloads — architecture](docs/sap-on-azure-sre-agent.png)

```
┌──────────────────────────────────────────────────────────┐
│ Azure SRE Agent (sre.azure.com)                          │
│  13 Custom Skills + 39 Built-in Skills                   │
│  Tools: ARM API, AMS KQL, Azure Monitor, CLI, Python     │
│  Knowledge: SAP landscape, SAP Notes, STAF references    │
└──────────┬────────────────┬────────────────┬─────────────┘
           │ Azure APIs   │ + Config Store │ + MCP Proxy
           │ (always on)  │ (storage)      │ (connector)
           ▼                ▼                ▼
   ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐
   │ Azure APIs  │  │ Config Store │  │ MCP Cmd Proxy    │
   │ + AMS / LAW │  │ (Storage)    │  │ (Container App)  │
   │ ARM, Cost   │  │ sap-configs/ │  │ run_command      │
   │ Monitor,    │  │   SID/host/  │  │ run_batch        │
   │ Advisor     │  │   latest/    │  │ 14-cmd allowlist │
   │             │  │ Agent UMI:   │  │ MCP + api-key    │
   │             │  │ Blob Reader  │  │ sre-mcp-umi      │
   └─────────────┘  └──────┬───────┘  └────────┬─────────┘
                           │ upload            │ ARM
                           │ (weekly cron +    │ run-command
                           │  on-demand)       │ (read-only)
                           ▼                   ▼
                   ┌────────────────────────────────────┐
                   │ SAP VMs                            │
                   │  /opt/sre/collect-sap-configs.sh   │
                   │  collector-umi (Blob Contributor)  │
                   └────────────────────────────────────┘
```

STAF check definitions live in the public [`Azure/sap-automation-qa`](https://github.com/Azure/sap-automation-qa) repo and are pulled live by the Config Validator skill (requires a Storage Account) — they are not hosted by Microsoft inside this stack.

## What It Does

| Capability | Example Prompt | Requires | How It Works |
|-----------|----------------|:--------:|-------------|
| Landscape discovery | "What SAP systems do I have?" | — | Azure Resource Graph |
| Health monitoring | "Is AB1 healthy?" | — | AMS telemetry + Azure Monitor + ARM API |
| Cost analysis | "How much does AB1 cost?" | — | Azure Cost Management API |
| Trend analysis | "Show memory trends for AB1" | — | AMS time-series with regression |
| Resiliency assessment | "Assess AB1 resiliency" | — | Azure Advisor + ACSS checks |
| Deployment readiness | "Can I deploy Standard_M32 in eastus?" | — | SKU / quota / HANA certification |
| Incident analysis (basic) | "Why did HANA restart?" | — | AMS + Activity Log correlation |
| Performance diagnostics (basic) | "Why is AB1 slow?" | — | AMS + Azure Monitor metrics |
| HA cluster health (basic) | "Cluster status for AB1" | — | AMS + Resource Graph |
| Maintenance handler (basic) | "Any scheduled maintenance?" | — | Scheduled Events API + Service Health |
| **Config validation (STAF)** | "Run config checks for AB1" | **Storage Account** | STAF YAML fetched live from `Azure/sap-automation-qa` on GitHub, compared against blob-stored VM configs — entirely in-skill, no proxy |
| **Config-enriched RCA / perf / cluster** | "Cross-layer RCA for AB1" | Storage Account | Adds stored config files (sysctl, global.ini, corosync) to incident / performance / HA skills |
| **Live VM commands** | "Run uptime on AB1vm" | **MCP command proxy** | Proxy → ARM run-command API (14-command allowlist, read-only) |
| **Self-healing remediation** | Auto: `/hana/log` full → log backup | MCP command proxy | Proxy executes restricted write commands within strict guardrails |

