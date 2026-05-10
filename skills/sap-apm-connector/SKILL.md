---
name: sap-apm-connector
description: "Connects to customer's APM tool (Dynatrace, SAP Focus Run via Grafana, AppDynamics, or other) to pull HANA-native deep telemetry. Enriches SAP Incident RCA and SAP Anomaly Forecaster with application-level metrics not available in AMS. Only active when configured. This skill is invoked by other skills — not directly by users."
tools:
    - ExecutePythonCode
    - GetCurrentUtcTime
---

## Environment Configuration

All environment-specific values (subscription ID, AMS workspace ID, proxy URLs, API keys, SAP landscape) are provided via the Team Onboarding instructions. The agent reads these from the onboarding context at runtime. Do not hardcode environment values in this skill.

**Authentication**: Use the agent's built-in tools (RunAzCliReadCommands, GetArmResourceAsJson, QueryLogAnalyticsByWorkspaceId, GetMetricTimeSeriesElementsForAzureResource) for Azure API calls. These authenticate automatically via the agent's Managed Identity.

## Agent-Internal Skill — Conditional Integration

This skill is invoked by SAP Incident RCA and SAP Anomaly Forecaster to enrich their analysis with deep HANA telemetry from the customer's APM tool. It is NOT invoked directly by users.

When not configured, calling skills gracefully fall back to AMS HANA telemetry only (sufficient for RCA and forecasting).

## Supported Backends

| Backend | Config Variable | API Pattern |
|---------|----------------|-------------|
| **SAP Focus Run (via Grafana)** | `GRAFANA_URL` + `GRAFANA_API_KEY` | Grafana REST API (`/api/dashboards`, `/api/ds/query`) |
| **Dynatrace** | `DYNATRACE_URL` + `DYNATRACE_API_TOKEN` | Dynatrace API v2 (`/api/v2/metrics`, `/api/v2/problems`) |

## Configuration

```python
# APM config: Use apm_type, apm_url from Team Onboarding (null = disabled)

# When set, the following skills invoke this bridge:
# - SAP Incident RCA: enriches Layer 4 with HANA-native deep telemetry
# - SAP Anomaly Forecaster: pulls granular memory pool data for better projection
```

## Capabilities

### 1. Query Dashboard Data
```python
def query_grafana_dashboard(dashboard_uid, timespan_hours=6):
    """Pull data from a Grafana dashboard."""
    if not GRAFANA_URL:
        return None  # Graceful skip — use AMS data only
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    from_ms = now_ms - (timespan_hours * 3600 * 1000)
    resp = requests.get(f"{GRAFANA_URL}/api/dashboards/uid/{dashboard_uid}",
        headers={"Authorization": f"Bearer {GRAFANA_API_KEY}"}, timeout=30)
    return resp.json() if resp.status_code == 200 else None
```

### 2. Query Datasource Directly
```python
def query_grafana_datasource(datasource_id, query, timespan_hours=6):
    """Execute a query against a Grafana datasource (e.g., Focus Run)."""
    if not GRAFANA_URL:
        return None
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    from_ms = now_ms - (timespan_hours * 3600 * 1000)
    resp = requests.post(f"{GRAFANA_URL}/api/ds/query",
        headers={"Authorization": f"Bearer {GRAFANA_API_KEY}", "Content-Type": "application/json"},
        json={"queries": [{"refId": "A", "datasourceId": datasource_id, "rawSql": query}],
              "from": str(from_ms), "to": str(now_ms)}, timeout=60)
    return resp.json() if resp.status_code == 200 else None
```

### 3. List Available Dashboards
```python
def list_dashboards():
    """List available Grafana dashboards for SAP."""
    if not GRAFANA_URL:
        return None
    resp = requests.get(f"{GRAFANA_URL}/api/search",
        headers={"Authorization": f"Bearer {GRAFANA_API_KEY}"},
        params={"query": "SAP", "type": "dash-db"}, timeout=30)
    return resp.json() if resp.status_code == 200 else []
```

## Expected Focus Run Dashboards

When connected to SAP Focus Run via Grafana, these dashboards provide HANA-native depth:
- **HANA Overview**: memory pools, row/column store, delta merge, SQL cache
- **HANA Performance**: SQL execution times, lock waits, thread utilization
- **HANA Replication**: HSR detailed lag, log shipping rate, replay rate
- **HANA Backup**: backup history, duration, catalog size
- **SAP Application**: dialog step times, RFC performance, batch job status

## What This Adds Over AMS

| Data Point | AMS (always available) | Focus Run / Dynatrace (when available) |
|-----------|----------------------|---------------------------|
| Memory total/used | Yes | Yes + per-pool breakdown |
| SQL probe RT | Yes | Yes + top 10 SQL statements |
| HSR lag | Yes | Yes + log shipping rate |
| Lock waits | No | Yes |
| Buffer cache hit ratio | No | Yes |
| End-to-end transaction trace | No | Yes (Dynatrace only) |
| BW/APO/EWM-specific KPIs | No | Yes |

## Cost

API calls only — no LLM processing of Grafana data. ~3 API calls per enrichment = negligible cost.
