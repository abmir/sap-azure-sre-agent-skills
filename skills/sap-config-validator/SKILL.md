---
name: sap-config-validator
description: "Validates SAP system configurations against Microsoft's SAP Testing Automation Framework (STAF) check definitions. Calls the proxy /api/validate endpoint which handles everything server-side: fetches STAF checks from GitHub, collects fresh config data from the VM, compares actual vs expected, returns a structured compliance report. Read-only."
tools:
    - ExecutePythonCode
---

## When to Use

- "Run config checks for AB1" / "Validate configuration for AB3"
- "STAF checks for HSO" / "Run all configuration checks"
- "Check OS parameters" / "Check HANA configuration"

## Architecture

The proxy does ALL the heavy lifting server-side:

1. **STAF check definitions** — fetched from GitHub (`Azure/sap-automation-qa`), cached 1h, with blob snapshot fallback
2. **Config data** — triggers the collector on the VM for fresh data, falls back to cached blob if collector fails
3. **Comparison** — compares actual config values against STAF expected values (string, range, list validators)
4. **Report** — returns structured JSON with pass/fail/not_evaluated for every applicable check

The agent's only job: **call one endpoint, format the output.**

### CRITICAL Rules

1. **NEVER invent or fabricate checks.** Every check ID comes from the proxy response (STAF YAML). If the proxy returns 0 checks, report the failure and stop.
2. **NEVER deploy anything.** No collector deployment, no VM commands, no infrastructure changes.
3. **NEVER use IMDS or ManagedIdentityCredential** — use API key from Team Onboarding context.
4. **Present the results exactly as returned** — do not add, remove, or modify check results.

## Execution

### Step 1: Call the Validate Endpoint

Use `ExecutePythonCode` to call the proxy's `/api/validate` endpoint. The proxy handles STAF fetch, config collection, comparison, and reporting — all server-side.

```python
import requests, json

# Values from Team Onboarding context:
PROXY_URL = "..."   # Proxy URL
API_KEY = "..."     # API key

resp = requests.get(
    f"{PROXY_URL}/api/validate/AB1/AB1vm",
    headers={"X-API-Key": API_KEY},
    params={
        "os_type": "SLES_SAP",         # from landscape inventory
        "roles": "DB,SCS,PAS",         # from landscape inventory
        "db_type": "HANA",             # from landscape inventory
        "storage_type": "Premium_LRS", # from landscape inventory
        "ha_type": "false",            # from landscape inventory
        "ha_agent": "none",            # from landscape inventory
        "rg": "RG_SAP_CUS_AB1",       # VM resource group
    },
    timeout=300,  # collector takes ~90s
)
report = resp.json()
print(json.dumps(report, indent=2))
```

**Parameters** (from Team Onboarding landscape inventory):

| Parameter | Description | Example |
|-----------|-------------|---------|
| `{sid}` | SAP System ID (URL path) | `AB1` |
| `{hostname}` | VM hostname (URL path) | `AB1vm` |
| `os_type` | OS type | `SLES_SAP` or `REDHAT` |
| `roles` | Comma-separated VM roles | `DB,SCS,PAS` |
| `db_type` | Database type | `HANA` or `Db2` |
| `storage_type` | Storage type | `Premium_LRS`, `ANF`, `UltraSSD_LRS` |
| `ha_type` | HA configuration | `false`, `scale_up`, `scale_out` |
| `ha_agent` | Fencing agent | `none`, `AFA`, `ISCSI` |
| `rg` | VM resource group (enables fresh collection) | `RG_SAP_CUS_AB1` |

### Step 2: Format and Present Results

The proxy returns a structured JSON report:

```json
{
  "data_sources": {
    "staf": {"source": "github_live", "total": 188},
    "config": {"source": "freshly_collected", "file_count": 58, "timestamp": "..."}
  },
  "staf": {"total": 188, "filtered_out": {"ha": 32, "storage": 16, "os": 10, "db_type": 25}, "applicable": 105},
  "summary": {"pass": 12, "fail": 6, "not_evaluated": 56, "data_collection": 31},
  "failures": [{"id": "DB-HANA-0024", "name": "sysctl vm.swappiness", "severity": "HIGH",
                 "details": {"actual": "60", "expected": "10"}, "references": {"sap": "3024346"}}],
  "results": [...]
}
```

**Format the output as:**

```
AB1 — STAF Config Compliance Report

  Data Sources:
    STAF checks:  GitHub (live, 188 total)       ← or "blob cached (synced ...)"
    Config data:  freshly collected (23:45Z)      ← or "cached (last collected 21:20Z)"

  Check Coverage:
    Total STAF checks:    188
    Filtered out:          -83 (HA: 32, Db2: 25, storage: 16, OS: 10)
    Applicable:            105
    ├─ Evaluated:           18 (12 pass, 6 fail)
    ├─ Not evaluated:       56 (config mapping pending)
    └─ Data collection:     31 (info only)

  ══════════════════════════════════════
  FAILURES (from report.failures):

  ❌ DB-HANA-0024 vm.swappiness: actual=60, expected=10 (HIGH)
     Ref: SAP Note 3024346
  ...

  ══════════════════════════════════════
  SUMMARY: 12/18 PASS, 6 FAIL
```

**Rules for formatting:**
- Show `data_sources` first so the user knows data freshness
- List ALL failures from `report.failures` — do not add or remove any
- Show the filter breakdown so users understand why 188 → 105
- Do NOT generate additional checks or expected values
- If `config.source` is `cached_fallback`, report the actual `collector_result.error` from the response — do NOT guess reasons (e.g., never say "VM may be stopped" unless you verified it via ARM API)

## References
- [SAP Testing Automation Framework (STAF)](https://github.com/Azure/sap-automation-qa)
- [STAF Check Definitions](https://github.com/Azure/sap-automation-qa/tree/main/src/roles/configuration_checks/tasks/files)
