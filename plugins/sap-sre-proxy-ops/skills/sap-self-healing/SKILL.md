---
name: sap-self-healing
description: "Handles time-critical SAP scenarios within strict guardrails. Detects log volume full, backup staleness, and sysctl drift after unplanned reboots. Requires command proxy for remediation actions."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - QueryLogAnalyticsByWorkspaceId
    - GetMetricTimeSeriesElementsForAzureResource
    - GetActivityLogsSummary
    - CreateScheduledMonitoringTask
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## Infrastructure Requirements

This skill **requires an SRE Proxy** in the `## Deployed Infrastructure` section of Team Onboarding for any remediation action. Detection-only behavior works without the proxy, but the skill cannot autonomously fix anything without it.

- **If no SRE Proxy is listed** — Respond exactly: "Automated self-healing requires the SRE Proxy (Container App). No SRE Proxy is listed in Deployed Infrastructure. I can DETECT issues from AMS / Activity Log / Azure Monitor but cannot REMEDIATE them without the proxy. Run `infra/deploy-sre-infra.ps1 -Mode Full` to enable remediation. Until then, this skill will only alert — not auto-act." Then stop. Do NOT attempt to invoke any proxy URL.
- **If SRE Proxy is listed** — Run the full flow below. T4 guardrails apply (allowlist, rate limit, kill switch).

## Tier: T4 — Autonomous Remediation

## Guardrails

- **Fixed allowlist of actions** — NO arbitrary shell commands:
  - `sysctl_apply` — apply sysctl.d config file
  - `hana_log_backup` — trigger HANA log backup
  - `hana_log_cleanup` — purge old backup catalog entries
- **Token budget**: max 5K tokens per invocation
- **Rate limit**: max 20 autonomous actions per day
- **Kill switch**: disable via Application Settings `T4_ENABLED=false`
- **Every action logged** to Application Insights with before/after state
- **Teams notification** within 60 seconds of every action
- **Severity escalation**: if 3+ actions on same system within 1 hour → escalate to human

## When to Use (Auto-Triggered)

| Trigger | Detection Source | Action |
|---------|-----------------|--------|
| /hana/log >90% full | Azure Monitor disk % or Anomaly Forecaster | Trigger log backup + catalog cleanup |
| Backup stale >48h | AMS `SapHana_BackupCatalog_CL` or Azure Monitor | Trigger on-demand HANA backup |
| Sysctl drift after unplanned reboot | Activity Log (VM restart) + Config Guardian | Auto-apply sysctl.d configs |

## Authentication

**IMPORTANT — Azure API Access:** Do NOT use IMDS tokens (169.254.169.254) or ManagedIdentityCredential — they are not available in the agent sandbox. Instead:
- For Azure Resource Manager queries: Use the built-in `GetArmResourceAsJson` or `RunAzCliReadCommands` tools
- For Log Analytics queries: Use the built-in `QueryLogAnalyticsByWorkspaceId` tool  
- For metrics: Use the built-in `GetMetricTimeSeriesElementsForAzureResource` tool
- For proxy HTTP calls: Use `ExecutePythonCode` with `X-API-Key` header (API key from Team Onboarding)

```python
# Only use ExecutePythonCode for proxy HTTP calls. Use built-in tools for Azure API access.
import requests, json
from datetime import datetime, timedelta, timezone

# SUB_ID: Use subscription_id from Team Onboarding
# AMS_WORKSPACE_ID: Use ams_workspace_id from Team Onboarding

# PROXY_URL: Use config_proxy_url from Team Onboarding
# PROXY_KEY: Use config_proxy_api_key from Team Onboarding

# All VM commands are executed via the SAP Command Executor skill
# This skill determines WHAT to do, Command Executor does HOW
```

## Scenario 1: Log Volume Full

**Trigger**: /hana/log utilization >90% (from Azure Monitor or Anomaly Forecaster)

**To execute VM commands**: Invoke the **SAP Command Executor** skill. Do NOT call the command proxy directly.

```python
def handle_log_volume_full(vm_name, rg, sidadm):
    """Emergency log volume management — invokes Command Executor skill."""
    # Step 1: Invoke Command Executor with command_id=hana_log_backup
    # Step 2: Invoke Command Executor with command_id=hana_log_cleanup
    # Step 3: Invoke Command Executor with command_id=df_hana to verify
    pass

    # Step 4: Notify
    return {
        "action": "log_volume_cleanup",
        "status": "completed",
        "space_freed": "parse from df output",
        "notification": "Teams notification sent"
    }
```

## Scenario 2: Backup Staleness

**Trigger**: Last successful HANA backup >48h ago

```python
def check_backup_freshness(sid):
    """Check when last successful backup was."""
    query = f"""
    SapHana_BackupCatalog_CL
    | where SID_s == "{sid}"
    | where STATE_NAME_s == "successful"
    | summarize LastBackup=max(UTC_END_TIME_t) by SID_s, ENTRY_TYPE_NAME_s
    | order by LastBackup desc
    """
    # Pseudocode — use QueryLogAnalyticsByWorkspaceId tool instead
    return query_log_analytics(query, timespan_hours=72)

def handle_stale_backup(vm_name, rg, sidadm):
    """Trigger on-demand backup when backup is stale — invokes Command Executor skill."""
    # Invoke SAP Command Executor with command_id=hana_log_backup, sidadm=sidadm
    return {"action": "on_demand_backup", "status": "triggered"}
```

## Scenario 3: Sysctl Drift After Reboot

**Trigger**: Activity Log shows VM restart + Config Guardian detects sysctl drift

```python
def handle_sysctl_drift(vm_name, rg):
    """Auto-apply sysctl.d configs after drift detected — invokes Command Executor skill."""
    # Step 1: Invoke SAP Command Executor with command_id=sysctl_apply
    # Step 2: Re-validate via Config Guardian T1 mode
    pass

    # Step 2: Verify by re-reading sysctl values
    # (Config Guardian T1 mode re-check)

    return {"action": "sysctl_apply", "status": "completed"}
```

## Audit Log Format

Every autonomous action is logged:
```json
{
    "timestamp": "2026-05-09T03:14:00Z",
    "skill": "sap-self-healing",
    "tier": "T4",
    "system": "AB1",
    "vm": "AB1vm",
    "trigger": "log_volume_90_percent",
    "action": "hana_log_backup + hana_log_cleanup",
    "before_state": "/hana/log 92% used",
    "after_state": "/hana/log 41% used",
    "tokens_used": 3200,
    "duration_seconds": 45,
    "notification_sent": true
}
```

## Output Format (Teams Notification)

```
🤖 SAP Self-Healing — AB1

Action: Log volume emergency cleanup
Trigger: /hana/log at 92% (threshold: 90%)

Steps executed:
  1. ✅ HANA log backup triggered
  2. ✅ Backup catalog cleanup (entries >7 days removed)
  3. ✅ /hana/log now at 41% (freed 51%)

No human intervention required.
Token cost: $0.03 (3,200 tokens)
```
