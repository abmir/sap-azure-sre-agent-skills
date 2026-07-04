---
name: sap-maintenance-handler
description: "Handles Azure scheduled maintenance events. Detects VM reboot/redeploy events via Azure Scheduled Events API, coordinates graceful SAP shutdown, and validates recovery. Requires command proxy for VM operations."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - GetActivityLogsSummary
    - QueryLogAnalyticsByWorkspaceId
    - GetMetricTimeSeriesElementsForAzureResource
    - CreateScheduledMonitoringTask
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Config reads & proxy fallback**: Stored SAP/OS configs are read **directly from the `sap-configs` blob container using the agent's own Managed Identity** (`--auth-mode login`) — there is **no config proxy**. The MCP command proxy is optional and runs only **live VM commands**; if it is not deployed or errors (timeout, 5xx, unreachable), continue with stored blob configs + Azure-native sources (AMS, ARM API, Azure Monitor). Never block the skill on the proxy.

## Infrastructure Requirements

This skill adapts automatically based on what infrastructure is listed in the `## Deployed Infrastructure` section of Team Onboarding.

- **No infrastructure listed** — Can DETECT scheduled maintenance via the Scheduled Events API and Service Health, and can RECOMMEND the graceful-shutdown runbook. CANNOT execute any pre-checks or shutdown actions on the VMs. Reply with the recommended runbook and stop. **Always disclose**: "Maintenance detection only — I can detect the event and recommend the runbook, but cannot execute it. Deploy the MCP command proxy to enable autonomous handling."
- **Storage Account listed** — Also reads collected configs from the `sap-configs` blob container to pre-validate the system is in a safe state before the maintenance window (HSR sync OK, no long-running backup pinned in `global.ini`, etc.). Still cannot execute remediation actions without the proxy.
- **MCP command proxy also listed** — Also autonomously executes the allowlisted maintenance actions (`sap_stop_graceful`, `hdb_stop`, `hdb_start`, `sap_start`) through the proxy, subject to the T4 guardrails below.

## Tier: T4 — Autonomous Remediation

## When to Use

- Azure Scheduled Events API detects "Reboot" or "Redeploy" event
- User asks: "Handle upcoming maintenance for AB1"
- User asks: "Is there scheduled maintenance?"

## Guardrails

- **Only allowlisted actions**: `sap_stop_graceful`, `hdb_stop`, `hdb_start`, `sap_start`
- **Token budget**: max 5K tokens per invocation
- **Rate limit**: max 5 autonomous maintenance actions per day
- **Kill switch**: disable via Application Settings `T4_ENABLED=false`
- **Every action logged** to Application Insights + Teams notification within 60 seconds
- **HA systems**: trigger takeover BEFORE acknowledging maintenance event

## Authentication

**IMPORTANT — Azure API Access:** Do NOT use IMDS tokens (169.254.169.254) or ManagedIdentityCredential — they are not available in the agent sandbox. Instead:
- For Azure Resource Manager queries: Use the built-in `GetArmResourceAsJson` or `RunAzCliReadCommands` tools
- For Log Analytics queries: Use the built-in `QueryLogAnalyticsByWorkspaceId` tool  
- For metrics: Use the built-in `GetMetricTimeSeriesElementsForAzureResource` tool
- For live VM commands: invoke the **SAP Command Runner** skill (the `sap-sre-proxy` MCP connector) — never make direct HTTP or proxy calls

```python
# Use the built-in tools above for Azure API access.
import json
from datetime import datetime, timezone

# SUB_ID: Use subscription_id from Team Onboarding

# All VM commands are executed via the SAP Command Runner skill (MCP run_command / run_batch).
# This skill determines WHAT to do; SAP Command Runner does HOW. Never call the proxy directly.
# Stored configs (pre-checks) are read directly from the sap-configs blob with the agent MI
#   (`az storage blob ... --auth-mode login`) — only when a Storage Account is listed.
```

**To execute VM commands**: Invoke the **SAP Command Runner** skill. Do NOT call the command proxy directly.

Example invocations:
- Stop SAP: invoke Command Runner with command_id=`sap_stop_graceful`, sidadm=`<sid>adm`, sid=`<SID>`
- Stop HANA: invoke Command Runner with command_id=`hdb_stop`, sidadm=`<sid>adm`
- Start HANA: invoke Command Runner with command_id=`hdb_start`, sidadm=`<sid>adm`
- Start SAP: invoke Command Runner with command_id=`sap_start`, sidadm=`<sid>adm`, sid=`<SID>`
- Cluster maintenance on: invoke Command Runner with command_id=`crm_maintenance_on`
- Cluster maintenance off: invoke Command Runner with command_id=`crm_maintenance_off`

## Step 1: Check for Scheduled Events

The agent checks Azure Scheduled Events API via the SAP Command Runner skill:
```python
# Invoke SAP Command Runner with command_id="scheduled_events"
# This reads http://169.254.169.254/metadata/scheduledevents on the VM
def check_scheduled_events(vm_name, rg):
    # Invoke SAP Command Runner skill
    pass
```

Response format from Azure:
```json
{
  "Events": [{
    "EventId": "...",
    "EventType": "Reboot",
    "ResourceType": "VirtualMachine",
    "Resources": ["AB1vm"],
    "EventStatus": "Scheduled",
    "NotBefore": "2026-05-09T03:00:00Z"
  }]
}
```

## Topology handling (all 8 system types)

The action plan keys off `deployment` (see Step 2) and must also respect `architecture`:
- **standalone / distributed** → graceful stop → reboot → auto-start (no takeover).
- **high-availability / disaster-recovery** → if the VM is the HSR/ASCS **primary**, take over to the secondary first, then maintain.
- **scale-out** → coordinate **node-by-node** (drain/maintain one worker at a time, preserve quorum and the standby); never take multiple DB nodes down together.
- **disaster-recovery** → confirm the async DR replica is caught up before touching the primary site.

## Step 2: Determine System Type and Action

```python
def determine_maintenance_action(vm_name, event_type, landscape):
    """Determine the correct maintenance sequence based on topology (deployment field)."""
    system = find_system_for_vm(vm_name, landscape)
    deployment = system.get("deployment", "standalone")  # standalone | distributed | high-availability | disaster-recovery

    if deployment in ("standalone", "distributed"):
        # No cluster: graceful stop, let Azure reboot, auto-start after
        return {
            "pre_actions": ["sap_stop_graceful", "hdb_stop"],
            "post_actions": ["hdb_start", "sap_start"],
            "takeover": False
        }
    elif deployment in ("high-availability", "disaster-recovery"):
        # HA/DR: if this VM is the HSR/ASCS primary, trigger takeover to the secondary first
        vm = find_vm(system, vm_name)
        is_primary = vm.get("ha_role") == "primary"
        return {
            "pre_actions": ["sap_stop_graceful", "hdb_stop"],
            "post_actions": ["hdb_start", "sap_start"],
            "takeover": is_primary,
            "takeover_target": next((v["name"] for v in system["vms"] if v.get("ha_role") == "secondary"), None)
        }
```

## Step 3: Execute Graceful Shutdown

For standalone / distributed (no cluster):
1. Stop SAP: Invoke Command Runner with command_id=`sap_stop_graceful`, sidadm=`ab1adm`, sid=`AB1`
2. Stop HANA: Invoke Command Runner with command_id=`hdb_stop`, sidadm=`db1adm`
3. Acknowledge event to Azure (starts countdown)
4. Wait for reboot to complete (monitor VM power state via ARM)
5. Start HANA: Invoke Command Runner with command_id=`hdb_start`, sidadm=`db1adm`
6. Start SAP: Invoke Command Runner with command_id=`sap_start`, sidadm=`ab1adm`, sid=`AB1`
7. Validate: check process list, HANA availability

For high-availability / disaster-recovery (clustered) — for scale-out, repeat per DB node preserving quorum:
1. Verify HSR is in sync (query AMS)
2. Set maintenance mode: Invoke Command Runner with command_id=`crm_maintenance_on`
3. Stop SAP on target node
4. Stop HANA on target node (triggers automatic takeover to secondary)
5. Acknowledge event
6. After reboot: start HANA, re-register as secondary
7. Bring node online: Invoke Command Runner with command_id=`crm_maintenance_off`

## Step 4: Post-Maintenance Validation

1. Check VM power state = Running
2. Check HANA availability via AMS
3. Check SAP process list via command proxy
4. For HA: check HSR replication re-established
5. Run Configuration Guardian (T1 mode) to detect any drift

## Step 5: Notification

Send Teams notification with full action log:
```
✅ SAP Maintenance Autopilot — AB1

Azure scheduled maintenance handled automatically:
  03:00  Scheduled Event detected: Reboot on AB1vm (NotBefore: 03:15)
  03:01  SAP graceful shutdown initiated (AB1)
  03:02  HANA graceful shutdown initiated (DB1)
  03:03  Maintenance event acknowledged
  03:15  VM rebooted by Azure
  03:17  VM back online
  03:18  HANA started successfully (7 services GREEN)
  03:19  SAP started successfully (all processes GREEN)
  03:20  Config validation: 59/59 PASS (no drift)

Total downtime: 2 minutes (03:15 - 03:17)
Without autopilot: estimated 15-30 minutes unplanned downtime
```
