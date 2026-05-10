---
name: sap-configuration-guardian
description: "Unified SAP configuration validation aligned with STAF. 59 checks across infrastructure, storage, OS/SAP parameters, and cluster. In T1 mode: audit and report drift. In T3 mode (post-reboot trigger): detect drift, recommend fix, on approval apply sysctl.d and re-validate."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
    - SearchMemory
    - SearchIncidentKnowledge
    - MCP-MSLearnDocs_microsoft_docs_search
    - MCP-MSLearnDocs_microsoft_docs_fetch
    - GetAppSetting
    - GetArmResourceAsJson
    - RunAzCliReadCommands
    - GetTlsSettings
    - CheckTcpConnectivity
    - CheckIfResourceExists
    - GetActivityLogsSummary
    - QueryLogAnalyticsByWorkspaceId
    - PlotBarChart
    - PlotHeatmap
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## Mode Selection

- **User asks** "Run config checks on AB1" → **T1 mode** (audit, report drift, read-only)
- **Activity Log trigger** "VM restarted" or **scheduled daily** → **T3 mode** (detect drift, recommend fix, await approval, execute, re-validate)
- **Critical drift in T1** (e.g., stonith-enabled=false) → **auto-escalate to T3** (alert immediately)

## When to Use

- "Run config checks for AB1" / "Validate configuration for AB3"
- "STAF checks for HSO" / "Run all configuration checks"
- "Check infra config" / "Extended OS checks"
- Post-reboot auto-trigger (T3 mode)

## Data Sources

| Category | Source | Freshness |
|----------|--------|-----------|
| STAF 1A Infrastructure | Azure ARM API | Live |
| STAF 1B Storage | ARM API + Blob: `stsreconfigscus/sap-configs/<SID>/<host>/` | Live + cron |
| STAF 1C OS/SAP Params | Blob: same path | Cron collected |
| STAF 1D Cluster | Blob: same path | Cron collected |
| SBD/Fencing | Blob: same path | Cron collected |
| Extended | Blob + ARM API (LB checks) | Cron + Live |

## Authentication

```python
import requests, json, re

# SUB_ID: Use subscription_id from Team Onboarding

# PROXY_URL: Use config_proxy_url from Team Onboarding
# PROXY_KEY: Use config_proxy_api_key from Team Onboarding

# VM commands are executed via the SAP Command Executor skill (never directly)
# When T3 mode needs to apply a fix, instruct the agent to invoke SAP Command Executor

def get_mi_token(resource):
    resp = requests.get("http://169.254.169.254/metadata/identity/oauth2/token",
        params={"api-version": "2019-08-01", "resource": resource},
        headers={"Metadata": "true"}, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]

def arm_get(path, api_version="2023-09-01"):
    token = get_mi_token("https://management.azure.com/")
    url = f"https://management.azure.com{path}?api-version={api_version}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    resp.raise_for_status()
    return resp.json()

def get_landscape_registry():
    resp = requests.get(f"{PROXY_URL}/registry", headers={"x-api-key": PROXY_KEY}, timeout=30)
    return resp.json() if resp.status_code == 200 else None

def get_vm_configs(sid, hostname):
    resp = requests.get(f"{PROXY_URL}/configs/{sid}/{hostname}", headers={"x-api-key": PROXY_KEY}, timeout=60)
    return resp.json().get("files", {}) if resp.status_code == 200 else {}
```

## Check Categories (59 total)

### 1A: Infrastructure Validation (7 checks)
- VM SKU SAP/HANA certification
- Accelerated Networking enabled
- Disk IOPS/MBPS for /hana/data (>=7000 IOPS, >=400 MBPS)
- Disk IOPS/MBPS for /hana/log (>=2000 IOPS, >=250 MBPS)
- Write Accelerator for /hana/log
- Load Balancer: HA ports, floating IP, health probe, idle timeout
- Premium SSD / Ultra SSD / ANF validation

### 1B: Storage Configuration (8 checks)
- /hana/data filesystem type (xfs/nfs/nfs4)
- /hana/log filesystem type (xfs/nfs/nfs4)
- /hana/data stripe size = 256k
- /hana/log stripe size = 64k
- Swap ~2GB (2046-2049 MB)
- fstrim disabled (SUSE)
- /hana/shared mount options
- /usr/sap mount

### 1C: OS/SAP Parameters (22 checks)
- vm.swappiness = 10
- net.ipv4.tcp_timestamps (0 for HA, 1 for non-HA)
- net.ipv4.tcp_rmem, tcp_wmem (premium vs ANF values)
- net.core.rmem_max, wmem_max
- net.core.netdev_max_backlog
- net.ipv4.tcp_slow_start_after_idle = 0
- net.ipv4.tcp_max_syn_backlog >= 8192
- net.ipv4.ip_local_port_range = "9000 65499"
- THP disabled (enabled=[never])
- I/O scheduler (none/noop)
- waagent.conf (ResourceDisk.EnableSwap=n)
- SAP User Limits (@sapsys nofile >= 65536)
- HANA global.ini (log_mode, basepath, HA/DR hooks)
- SAP Profiles (SAPSYSTEMNAME, SAPDBHOST)
- Hosts file (hostname resolves)
- DefaultTasksMax = 4096
- Time sync (chrony/ntp)

### 1D: Cluster Configuration (15 checks)
- stonith-enabled = true
- stonith-action = reboot
- stonith-timeout 144-900
- concurrent-fencing = true (AFA)
- Corosync: token=30000, join=60, consensus=36000, max_messages=20, expected_votes=2, two_node=1
- SBD: SBD_PACEMAKER=yes, SBD_STARTMODE=always, softdog loaded
- PREFER_SITE_TAKEOVER = true
- AUTOMATED_REGISTER = true

### Extended (7 checks)
- Tuned profile (sap-hana)
- Network drivers (AccelNet via ethtool)
- HANA HA/DR provider hooks registered
- Azure Fence Agent MSI
- LB health probe configuration
- icm/server_port_0
- rdisp/wp_no_dia

## T3 Mode: Auto-Remediation Flow

When drift is detected in T3 mode:

1. **Detect**: Run full check suite, identify FAIL items
2. **Classify**: Separate auto-fixable (sysctl, services) from manual-fix (disk resize, LB config)
3. **Recommend**: Generate remediation plan with exact commands
4. **Approve**: Send Teams Adaptive Card or display approval prompt
5. **Execute**: On approval, call command proxy with `sysctl_apply` command
6. **Validate**: Re-run failed checks to confirm fix
7. **Log**: Record drift + fix in agent memory

**To execute the fix**: Invoke the **SAP Command Executor** skill with:
- vm_name: the target VM
- rg: the resource group
- command_id: `sysctl_apply`

Do NOT call the command proxy directly from this skill.

## Output Format

Per-system compliance report:
```
AB1 — Config Compliance: 55/59 PASS, 2 WARN, 2 FAIL
  1A Infrastructure: 7/7 ✅
  1B Storage:        7/8 ⚠️ (stripe size /hana/log = 128k, expected 64k)
  1C OS/SAP Params:  20/22 ❌ (vm.swappiness=60, THP=always)
  1D Cluster:        N/A (non-HA)
  Extended:          6/7 ⚠️ (tuned profile: virtual-guest, expected sap-hana)

  [T3 mode]: 2 auto-fixable items found. Remediate? [Apply / Deny]
```
