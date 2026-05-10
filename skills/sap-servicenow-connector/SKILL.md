---
name: sap-servicenow-connector
description: "Conditional ServiceNow REST API integration. Queries change records, incident history, and KB articles with targeted filters. Creates/updates incidents with structured RCA data. Only active when SNOW_URL is configured. This skill is invoked by other skills — not directly by users."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

**Data Reuse (AAU Optimization)**: Before calling any API or proxy, check if the data was already retrieved earlier in this conversation. Reuse landscape registry, VM power states, config files, and AMS query results from context. Do not re-fetch data that is already available.

**Proxy Fallback**: If the config proxy or command proxy returns an error (timeout, 5xx, unreachable), inform the user and continue with Azure-native data sources only (AMS, ARM API, Azure Monitor). Do not block the entire skill on a proxy failure.

## Agent-Internal Skill — Conditional Integration

This skill is invoked by other skills (SAP Incident RCA, SAP Anomaly Forecaster, SAP HA & DR Guardian) to read from and write to ServiceNow. It is NOT invoked directly by users.

When not configured (`SNOW_URL = None`), calling skills gracefully fall back to:
- Azure Activity Log for change correlation
- Teams/Outlook for incident notifications

## Configuration

```python
# SNOW_URL: Use servicenow_url from Team Onboarding (null = disabled)
# SNOW_API_KEY: Use servicenow_api_key from Team Onboarding (null = disabled)

# When set, the following skills use this connector:
# - SAP Incident RCA: creates incidents with RCA payload, queries recent changes
# - SAP Anomaly Forecaster: creates change requests for recommended actions
# - SAP HA & DR Guardian: creates P1 incidents for critical cluster events
```

## Capabilities

### 1. Create Incident
```python
def create_incident(rca_payload):
    if not SNOW_URL:
        return None  # Graceful skip
    resp = requests.post(f"{SNOW_URL}/api/now/table/incident",
        headers={"Authorization": f"Bearer {SNOW_API_KEY}", "Content-Type": "application/json"},
        json={
            "short_description": rca_payload["summary"],
            "description": rca_payload["full_rca"],
            "urgency": rca_payload["urgency"],
            "impact": rca_payload["impact"],
            "cmdb_ci": rca_payload["affected_ci"],
            "category": "SAP",
            "assignment_group": rca_payload.get("assignment_group", "SAP Basis")
        }, timeout=30)
    return resp.json() if resp.status_code == 201 else None
```

### 2. Query Recent Changes (Targeted — NOT bulk scan)
```python
def query_recent_changes(ci_name, hours_back=24, limit=20):
    """Targeted query for recent changes on a specific CI. Max 20 results."""
    if not SNOW_URL:
        return None  # Graceful skip — fall back to Azure Activity Log
    query = (f"cmdb_ci.name={ci_name}"
             f"^start_date>javascript:gs.hoursAgoStart({hours_back})"
             f"^state=implement^ORstate=closed")
    resp = requests.get(f"{SNOW_URL}/api/now/table/change_request",
        headers={"Authorization": f"Bearer {SNOW_API_KEY}"},
        params={"sysparm_query": query,
                "sysparm_fields": "number,short_description,start_date,end_date,state,close_notes",
                "sysparm_limit": limit}, timeout=30)
    return resp.json().get("result", []) if resp.status_code == 200 else []
```

### 3. Query KB Articles
```python
def query_kb(search_text, limit=5):
    """Search knowledge base for known issues. Max 5 results."""
    if not SNOW_URL:
        return None
    resp = requests.get(f"{SNOW_URL}/api/now/table/kb_knowledge",
        headers={"Authorization": f"Bearer {SNOW_API_KEY}"},
        params={"sysparm_query": f"short_descriptionLIKE{search_text}",
                "sysparm_fields": "number,short_description,text",
                "sysparm_limit": limit}, timeout=30)
    return resp.json().get("result", []) if resp.status_code == 200 else []
```

## Cost Guardrail

Every ServiceNow query is **targeted** — filtered by CI, time window, and limited to max 20 results.
This prevents the "scan 100K tickets" anti-pattern the customer flagged.

Estimated token cost per ServiceNow interaction: ~500 tokens (API response) + ~200 tokens (processing) = **$0.01 per query**.
