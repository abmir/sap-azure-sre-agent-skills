# Operations

## Operations

### Updating the MCP Command Proxy (Code Changes)

After editing `proxy-mcp/server.py`:

```powershell
# Rebuild the image and restart the Container App
az acr build --registry <acr-name> -t sre-mcp:latest .\proxy-mcp
az containerapp update -n sap-sre-mcp -g rg-sre-proxy-mcp `
    --image <acr-name>.azurecr.io/sre-mcp:latest
```

> **Note**: re-run `infra/deploy-mcp-proxy.ps1` to rebuild and redeploy in one step.

### Updating Skills

Skills live under `plugins/<plugin>/skills/<name>/SKILL.md`. To change a skill:

1. Edit the `SKILL.md`, open a **pull request**, and merge to your fork's default branch.
2. In sre.azure.com → **Builder → Plugins**, open the installed plugin and click **Update**. The portal diffs the new commit against the installed one (SHA-256) and shows what changed before you apply.

Because installs are **commit-pinned**, merged changes never reach an agent automatically — each agent updates on its own schedule. This is what lets you stage a change on a dev agent before promoting it to production. (Knowledge-base and Code Access sources re-index on their own; only Plugin Marketplace installs are pinned.)

> **Tip — drive it from your IDE/terminal.** With the [SRE Agent MCP server](https://learn.microsoft.com/azure/sre-agent/mcp-server) (shipped in the Azure MCP Server, `sreagent_*` tools), you can list/update skills and configure connectors from VS Code, Copilot CLI, Cursor, or Claude — no portal tab required. Needs `Reader` + `SRE Agent Administrator` on the agent resource.

### Adding a New SAP System

1. Add the system to `config/sap-landscape-inventory.json`; re-upload to Knowledge Sources
2. Append to the landscape table in `onboarding/team-onboarding.template.md`; re-paste to Team Onboarding
3. Grant proxy UMI the custom role on the new SAP RG (Step 11)
4. Assign collector UMI to the new VMs (Step 12)
5. Add the new subnet to storage firewall (Step 13)
6. Deploy collector to each new VM (Step 14)

---

