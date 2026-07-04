---
name: sap-config-validator
description: "Validates SAP system configurations against Microsoft's SAP Testing Automation Framework (STAF). Pulls STAF check definitions live from the public Azure/sap-automation-qa GitHub repo, reads collected VM configs from the customer's sap-configs blob container, and runs the comparison entirely in-skill. Requires a Storage Account in Deployed Infrastructure. Read-only."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
---

## When to Use

- "Run config checks for AB1" / "Validate configuration for AB3"
- "STAF checks for HSO" / "Run all configuration checks"
- "Check OS parameters" / "Check HANA configuration"

## Topology handling (all 8 system types)

Derive `HA_TYPE` from `architecture` + `deployment` (see the values block below) and iterate nodes by topology:
- **scale-out** → validate **every** DB node (master + workers + standby) and compare `sysctl` / `global.ini` **across all worker nodes** — they must be uniform for HSR partitioning; flag any drift.
- **standalone / distributed** → `HA_TYPE = "false"`; skip the `high_availability` STAF checks.
- **high-availability / disaster-recovery** → `HA_TYPE = scale_up|scale_out`; run the HA checks. For **disaster-recovery**, also confirm the DR-region nodes have collected configs and validate them too.

## Infrastructure Requirements

This skill **requires a Storage Account** in the `## Deployed Infrastructure` section of Team Onboarding.

- **If no Storage Account is listed** — Respond exactly: "Config validation requires a Storage Account with collected SAP configs (the `sap-configs` container must exist and the agent MI must have `Storage Blob Data Reader` on it). No Storage Account is listed in Deployed Infrastructure. Run `infra/deploy-sre-infra.ps1` to deploy one." Then **stop**. Do NOT attempt to fetch STAF or list blobs.
- **If Storage Account is listed** — Run the flow below. Configs are read from blob using the **SRE Agent's own Managed Identity** (built into `RunAzCliReadCommands` — `--auth-mode login`). No proxy is needed for this skill.

## Architecture

```
   STAF check definitions                Collected configs
   (Azure/sap-automation-qa @ main)      (Azure Blob Storage)
            │                                    │
            │ requests.get                       │ az storage blob (--auth-mode login)
            │ (9 YAML files)                     │ uses SRE Agent UMI
            ▼                                    ▼
            └────────►  ExecutePythonCode  ◄─────┘
                              │
                              │ parse YAML → filter applicability →
                              │ extract actuals from collected files →
                              │ compare against expected_output/valid_list/min/max
                              ▼
                       compliance report
```

### Rules

1. **Never invent or fabricate checks.** Every check ID, expected value, and reference must come from the STAF YAML fetched from GitHub. If GitHub is unreachable and no checks load, report the failure and stop.
2. **Never deploy anything.** No collector deployment, no VM commands, no infrastructure changes.
3. **Never use `az vm run-command`.** If configs are missing or stale, instruct the user to re-run the collector on the affected VM (via `az vm run-command` or their config-mgmt tool) so fresh configs land in the `sap-configs` blob.
4. **Present results exactly as computed** — do not add, remove, or modify any check result.

### STAF YAML schema (verified against `Azure/sap-automation-qa` @ `main`)

Each YAML file has a top-level `checks:` list. Each check has:

- `id`, `name`, `description`, `category`, `severity`
- `applicability` — filter keys: `os_type`, `os_version`, `role` *(singular: `DB`, `SCS`, `ERS`, `APP`, `WEB`, `PAS`)*, `database_type`, `storage_type`, `workload`, `hardware_type`, `high_availability` *(bool, or list of `scale_up`/`scale_out`)*, `high_availability_agent` *(`ISCSI` or `AFA`)*
- `collector_type` — `command` *(shell command run on the VM)* or `azure` *(ARM API lookup — not supported by this skill, returns `not_evaluated`)*
- `collector_args` — for `command`: `command:` *(shell string)* and `user:` *(`root` or `sidadm`)*. For `azure`: `resource_type`, `property`, optional `mount_point`
- `validator_type` — `string`, `range`, or `list`
- `validator_args` — `expected_output` *(string)*, `valid_list` *(list)*, `min`/`max` *(range)*
- `report` — `check` *(compare actual vs expected — the only kind we evaluate)*, `section` *(UI heading)*, or `table` *(data-only)*
- `references` — list of SAP Note / Microsoft docs URLs

## Execution

### Step 1 — Download collected configs from blob

Use `RunAzCliReadCommands`. The values for `<storage>`, `<SID>`, and `<host>` come from the Team Onboarding `## Deployed Infrastructure` and `## SAP Landscape` sections. Authentication is the agent's own Managed Identity (`--auth-mode login`) — no keys, no SAS.

```bash
# 1a. Confirm fresh configs exist
az storage blob list \
    --account-name <storage> --container-name sap-configs \
    --prefix "<SID>/<host>/latest/" --auth-mode login \
    --query "[].{name:name, modified:properties.lastModified}" -o json

# 1b. Download all files under latest/ to a local temp dir
az storage blob download-batch \
    --source sap-configs --pattern "<SID>/<host>/latest/*" \
    --destination /tmp/configs/<SID>/<host>/ \
    --account-name <storage> --auth-mode login
```

If the blob list is empty or the newest file is older than 14 days, **stop and report**:
*"No fresh collected configs found for `<SID>/<host>` in `<storage>/sap-configs`. The collector may not be installed on this VM. Re-run the collector on the VM (`az vm run-command invoke -g <RG> -n <vm> --command-id RunShellScript --scripts 'sudo /opt/sre/collect-sap-configs.sh'`, or your config-management tool) so fresh configs land in the `sap-configs` blob, then re-run this validation."*

### Step 2 — Fetch STAF definitions and run the comparison

Use `ExecutePythonCode`. Set the six landscape variables at the top from the Team Onboarding inventory before running.

```python
import json, re, requests, yaml
from pathlib import Path

# ── Values from Team Onboarding landscape inventory ─────────────────────────
SID          = "AB1"              # SAP System ID
HOST         = "AB1vm"            # VM hostname
OS_TYPE      = "SLES_SAP"         # SLES_SAP | REDHAT | OracleLinux | Windows
ROLES        = ["DB", "SCS", "PAS"]    # DB,SCS,ERS,APP,WEB,PAS  (ASCS auto-aliased to SCS)
DB_TYPE      = "HANA"             # HANA | Db2 | MSSQL | Oracle | ASE
STORAGE_TYPE = "Premium_LRS"      # Premium_LRS | UltraSSD_LRS | PremiumV2_LRS | AFS | ANF | StandardSSD_LRS | Standard_LRS
# HA_TYPE is the STAF applicability value — DERIVE it from the inventory's architecture + deployment:
#   deployment in (standalone, distributed)          -> "false"
#   deployment in (high-availability, disaster-recovery) and architecture == scale-up  -> "scale_up"
#   deployment in (high-availability, disaster-recovery) and architecture == scale-out -> "scale_out"
HA_TYPE      = "false"            # "false" (no HA) | "scale_up" | "scale_out"  (derived — see above)
HA_AGENT     = "none"             # none | AFA | ISCSI   (from ha.fencing: azure-fence-agent->AFA, sbd->ISCSI)

CONFIG_DIR = Path(f"/tmp/configs/{SID}/{HOST}/latest")  # populated by Step 1b

# ── 1. Fetch STAF check definitions from GitHub ─────────────────────────────
STAF_FILES = ["hana.yml", "sap.yml", "virtual_machine.yml", "network.yml",
              "ascs.yml", "app.yml", "high_availability.yml", "package.yml", "db2.yml"]
STAF_BASE  = ("https://raw.githubusercontent.com/Azure/sap-automation-qa"
              "/main/src/roles/configuration_checks/tasks/files")

def parse_yaml(text):
    """STAF YAML defines its anchor enums BELOW the checks that use them.
    PyYAML rejects forward references, so on failure we move `enums:` to the top and retry."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        m = re.search(r'^\s{0,2}enums:', text, re.MULTILINE)
        if not m:
            raise
        pos = text.rfind('\n', 0, m.start()) + 1
        return yaml.safe_load(text[pos:] + '\n' + text[:pos])

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

# ── 2. Filter by applicability (real STAF schema field names) ───────────────
roles = {("SCS" if r.upper() == "ASCS" else r.upper()) for r in ROLES}

def applies(chk):
    a = chk.get("applicability") or {}
    def in_list(value, target):
        lst = value if isinstance(value, list) else [value]
        return target in lst

    if "os_type"      in a and a["os_type"]      and not in_list(a["os_type"], OS_TYPE):           return False, "os_type"
    if "database_type" in a and a["database_type"] and not in_list(a["database_type"], DB_TYPE):    return False, "database_type"
    if "storage_type" in a and a["storage_type"] and not in_list(a["storage_type"], STORAGE_TYPE): return False, "storage_type"

    if "role" in a and a["role"]:
        rolelst = {x.upper() for x in (a["role"] if isinstance(a["role"], list) else [a["role"]])}
        if not (rolelst & roles):
            return False, "role"

    if "high_availability" in a and a["high_availability"] is not None:
        v = a["high_availability"]
        if HA_TYPE == "false":
            if v is True or (isinstance(v, list) and v):
                return False, "high_availability"
        else:
            if v is False:                              return False, "high_availability"
            if isinstance(v, list) and HA_TYPE not in v: return False, "high_availability"

    if "high_availability_agent" in a and a["high_availability_agent"] is not None:
        if HA_AGENT == "none":
            return False, "high_availability_agent"
        if not in_list(a["high_availability_agent"], HA_AGENT):
            return False, "high_availability_agent"

    return True, None

applicable, seen, filtered = [], set(), {}
for chk in all_checks:
    cid = chk.get("id", "")
    if cid in seen:
        continue
    ok, why = applies(chk)
    if ok:
        seen.add(cid); applicable.append(chk)
    elif why:
        filtered[why] = filtered.get(why, 0) + 1

evaluatable = [c for c in applicable if c.get("report") == "check" and c.get("validator_type")]
data_only   = [c for c in applicable if c not in evaluatable]

# ── 3. Load collected config files into memory ──────────────────────────────
configs = {}
if CONFIG_DIR.is_dir():
    for p in CONFIG_DIR.rglob("*"):
        if p.is_file():
            try:
                configs[str(p.relative_to(CONFIG_DIR)).replace("\\", "/")] = p.read_text(errors="replace")
            except Exception:
                pass

if not configs:
    print(json.dumps({"error": f"No collected configs found in {CONFIG_DIR}. Run Step 1 first."}))
    raise SystemExit

# ── 4. Extract actual values for the common command patterns ────────────────
# We only evaluate checks whose `collector_args.command` matches one of the
# patterns below — everything else is marked `not_evaluated` with a reason.
# This keeps the skill honest: we never compare against a value we couldn't
# actually find in the collected data.
def extract_actual(check):
    if check.get("collector_type") == "azure":
        return None, "azure_collector_not_supported_in_skill"

    cmd = (check.get("collector_args") or {}).get("command", "")
    if not cmd:
        return None, "no_command"

    # 1. sysctl: "/sbin/sysctl <param> -n"  → look up in os/sysctl-runtime.txt
    m = re.search(r'sysctl\s+([\w.]+)', cmd)
    if m:
        param = m.group(1)
        data = configs.get("os/sysctl-runtime.txt", "")
        if not data:
            return None, "missing:os/sysctl-runtime.txt"
        for line in data.split("\n"):
            if "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == param:
                    return v.strip(), None
        return None, f"sysctl_param_not_found:{param}"

    # 2. df -T <mount>  → filesystem type column from os/disk-usage.txt
    m = re.search(r'df\s+-T\s+(\S+)', cmd)
    if m:
        mount = m.group(1)
        data = configs.get("os/disk-usage.txt", "")
        if not data:
            return None, "missing:os/disk-usage.txt"
        for line in data.strip().split("\n"):
            parts = line.split()
            # df -hT columns: Filesystem Type Size Used Avail Use% Mounted-on
            if len(parts) >= 7 and parts[-1] == mount:
                return parts[1], None
        return None, f"mount_not_found:{mount}"

    # 3. Transparent HugePages
    if "transparent_hugepage" in cmd:
        data = configs.get("os/thp-status.txt", "")
        if not data:
            return None, "missing:os/thp-status.txt"
        m2 = re.search(r'\[(\w+)\]', data)
        return (m2.group(1) if m2 else data.strip()), None

    # 4. tuned-adm active profile
    if "tuned-adm" in cmd:
        data = configs.get("os/tuned-profile.txt", "")
        return (data.strip(), None) if data else (None, "missing:os/tuned-profile.txt")

    # 5. fstrim systemctl active count
    if "fstrim" in cmd and "systemctl" in cmd:
        data = configs.get("os/fstrim-status.txt", "")
        if not data:
            return None, "missing:os/fstrim-status.txt"
        return str(sum(1 for l in data.split("\n") if "active" in l.lower())), None

    # 6. free -m Swap total
    if "free" in cmd and "Swap" in cmd:
        data = configs.get("os/free-m.txt", "")
        if not data:
            return None, "missing:os/free-m.txt"
        for line in data.split("\n"):
            if line.strip().startswith("Swap:"):
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1], None
        return None, "swap_line_not_found"

    # 7. systemd DefaultTasksMax
    if "DefaultTasksMax" in cmd:
        data = configs.get("os/systemd-defaults.txt", "")
        if not data:
            return None, "missing:os/systemd-defaults.txt"
        for line in data.split("\n"):
            if "DefaultTasksMax" in line and "=" in line:
                return line.split("=", 1)[1].strip(), None
        return None, "DefaultTasksMax_not_found"

    # 8. softdog module loaded
    if "lsmod" in cmd and "softdog" in cmd:
        data = configs.get("os/lsmod-softdog.txt", "")
        if data is None:
            return None, "missing:os/lsmod-softdog.txt"
        return (data.strip() if data.strip() else "not loaded"), None

    # 9. uname -r
    if "uname" in cmd and "-r" in cmd:
        data = configs.get("os/uname-r.txt", "")
        return (data.strip(), None) if data else (None, "missing:os/uname-r.txt")

    return None, "unsupported_command_pattern"

# ── 5. Compare actual vs expected ───────────────────────────────────────────
def compare(check, actual):
    vtype = check.get("validator_type")
    vargs = check.get("validator_args") or {}
    if actual is None:
        return "not_evaluated", "actual value unavailable"
    actual_s = " ".join(str(actual).strip().split())

    if vtype == "string":
        expected = " ".join(str(vargs.get("expected_output", "")).strip().split())
        if actual_s.lower() == expected.lower():
            return "pass", f"actual={actual_s}"
        return "fail", f"actual={actual_s} | expected={expected}"

    if vtype == "range":
        try:
            n = float(actual_s)
        except ValueError:
            return "not_evaluated", f"actual={actual_s} not numeric"
        lo, hi = vargs.get("min"), vargs.get("max")
        if lo is not None and n < float(lo):
            return "fail", f"actual={n} below min={lo}"
        if hi is not None and n > float(hi):
            return "fail", f"actual={n} above max={hi}"
        return "pass", f"actual={n} in [{lo},{hi}]"

    if vtype == "list":
        valid = vargs.get("valid_list", []) or []
        if actual_s.lower() in [str(v).strip().lower() for v in valid]:
            return "pass", f"actual={actual_s}"
        return "fail", f"actual={actual_s} | valid_list={valid}"

    return "not_evaluated", f"unknown validator_type={vtype}"

# ── 6. Run every evaluatable check ──────────────────────────────────────────
results, failures = [], []
for chk in evaluatable:
    actual, skip = extract_actual(chk)
    if actual is None:
        status, detail = "not_evaluated", skip
    else:
        status, detail = compare(chk, actual)
    item = {
        "id":         chk.get("id"),
        "name":       chk.get("name"),
        "severity":   chk.get("severity"),
        "category":   chk.get("category"),
        "status":     status,
        "detail":     detail,
        "references": chk.get("references"),
    }
    results.append(item)
    if status == "fail":
        failures.append(item)

summary = {
    "pass":          sum(1 for r in results if r["status"] == "pass"),
    "fail":          len(failures),
    "not_evaluated": sum(1 for r in results if r["status"] == "not_evaluated"),
    "data_only":     len(data_only),
}

print(json.dumps({
    "sid": SID, "host": HOST,
    "data_sources": {
        "staf":    {"source": "github_live", "total": len(all_checks),
                    "fetch_errors": fetch_errors or None},
        "configs": {"source": "blob", "file_count": len(configs),
                    "dir": str(CONFIG_DIR)},
    },
    "staf": {
        "total":        len(all_checks),
        "filtered_out": filtered,
        "applicable":   len(applicable),
        "evaluatable":  len(evaluatable),
    },
    "summary": summary,
    "failures": failures,
    "results": results,
}, indent=2))
```

### Step 3 — Format and present results

Use the printed JSON to produce a compliance report. **Do not invent any check IDs, expected values, or references** — quote them verbatim from the JSON.

Suggested format:

```
<SID> — STAF Config Compliance Report

  Data Sources:
    STAF checks:  GitHub live (<staf.total> total) — Azure/sap-automation-qa @ main
    Config data:  blob <configs.dir> (<configs.file_count> files)

  Check Coverage:
    Total STAF checks:    <staf.total>
    Filtered out:         <sum of filtered_out>  (os_type: N, role: N, ...)
    Applicable:           <staf.applicable>
    ├─ Evaluated:         <pass + fail>  (<pass> pass, <fail> fail)
    ├─ Not evaluated:     <not_evaluated>  (commands this skill does not extract — see below)
    └─ Data-only:         <data_only>  (info/section/table — no expected value)

  ══════════════════════════════════════
  FAILURES (list every entry from .failures verbatim):

  ❌ <id> <name> — <detail>   [<severity>]
     Refs: <references>
  ...

  ══════════════════════════════════════
  SUMMARY: <pass>/<evaluated> PASS, <fail> FAIL
```

**Presentation rules:**

- Show `data_sources` first so the user knows where the data came from.
- List **every** failure from `report.failures` — never truncate or summarize.
- If `summary.not_evaluated` is high, explain that this skill only extracts values for nine common command patterns (sysctl, `df -T`, THP, tuned-adm, fstrim, swap, DefaultTasksMax, softdog, uname). Cluster / ANF / Azure-collector / arbitrary-command checks return `not_evaluated` with a reason — that is **expected**, not a bug.
- If `configs.file_count == 0`, do **not** report any PASS/FAIL — say configs are missing and stop.
- If `staf.fetch_errors` is non-empty, surface it so the user knows partial STAF data was used.

## References

- [SAP Testing Automation Framework (STAF)](https://github.com/Azure/sap-automation-qa)
- [STAF Check Definitions](https://github.com/Azure/sap-automation-qa/tree/main/src/roles/configuration_checks/tasks/files)
- [README adoption modes](https://github.com/mcaps-microsoft/sap-azure-sre-agent/blob/main/README.md#adoption-modes)
