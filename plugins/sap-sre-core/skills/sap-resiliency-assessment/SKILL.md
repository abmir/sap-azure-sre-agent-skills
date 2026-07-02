---
name: sap-resiliency-assessment
description: "Resiliency assessment for SAP workloads on Azure. Uses Azure Advisor HighAvailability recommendations (including ACSS/SAP checks for Pacemaker, STONITH, corosync, LB config) as primary data source, supplemented by ARG queries for resource locks, accelerated networking, and diagnostic settings. No proxy required."
tools:
    - ExecutePythonCode
    - RunAzCliReadCommands
    - GetArmResourceAsJson
    - CheckIfResourceExists
    - PlotBarChart
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse Advisor recommendations, landscape registry, VM properties, and ARG results from context.

**Config reads & proxy fallback**: Stored SAP/OS configs are read **directly from the `sap-configs` blob container using the agent's own Managed Identity** (`--auth-mode login`) — there is **no config proxy**. The MCP command proxy is optional and runs only **live VM commands**; if it is not deployed or errors (timeout, 5xx, unreachable), continue with Advisor + Azure-native sources. Never block the skill on the proxy.

## When to Use

- "Can we survive a zone failure?"
- "Resiliency assessment for AB1"
- "Are our SAP VMs in availability zones?"
- "Single points of failure?"
- "Zone coverage analysis"
- "Pacemaker configuration compliance check"
- "DR readiness check"

**Routing**: For live Pacemaker/HSR state ("Is HSR in sync?", "Cluster status", "Takeover readiness?") → route to SAP HA Cluster Health. This skill checks *compliance* (Advisor recommendations, configuration best practices), not live cluster state.

## Topology-aware scoring

Read each system's `architecture` + `deployment` from the inventory and set expectations by topology — don't penalize a system for lacking redundancy it was never designed to have; instead state the resilience ceiling of its tier:

- **standalone / distributed** — single points of failure are expected. Report SPOFs and recommend the upgrade path to HA (Pacemaker + HSR + ASCS/ERS), but score against the standalone/distributed baseline, not the HA rubric.
- **high-availability** — apply the full in-region rubric: zone spread of DB primary/secondary + ASCS/ERS, Standard ILB with HA ports, SBD/fence-agent, HSR sync/syncmem, matching zone-redundant storage.
- **disaster-recovery** — everything HA, **plus** cross-region checks: `dr.region` resources provisioned and sized, HSR `async` replica present and replicating, ASR/backup for the ASCS/app tier, and an RPO/RTO statement.
- **scale-out** — additionally verify every DB worker node is zone/PPG-aligned and a **standby node** exists for HA/DR (`scale_out.standby_nodes >= 1`); flag a scale-out HA/DR system with no standby as a gap.

## How It Works

The skill combines three data sources:

1. **Azure Advisor HighAvailability recommendations** (primary) — Advisor automatically evaluates all resources in the SAP resource groups. For SAP VIS (Virtual Instance for SAP) resources registered via ACSS, Advisor receives ~30 SAP-specific checks from the ACSS agent extension running on the VMs. These cover Pacemaker config, STONITH, corosync, load balancer HA ports, fencing agents, zone placement, and more. For non-SAP resources (storage, NICs, LBs), Advisor applies standard reliability checks.

2. **ARG gap-fill queries** (3 supplemental checks) — Resource locks, accelerated networking, and diagnostic settings are not covered by Advisor.

3. **Landscape registry** (optional context) — If the landscape inventory is available (Knowledge Base or the `sap-configs` blob), cross-reference Advisor findings with SAP system roles (DB, ASCS, PAS, ERS) for richer reporting.

## Execution Steps

### Step 1: Get Advisor Reliability Recommendations

Query for all SAP resource groups. Multiple RGs can be checked in one call:

```
RunAzCliReadCommands: az advisor recommendation list --category HighAvailability --query "[?contains(resourceGroup,'SAP') || contains(resourceGroup,'sap')].{resource:impactedValue, resourceGroup:resourceGroup, impact:impact, problem:shortDescription.problem, solution:shortDescription.solution, resourceType:impactedField}" -o json
```

For a specific SAP system (e.g., AB1):
```
RunAzCliReadCommands: az advisor recommendation list --category HighAvailability --query "[?resourceGroup=='RG_SAP_AB1' || resourceGroup=='mrg-AB1-48f3f2'].{resource:impactedValue, impact:impact, problem:shortDescription.problem, solution:shortDescription.solution}" -o json
```

### Step 2: Get Resource Inventory

```
RunAzCliReadCommands: az graph query -q "Resources | where resourceGroup =~ '<RG_NAME>' | project name, type, location, zones, sku" --query "data" -o json
```

### Step 3: Run Supplemental Checks

These three checks are NOT covered by Advisor and must be queried separately.

**SUP-01: Resource Locks**
```
RunAzCliReadCommands: az lock list --resource-group <RG_NAME> --query "[].{name:name, level:level}" -o json
```
- PASS: CanNotDelete lock on SAP resource group
- FAIL: No locks — accidental deletion risk for production SAP VMs

**SUP-02: Accelerated Networking**
```
RunAzCliReadCommands: az graph query -q "Resources | where resourceGroup =~ '<RG_NAME>' and type == 'microsoft.network/networkinterfaces' | extend accelNet = properties.enableAcceleratedNetworking | project name, accelNet" --query "data" -o json
```
- PASS: All NICs have acceleratedNetworking enabled (required for SAP production per SAP Note 2015553)
- FAIL: Any NIC without accelerated networking

**SUP-03: Diagnostic Settings**
```
RunAzCliReadCommands: az monitor diagnostic-settings list --resource <VM_RESOURCE_ID> --query "[].{name:name, workspace:workspaceId}" -o json
```
- PASS: VMs have diagnostic settings forwarding to Log Analytics
- FAIL: No diagnostic settings on production VMs

### Step 4: Output

Group findings by **impact level** (High → Medium → Low), then by SAP system.

```
<SAP_SID> (<RG_NAME>) — Resiliency Assessment

══════════════════════════════════════════
ADVISOR FINDINGS: <N> recommendations
══════════════════════════════════════════

🔴 HIGH IMPACT (<count>)
  • <vm/resource> — <problem>
    Fix: <solution>
  • <vm/resource> — <problem>
    Fix: <solution>

🟡 MEDIUM IMPACT (<count>)
  • <vm/resource> — <problem>
    Fix: <solution>

══════════════════════════════════════════
SUPPLEMENTAL CHECKS
══════════════════════════════════════════

  ❌ SUP-01: No CanNotDelete locks on RG_SAP_AB1
  ✅ SUP-02: All 6 NICs have accelerated networking
  ❌ SUP-03: 2 VMs missing diagnostic settings

══════════════════════════════════════════
COMPLIANT RESOURCES (no Advisor findings)
══════════════════════════════════════════

  ✅ ab1vm (<VM>) — no reliability recommendations
  ✅ ab1-lb (<LB>) — no reliability recommendations

══════════════════════════════════════════
SUMMARY
══════════════════════════════════════════

  Advisor findings:  <N> total (<H> high, <M> medium, <L> low)
  Supplemental:      <P>/3 pass
  Resources in scope: <total>
  Compliant:          <compliant> (<pct>%)
```

### Step 5: Multi-System Summary

When assessing multiple SAP systems, produce a heatmap:

```
PlotHeatmap with data:
         | Advisor High | Advisor Med | Locks | AccelNet | DiagSettings |
  AB1    |     0        |     2       |  ❌   |    ✅    |     ❌       |
  AB2    |     1        |     3       |  ❌   |    ✅    |     ✅       |
  AB3    |     0        |     1       |  ❌   |    ✅    |     ✅       |
  AB5    |     3        |     4       |  ❌   |    ✅    |     ❌       |
```

## What Advisor Covers for SAP (no custom logic needed)

Advisor + ACSS automatically evaluates these for SAP VIS resources:

| Category | Checks |
|---|---|
| **Pacemaker HA** | STONITH enabled, stonith-timeout (144 or 900), concurrent-fencing, fence_azure_arm instance count, softdog module loaded |
| **Corosync** | Token set to 30000, consensus to 36000 (SUSE), expected_votes = 2, two_node = 1 |
| **Load Balancer** | Idle timeout = 30 min, floating IP enabled, HA ports enabled, TCP timestamps disabled |
| **Zone Placement** | HA across zones for DB, ASCS, App Server; zone-redundant LB |
| **Disk/Storage** | Premium or Ultra disk for production, disk-VM zone alignment |
| **Backup/DR** | VM backup enabled, HSR configured, ASR replication |
| **App Server** | HA configuration, 2+ instances for production |

For non-SAP resources in SAP RGs (storage accounts, NICs, NSGs), Advisor applies standard reliability checks (zone redundancy, soft delete, TLS, etc.).

## References
- [Azure Advisor Reliability Recommendations — Workloads (SAP)](https://learn.microsoft.com/en-us/azure/advisor/advisor-reference-reliability-recommendations#workloads)
- [Resiliency in Azure (Business Continuity Center)](https://learn.microsoft.com/en-us/azure/resiliency/resiliency-overview)
- [SAP on Azure HA Architecture](https://learn.microsoft.com/en-us/azure/sap/workloads/sap-high-availability-architecture-scenarios)
- [Azure Well-Architected Framework — Reliability](https://learn.microsoft.com/en-us/azure/well-architected/reliability/)
