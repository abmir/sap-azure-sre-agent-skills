# Reference (RBAC, MCP tools, config flow, troubleshooting)

## MCP Tools

The MCP command proxy exposes only the tools the agent actually needs. STAF validation is **not** a proxy tool — the `sap-config-validator` skill fetches STAF YAML from GitHub and reads collected configs from blob entirely in-skill (no proxy round-trip).

| Tool | Description |
|------|-------------|
| `list_allowed_commands` | List the read-only VM command allowlist |
| `run_command` | Execute one of the 14 allowed commands on a VM |
| `run_batch` | Execute up to 6 commands in one call |

The MCP server enforces the allowlist and runs commands under its own managed identity via the Azure run-command API. It does **not** serve configs — config reads are done directly by the SRE Agent's own MI against the `sap-configs` blob.

### Config Validation Flow (in-skill, no proxy)

The `sap-config-validator` skill performs the entire flow client-side via `ExecutePythonCode` + `RunAzCliReadCommands`:

```
1. Skill calls RunAzCliReadCommands:
     az storage blob download-batch --auth-mode login \
       --source sap-configs --pattern "<SID>/<host>/latest/*"
     (uses the SRE Agent's own MI — requires Storage Blob Data Reader on the storage account)

2. Skill calls ExecutePythonCode:
     requests.get(<github raw>) for the 9 STAF YAML files in
       Azure/sap-automation-qa @ main/src/roles/configuration_checks/tasks/files
     parse → filter applicability → extract actuals from collected files
     → compare (string / range / list) → emit JSON report

3. Agent presents the report to the user verbatim.
```

This keeps the proxy focused on its one job — brokered VM command execution — and lets the Config Validator skill work with just a Storage Account (no proxy needed).

---

## Identities & RBAC

Config reads are **decoupled from the proxy**: the SRE Agent reads configs from storage with its **own** identity; the proxy (optional) only runs live VM commands and has **no storage role**.

| Identity | Assigned to | Purpose | RBAC |
|----------|------------|---------|------|
| **SRE Agent MI** | SRE Agent platform | Azure API queries + **direct** config blob reads | Reader on SAP RGs, Log Analytics Reader on AMS LAW, **Storage Blob Data Reader on the `sap-configs` storage account** (whenever a config store is deployed — used by *all* config-consuming skills, not just config-validator) |
| **sre-collector-umi** | SAP VMs | Config upload to blob (write) | Storage Blob Data Contributor on the `sap-configs` storage account |
| **sre-mcp-umi** *(optional)* | MCP command proxy Container App | **Live VM commands only** | Custom - SAP SRE Agent Operator on SAP RGs; AcrPull on ACR. **No storage role** — the proxy is never in the config path. |

> **Network path for direct reads:** because the storage account is private (no shared keys), the SRE Agent must be able to reach it. VNet-integrate the agent (delegated subnet) and allow that subnet on the storage firewall, so the agent MI reads configs privately and Entra-only. The proxy is no longer needed to bridge the network.

The custom role grants:
- `Microsoft.Compute/virtualMachines/read`, `/runCommand/action`
- `Microsoft.Compute/disks/read`
- `Microsoft.Network/networkInterfaces/read`, `/loadBalancers/read`, `/proximityPlacementGroups/read`
- `Microsoft.Resources/subscriptions/resourceGroups/read`
- **No** write, delete, restart, deallocate, or power-state actions.

---

## RBAC scoping — RG-only vs subscription

Everything in the center of the architecture (health, cost, advisor, resource state, platform metrics, activity log, resource graph) is reached through the **agent's Managed Identity + Azure RBAC** — *not* through connectors. **Connectors** (AMS Log Analytics, Application Insights) only let the agent *query a telemetry store*; they are **additive**, not a substitute for RBAC.

RBAC **scope = visibility**. If a customer grants read roles only on the SAP resource groups (not the whole subscription), most skills still work — **provided you grant on all relevant RGs**, not just the app RG:

- SAP application RG(s) — `RG_SAP_*`
- ACSS / managed RG(s) — `mrg-*` (VIS, disks, Key Vault often live here)
- AMS workspace RG — `mrg-sapmon-*` (the Log Analytics workspace lives here, **not** in the app RG)
- add **Cost Management Reader** on the same RGs for cost skills

**Works fine at RG scope:**

| Data source | Requires |
|---|---|
| VM state / disks / NIC / PPG / accel-net / zones (ARM) | Reader on the RG |
| Azure Monitor **platform metrics** (CPU/mem/disk/network) | Monitoring Reader on the RG |
| Resource Health (per-resource) | Reader on the RG |
| Activity Log (that RG's events) | Reader on the RG |
| Advisor recommendations (resources in the RG) | Reader on the RG |
| Resource Graph | returns only resources you can read |
| AMS / Log Analytics telemetry | Log Analytics Reader on the **AMS** RG + the workspace connector |

**Needs subscription-scope `Reader` (else partial/blank — the skills disclose this):**

| Data source | Why |
|---|---|
| **Service Health** (service issues, planned-maintenance advisories) | subscription-level feed |
| Subscription-wide **cost / RI coverage** rollups | cost rolls up at subscription/billing scope |
| **Defender for Cloud** secure score / posture | subscription-scoped |
| Subscription-wide **Azure Policy** compliance | assignments evaluated at sub / MG scope |
| **Auto-discovery** of *all* SAP systems | Resource Graph only sees granted RGs |

**Recommendation:** grant **Reader + Monitoring Reader on all relevant RGs** (least privilege — covers every per-system skill). Add **subscription `Reader`** (read-only, low risk) only if the customer wants the sub-level sources above; otherwise the skills degrade gracefully and say so in the report header.

---

## Security

- **Command allowlist** — 14 read-only commands hardcoded in `proxy-mcp/server.py` (`ALLOWED_COMMANDS`) — no arbitrary shell execution
- **API key auth** — the MCP connector sends `x-api-key` on every call
- **Custom RBAC** — read + runCommand only — no VM delete/restart/write
- **No shared storage keys** — Entra ID auth only (MCAP-compliant)
- **VNet-integrated proxy** — Container App on a delegated subnet; storage firewall restricts to the SAP subnets
- **Audit logging** — every command logged with caller, VM, RG, timestamp (visible in Container App logs)
- **Onboarding rule** — agent is instructed to NEVER use `az vm run-command` directly; all VM execution goes through the MCP command proxy

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `run_command` fails on the ARM call | MCP proxy UMI missing RBAC on the SAP RG | Re-run `deploy-mcp-proxy.ps1` (grants the custom role) |
| `run_command` returns "AuthorizationFailed" | Custom role not assigned on the target RG | Confirm the `sre-mcp-umi` role assignment on the **VM's** RG |
| Config skills report "no collected configs" | Collector hasn't run yet, or blob firewall blocks the SAP/agent subnet | Run the collector once manually + allow the subnet on the storage firewall |
| Collector run fails with "AADSTS" | VM missing the collector UMI assignment | Assign `sre-collector-umi` to the VM (`az vm identity assign`) |
| Collector log shows "AuthorizationPermissionMismatch" | Collector UMI missing Storage Blob Data Contributor | Re-run `deploy-sre-infra.ps1` (grants this automatically) |
| Skills can't find systems | `sap-landscape-inventory.json` not in the fork / Knowledge Base | Add it via Code Access or Knowledge Sources |
| Live commands unavailable | `sap-sre-proxy` MCP connector not configured | Register the connector (setup-detailed Step 11) + paste Team Onboarding |
| AMS queries return no rows | Wrong column names (`sapsid_s` vs `SID_s`) | See onboarding template "Data Sources" section |

Container App logs:
```powershell
az containerapp logs show -n sap-sre-proxy -g rg-sre-proxy --tail 100 --follow
```


## Updates & version pinning

Plugin installs are pinned to the exact git commit at install time. Changes you merge do **not** reach an agent until someone clicks **Update** on that plugin (the portal diffs by SHA-256 hash). This is by design — it gives you production stability, staged rollouts (update dev before prod), and version diversity across agents. It is *not* a live, auto-propagating feed.

*Exception:* data files a skill fetches live at runtime — like `knowledge/sap-certified-vms.json` and the STAF YAML — update immediately, because the skill pulls the current file on each run.

## Repository connections (Code Access vs Knowledge base)

In the current portal, **repository connections are under Builder → Code Access**, not Knowledge base ("Repository connections have moved to Code Access"). **Knowledge base** is now only for uploaded files (PDFs) and web pages — you don't need it for anything already in this repo.
