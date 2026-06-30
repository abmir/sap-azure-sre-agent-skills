# sap-sre-config

STAF configuration validation for SAP on Azure. The `sap-config-validator` skill fetches STAF
check definitions live from [`Azure/sap-automation-qa`](https://github.com/Azure/sap-automation-qa)
at runtime and compares them against SAP configuration files collected from your VMs into blob
storage. The entire comparison runs in-skill (Python via `ExecutePythonCode`) — **no proxy
required**.

This is **Tier 2 (+ Config Store)** of the SAP Azure SRE Agent.

## Requires

- A customer **Storage Account** with a `sap-configs` blob container.
- The SRE Agent managed identity granted **Storage Blob Data Reader** on that storage account.
- A collector deployed on the SAP VMs that uploads configs to `sap-configs/{SID}/{host}/latest/`.

See the repository [README](../../README.md) → *Phase 2 — Infrastructure (+ Config Store)* for the
deploy steps. Install [`sap-sre-core`](../sap-sre-core) first.

## Skills

| Skill | Purpose |
|-------|---------|
| `sap-config-validator` | STAF compliance — STAF YAML from GitHub vs. blob-stored VM configs |

## Install

**Builder → Plugins → Install from URL** (or register the marketplace and pick this plugin):

```
https://github.com/<your-org>/sap-azure-sre-agent  →  plugin: sap-sre-config
```

Each install is pinned to the exact commit. See the repository [README](../../README.md) for the
update model.
