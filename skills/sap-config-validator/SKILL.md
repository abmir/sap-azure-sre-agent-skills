---
name: sap-config-validator
description: "Validates SAP system configurations against Microsoft's SAP Testing Automation Framework (STAF). Pulls STAF check definitions live from the public Azure/sap-automation-qa GitHub repo, reads collected VM configs from the customer's sap-configs blob container, and runs the comparison entirely in-skill (no proxy involved). Requires Mode 2 or Mode 3. Read-only."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
---

## When to Use

- "Run config checks for AB1" / "Validate configuration for AB3"
- "STAF checks for HSO" / "Run all configuration checks"
- "Check OS parameters" / "Check HANA configuration"

## Mode Requirements

This skill **requires Mode 2 (Config Store) or Mode 3 (Full Proxy)**. Check the `## Deployment Mode` block at the top of the Team Onboarding context.

- **Mode 1 (Azure-Native)** — Respond exactly: "Config validation requires Mode 2 or Mode 3 (the `sap-configs` storage account must be deployed). Your environment is in Mode 1 (Azure-Native). Run `infra/deploy-sre-infra.ps1 -Mode ConfigStore -SreAgentUmiPrincipalId <agent-mi-id>` to enable this skill." Then stop. Do NOT attempt to fetch STAF or list blobs.
- **Mode 2 (Config Store)** — Run the full flow below. The agent MI must have `Storage Blob Data Reader` on the storage account.
- **Mode 3 (Full Proxy)** — Same as Mode 2. Optionally trigger a fresh on-demand collection through the proxy first (see Step 0 below) before reading configs.

## Architecture

```
   ┌─ STAF check definitions ────────────────────────────────┐
   │  Azure/sap-automation-qa @ main                         │
   │  src/roles/configuration_checks/tasks/files/*.yml       │
   │  9 YAML files: hana, sap, virtual_machine, network,     │
   │                ascs, app, high_availability, package,   │
   │                db2                                      │
   └─────────────────────────────┬───────────────────────────┘
                                 │ requests.get  (in-skill)
                                 ▼
   ┌─ ExecutePythonCode (in agent sandbox) ──────────────────┐
   │  1. Fetch all 9 STAF YAML files from GitHub             │
   │  2. Parse YAML, dedupe by check id                      │
   │  3. Filter by applicability (os_type, roles, db_type,   │
   │     storage_type, ha_type, ha_agent)                    │
   │  4. Compare actual vs expected (string/range/list)      │
   │  5. Format compliance report                            │
   └─────────────────────────────▲───────────────────────────┘
                                 │ actual values
   ┌─ RunAzCliReadCommands ──────┴───────────────────────────┐
   │  az storage blob download                               │
   │  --account-name <storage> --container sap-configs       │
   │  --name <SID>/<host>/latest/<path>  --auth-mode login   │
   │  (uses agent MI \u2192 Storage Blob Data Reader)              │
   └─────────────────────────────────────────────────────────┘
```

The agent does ALL the work in-skill. No proxy involved (Mode 2). No server-side STAF cache (the GitHub fetch is < 5 seconds for all 9 files).

### CRITICAL Rules

1. **NEVER invent or fabricate checks.** Every check ID, expected value, and reference must come from the STAF YAML fetched from GitHub. If GitHub is unreachable and no checks load, report the failure and stop.
2. **NEVER deploy anything.** No collector deployment, no VM commands, no infrastructure changes.
3. **NEVER use `az vm run-command`.** Reading from blob is sufficient; if configs are stale, instruct the user to trigger the collector separately (via the `sap-command-runner` skill in Mode 3, or `az vm run-command` manually in Mode 2).
4. **Present results exactly as computed** — do not add, remove, or modify check results.
5. **STAF YAML schema**: each YAML has a top-level `checks:` list. Each check has: `id`, `name`, `description`, `category`, `severity`, `applicability` (filters), `collector_type`, `collector_args`, `validator_type`, `validator_args`, `report` (`check` or `info`), `references`. Validator types are typically `string_match`, `range_check`, `list_match`. Skip checks where `report != "check"` or `validator_type` is empty — those are data-collection only.

## Execution

### Step 0 (Mode 3 only, optional): Trigger Fresh Collection

If the user wants the *very latest* config state and your Team Onboarding declares Mode 3, you may invoke the `sap-command-runner` skill with `command_id=run_collector` before Step 1. Otherwise skip — collected configs are refreshed weekly by cron, which is sufficient for most validations.

### Step 1: Read collected configs from blob (Mode 2/3)

Use `RunAzCliReadCommands` to list and pull config files. The values for `<storage>`, `<container>`, `<SID>`, and `<host>` come from the Team Onboarding `## Deployment Mode` and SAP Landscape sections.

```bash
# 1a. List files (verify configs exist and are fresh)
az storage blob list \
    --account-name <storage> --container-name sap-configs \
    --prefix "<SID>/<host>/latest/" --auth-mode login \
    --query "[].{name:name, modified:properties.lastModified, size:properties.contentLength}" \
    -o json

# 1b. Download to a temp dir (one call per file you need, or use az storage blob download-batch)
az storage blob download-batch \
    --source sap-configs --pattern "<SID>/<host>/latest/*" \
    --destination /tmp/configs/<SID>/<host>/ \
    --account-name <storage> --auth-mode login
```

If `download-batch` is not supported in your environment, fall back to a loop of `az storage blob download --name <blob-name> --file <local-path>` calls for each file returned by 1a.

If the blob list is empty or the latest file is older than 14 days, **stop and report**: "No fresh collected configs found for `<SID>/<host>` in `<storage>/sap-configs`. The collector may not be installed on this VM. Trigger collection (Mode 3: `sap-command-runner run_collector`; Mode 2: `az vm run-command invoke -g <RG> -n <vm> --command-id RunShellScript --scripts 'sudo /opt/sre/run-collector.sh'`) and re-run this validation."

### Step 2: Fetch STAF Definitions Live from GitHub

Use `ExecutePythonCode`. This pulls all 9 STAF YAML files in parallel and runs the entire validation in one block.

```python
import json, os, re, requests, yaml
from pathlib import Path

# ── Values from Team Onboarding landscape inventory ──
SID          = "AB1"          # SAP System ID
HOST         = "AB1vm"        # VM hostname
OS_TYPE      = "SLES_SAP"     # SLES_SAP | REDHAT
ROLES        = ["DB","SCS","PAS"]  # DB,SCS,PAS,APP,ERS,WEB
DB_TYPE      = "HANA"         # HANA | Db2
STORAGE_TYPE = "Premium_LRS"  # Premium_LRS | UltraSSD_LRS | ANF | PremiumV2_LRS | AFS
HA_TYPE      = "false"        # false | scale_up | scale_out
HA_AGENT     = "none"         # none | AFA | ISCSI

CONFIG_DIR = Path(f"/tmp/configs/{SID}/{HOST}/latest")  # populated by Step 1b

# ── 1. Fetch STAF check definitions from GitHub ──
STAF_FILES = ["hana.yml", "sap.yml", "virtual_machine.yml", "network.yml",
              "ascs.yml", "app.yml", "high_availability.yml", "package.yml", "db2.yml"]
STAF_BASE = ("https://raw.githubusercontent.com/Azure/sap-automation-qa"
             "/main/src/roles/configuration_checks/tasks/files")

def parse_yaml(text):
    """Parse STAF YAML, handling enums-at-bottom anchor ordering."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        m = re.search(r'^\s{0,2}enums:', text, re.MULTILINE)
        if m:
            pos = text.rfind('\n', 0, m.start()) + 1
            return yaml.safe_load(text[pos:] + '\n' + text[:pos])
        raise

all_checks, fetch_errors = [], []
for fname in STAF_FILES:
    try:
        r = requests.get(f"{STAF_BASE}/{fname}", timeout=30)
        if r.status_code != 200:
            fetch_errors.append(f"{fname}: HTTP {r.status_code}"); continue
        parsed = parse_yaml(r.text)
        if parsed and "checks" in parsed:
            for chk in parsed["checks"]:
                chk["_source"] = fname
                all_checks.append(chk)
    except Exception as e:
        fetch_errors.append(f"{fname}: {e}")

if not all_checks:
    print(json.dumps({"error": "Failed to fetch STAF checks from GitHub",
                       "details": fetch_errors})); raise SystemExit

# ── 2. Filter checks by applicability ──
# Each check may have an `applicability` block with filters.
# Normalize ASCS role alias to STAF convention
roles = {("SCS" if r == "ASCS" else r).upper() for r in ROLES}

def applies(chk):
    a = chk.get("applicability") or {}
    # OS filter
    if "os" in a and OS_TYPE not in (a["os"] if isinstance(a["os"], list) else [a["os"]]):
        return False, "os"
    # Role filter
    if "roles" in a:
        chk_roles = set(r.upper() for r in (a["roles"] if isinstance(a["roles"], list) else [a["roles"]]))
        if not (chk_roles & roles):
            return False, "role"
    # DB filter
    if "db_type" in a and DB_TYPE not in (a["db_type"] if isinstance(a["db_type"], list) else [a["db_type"]]):
        return False, "db_type"
    # Storage filter
    if "storage_type" in a:
        stg = a["storage_type"] if isinstance(a["storage_type"], list) else [a["storage_type"]]
        if STORAGE_TYPE not in stg:
            return False, "storage"
    # HA filter
    if "ha_type" in a:
        ha = a["ha_type"] if isinstance(a["ha_type"], list) else [a["ha_type"]]
        if HA_TYPE not in ha:
            return False, "ha"
    if "ha_agent" in a:
        agent = a["ha_agent"] if isinstance(a["ha_agent"], list) else [a["ha_agent"]]
        if HA_AGENT not in agent:
            return False, "ha"
    return True, None

applicable, filtered = [], {"os":0, "role":0, "db_type":0, "storage":0, "ha":0}
seen = set()
for chk in all_checks:
    cid = chk.get("id", "")
    if cid in seen: continue
    ok, why = applies(chk)
    if ok:
        seen.add(cid); applicable.append(chk)
    elif why:
        filtered[why] += 1

evaluatable = [c for c in applicable if c.get("report") == "check" and c.get("validator_type")]
data_only   = [c for c in applicable if c not in evaluatable]

# ── 3. Compare actual (from blob configs) vs expected (from STAF) ──
# Load the collected config files into a dict keyed by relative path
configs = {}
if CONFIG_DIR.is_dir():
    for p in CONFIG_DIR.rglob("*"):
        if p.is_file():
            try:
                configs[str(p.relative_to(CONFIG_DIR))] = p.read_text(errors="replace")
            except Exception:
                pass

def find_value(collector_args):
    """Extract the actual value from configs based on the check's collector_args.
    Common collector_types: 'sysctl', 'file_grep', 'ini_value', 'package_version', 'service_status'."""
    # collector_args typically has: file, key, section, regex, command
    f = (collector_args or {}).get("file", "")
    key = (collector_args or {}).get("key", "")
    section = (collector_args or {}).get("section", "")
    # Try matching collected file by suffix
    for rel, content in configs.items():
        if f and f in rel:
            # ini_value: look up [section] key=value
            if section and key:
                in_section = False
                for ln in content.splitlines():
                    s = ln.strip()
                    if s == f"[{section}]": in_section = True; continue
                    if s.startswith("[") and s.endswith("]"): in_section = False
                    if in_section and "=" in s and s.split("=",1)[0].strip() == key:
                        return s.split("=",1)[1].strip()
            # sysctl-style: key = value or key=value
            if key:
                for ln in content.splitlines():
                    s = ln.strip()
                    if not s or s.startswith("#"): continue
                    if "=" in s and s.split("=",1)[0].strip() == key:
                        return s.split("=",1)[1].strip()
    return None  # not found

def compare(check, actual):
    vtype = check.get("validator_type")
    vargs = check.get("validator_args") or {}
    expected = vargs.get("expected")
    if actual is None:
        return "not_evaluated", "actual value not found in collected configs"
    if vtype == "string_match":
        return ("pass" if str(actual).strip() == str(expected).strip() else "fail",
                f"actual={actual}, expected={expected}")
    if vtype == "range_check":
        try:
            a = float(actual); lo = vargs.get("min"); hi = vargs.get("max")
            if (lo is not None and a < float(lo)) or (hi is not None and a > float(hi)):
                return "fail", f"actual={actual}, expected range=[{lo},{hi}]"
            return "pass", f"actual={actual} in [{lo},{hi}]"
        except ValueError:
            return "not_evaluated", f"actual={actual} not numeric"
    if vtype == "list_match":
        exp_list = expected if isinstance(expected, list) else [expected]
        return ("pass" if str(actual).strip() in [str(e).strip() for e in exp_list] else "fail",
                f"actual={actual}, expected one of {exp_list}")
    return "not_evaluated", f"unknown validator_type={vtype}"

results, failures = [], []
for chk in evaluatable:
    actual = find_value(chk.get("collector_args"))
    status, detail = compare(chk, actual)
    item = {"id": chk.get("id"), "name": chk.get("name"),
            "severity": chk.get("severity"), "status": status, "detail": detail,
            "references": chk.get("references")}
    results.append(item)
    if status == "fail":
        failures.append(item)

summary = {
    "pass": sum(1 for r in results if r["status"] == "pass"),
    "fail": len(failures),
    "not_evaluated": sum(1 for r in results if r["status"] == "not_evaluated"),
    "data_collection": len(data_only),
}

report = {
    "sid": SID, "host": HOST,
    "data_sources": {
        "staf": {"source": "github_live", "total": len(all_checks),
                  "fetch_errors": fetch_errors or None},
        "configs": {"source": "blob", "file_count": len(configs),
                     "dir": str(CONFIG_DIR)},
    },
    "staf": {"total": len(all_checks), "filtered_out": filtered,
              "applicable": len(applicable)},
    "summary": summary,
    "failures": failures,
    "results": results,
}
print(json.dumps(report, indent=2))
```

### Step 3: Format and Present Results

Use the printed JSON to produce a compliance report. Format strictly from the data — do not invent any check IDs, expected values, or references.

```
<SID> — STAF Config Compliance Report

  Data Sources:
    STAF checks:  GitHub live (188 total) — pulled from Azure/sap-automation-qa @ main
    Config data:  blob /tmp/configs/<SID>/<host>/latest (58 files)

  Check Coverage:
    Total STAF checks:    188
    Filtered out:          -83 (HA: 32, Db2: 25, storage: 16, OS: 10)
    Applicable:            105
    ├─ Evaluated:           18 (12 pass, 6 fail)
    ├─ Not evaluated:       56 (collector_args mapping not implemented for these check types)
    └─ Data collection:     31 (info only, no expected value)

  ══════════════════════════════════════
  FAILURES (from report.failures):

  ❌ DB-HANA-0024 vm.swappiness — actual=60, expected=10 (HIGH)
     Ref: SAP Note 3024346
  ...

  ══════════════════════════════════════
  SUMMARY: 12/18 PASS, 6 FAIL
```

**Rules for formatting:**
- Show `data_sources` first so the user knows where the data came from.
- List **all** failures from `report.failures` — do not add or remove any.
- Show the filter breakdown so users understand why 188 became 105.
- If `summary.not_evaluated` is high, mention that the `find_value` helper above is a deliberately minimal implementation (sysctl / ini_value style). Some STAF check types (e.g. `service_status`, `package_version`, `cluster_property`) require more specific collectors that this skill does not currently parse — those will appear as `not_evaluated` and that is expected, not a bug.
- If `data_sources.configs.file_count == 0`, do NOT report any FAIL/PASS — instead say configs are missing and stop.

## References
- [SAP Testing Automation Framework (STAF)](https://github.com/Azure/sap-automation-qa)
- [STAF Check Definitions](https://github.com/Azure/sap-automation-qa/tree/main/src/roles/configuration_checks/tasks/files)
- README adoption modes: [`README.md#adoption-modes`](../../README.md#adoption-modes)
