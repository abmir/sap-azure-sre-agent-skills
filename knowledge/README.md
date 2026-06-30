# Knowledge sources

The SRE Agent **Plugin Marketplace** delivers *skills and MCP configs* from this repo, but it does
**not** deliver *knowledge files*. Knowledge is a separate connection. Wire it up so this repo is
the single source of truth for both.

## How the agent gets repo knowledge — Code Access (not Knowledge base)

In the current SRE Agent portal, **repository connections live under Builder → Code Access**, not
Knowledge base. The banner on the Knowledge Sources page confirms this: *"Repository connections
have moved to Code Access."*

**Builder → Code Access → connect your fork** of `mcaps-microsoft/sap-azure-sre-agent`. One
connection lets the agent read everything in the repo — this `knowledge/` folder, `config/`,
`docs/`, and the proxy/IaC code — and cite it by file and commit during investigations.

> **Builder → Knowledge base** is now only for **uploaded files** (PDFs, images) and **web pages**.
> You don't need it for anything in this repo. Use it only if a customer wants to drop in extra
> material that isn't in the repo.

## Repo-hosted knowledge files

| File | Purpose | Update model |
|------|---------|--------------|
| [`sap-note-1928533.md`](sap-note-1928533.md) | **Authoritative, verbatim** SAP Note 1928533 (you paste the exact note / drop the PDF). The source of truth for SAP VM support. | Replace verbatim via PR when SAP revises the note. |
| [`sap-certified-vms.json`](sap-certified-vms.json) | Machine-readable **index** derived from the note above (+ HANA Hardware Directory). The `sap-deployment-readiness` skill fetches this **live** at runtime. | Reconcile to the note, edit → PR → merge. Takes effect on the next skill run — **no plugin re-install**. |
| [`../config/sap-landscape-inventory.template.json`](../config/sap-landscape-inventory.template.json) | **Sample** — fill in your SAP systems. | Customer populates in their fork (or uploads the filled file, or the collector publishes the live inventory to blob). |

> **Why these aren't web pages:** SAP Note 1928533 is behind SAP login (the agent can't crawl it),
> and the HANA Hardware Directory is a dynamic page that indexes poorly. Capturing the data as a
> repo file makes it reliable, version-controlled, and replaceable via a simple PR — which is the
> whole point of the single-source-of-truth model.

## Customer samples (populate before use)

These ship as **samples/templates**. The customer fills them in for their own environment:

| Sample | What the customer does |
|--------|------------------------|
| `../config/sap-landscape-inventory.template.json` | Fill in their SAP systems (blank template) |
| `../config/sap-landscape-inventory.json` | Example filled inventory (the AB1 lab) — replace with your own or delete |
| `../onboarding/team-onboarding.template.md` | Fill in environment + secrets, then **paste** into Settings → Team Onboarding (not read from the repo, because it contains keys) |
