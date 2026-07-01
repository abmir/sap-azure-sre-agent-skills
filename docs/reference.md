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
| `/api/diag` returns 500 on ARM call | Proxy UMI missing RBAC on subscription/SAP RG | Re-run Step 11 |
| `/api/command` returns "AuthorizationFailed" | Custom role not assigned on the target RG | Step 11 — confirm role assignment on the **VM's** RG |
| `/api/configs/...` returns empty list | Collector hasn't run yet, or blob firewall blocks SAP subnet | Step 13 + Step 17 (run collector once manually) |
| `deploy_collector` returns "AADSTS" error | VM missing collector UMI assignment | Step 12 |
| Collector log shows "AuthorizationPermissionMismatch" | Collector UMI missing Storage Blob Data Contributor | Re-run deploy script (Step 10 grants this automatically) |
| Skills can't find systems | `sap-landscape-inventory.json` not uploaded to Knowledge Sources | Step 9 |
| Agent asks for proxy URL repeatedly | Team Onboarding not pasted | Step 15 |
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
