---
name: sap-resiliency-assessment
description: "Evaluates SAP workload resiliency: availability zone coverage, HA architecture, load balancer redundancy, single points of failure, disk zone alignment, and zone migration readiness."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
    - SearchMemory
    - SearchIncidentKnowledge
    - MCP-MSLearnDocs_microsoft_docs_search
    - MCP-MSLearnDocs_microsoft_docs_fetch
    - GetArmResourceAsJson
    - RunAzCliReadCommands
    - CheckIfResourceExists
    - CheckTcpConnectivity
    - GetTlsSettings
    - PlotBarChart
    - PlotHeatmap
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

## When to Use

- "Can we survive a zone failure?"
- "Resiliency assessment for AB1"
- "Are our SAP VMs in availability zones?"
- "Single points of failure?"
- "Zone coverage analysis"

## Authentication

```python
import requests, json

# SUB_ID: Use subscription_id from Team Onboarding

# PROXY_URL: Use config_proxy_url from Team Onboarding
# PROXY_KEY: Use config_proxy_api_key from Team Onboarding

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
```

## Resiliency Checks (12 total)

| ID | Check | Source | PASS | FAIL |
|----|-------|--------|------|------|
| RES-01 | VMs deployed in availability zones | ARM VM `zones` property | All VMs zoned | Any VM without zone |
| RES-02 | Zone distribution (not all in same zone) | ARM VM | Spread across >=2 zones | All in 1 zone |
| RES-03 | HA pairs in different zones | ARM VM | DB primary/secondary in different zones | Same zone |
| RES-04 | Load Balancer zone redundancy | ARM LB `sku.name` + frontend zones | Zone-redundant Standard LB | Basic LB or single-zone |
| RES-05 | LB health probe configured | ARM LB probes | Probe exists with correct port | Missing or wrong port |
| RES-06 | LB HA ports enabled | ARM LB rules | HA ports rule | Individual port rules |
| RES-07 | Managed disks in same zone as VM | ARM Disk `zones` | Disk zone matches VM zone | Mismatch |
| RES-08 | No single point of failure (ASCS/ERS) | ARM VM | ASCS and ERS on separate VMs | Same VM |
| RES-09 | PPG for latency-sensitive VMs | ARM PPG | DB + App in same PPG | No PPG |
| RES-10 | Backup enabled on all VMs | ARM Recovery Services | All VMs protected | Unprotected VMs |
| RES-11 | DR strategy exists (HSR/backint/ASR) | AMS + ARM | HSR active or ASR configured | No DR |
| RES-12 | Resource locks on production VMs | ARM locks | CanNotDelete lock | No lock |

## Output Format

```
AB1 — Resiliency Score: 8/12 (67%)

  ✅ RES-01: All VMs in availability zones (Zone 1)
  ❌ RES-02: All VMs in SAME zone (Zone 1) — no zone redundancy
  ⚪ RES-03: N/A (no HA pair)
  ✅ RES-04: Standard LB, zone-redundant
  ...

  TOP GAPS:
  1. Single-zone deployment — zone failure = full outage
  2. No DR strategy configured
  
  RECOMMENDATION: Migrate to multi-zone with zone-redundant LB
```
