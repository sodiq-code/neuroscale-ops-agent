"""
Splunk Client — wraps both the Splunk Python SDK (for management ops)
and the MCP-style REST interface (for real-time SPL queries).

This is the intelligence backbone of the NeuroScale Ops Agent.
All agent workflows query Splunk FIRST before taking any action.
"""
import os
import json
import time
import requests
import splunklib.client as splunk_client
import splunklib.results as splunk_results
from typing import Any
from rich.console import Console

console = Console()

# ── Splunk connection config ──────────────────────────────────────────────────
SPLUNK_HOST    = os.getenv("SPLUNK_HOST", "localhost")
SPLUNK_PORT    = int(os.getenv("SPLUNK_PORT", "8089"))
SPLUNK_HEC_PORT= int(os.getenv("SPLUNK_HEC_PORT", "8088"))
SPLUNK_USER    = os.getenv("SPLUNK_USERNAME", "admin")
SPLUNK_PASS    = os.getenv("SPLUNK_PASSWORD", "")
SPLUNK_TOKEN   = os.getenv("SPLUNK_TOKEN", "")
SPLUNK_HEC_TOK = os.getenv("SPLUNK_HEC_TOKEN", "")
SPLUNK_INDEX   = os.getenv("SPLUNK_INDEX", "neuroscale")
VERIFY_SSL     = os.getenv("SPLUNK_VERIFY_SSL", "false").lower() == "true"
DEMO_MODE      = os.getenv("DEMO_MODE", "false").lower() == "true"


def _get_service() -> splunk_client.Service:
    """Return an authenticated Splunk service object."""
    if SPLUNK_TOKEN:
        return splunk_client.connect(
            host=SPLUNK_HOST,
            port=SPLUNK_PORT,
            splunkToken=SPLUNK_TOKEN,
            autologin=True,
        )
    return splunk_client.connect(
        host=SPLUNK_HOST,
        port=SPLUNK_PORT,
        username=SPLUNK_USER,
        password=SPLUNK_PASS,
        autologin=True,
    )


def run_spl_query(spl: str, earliest: str = "-1h", latest: str = "now",
                  max_results: int = 15) -> list[dict]:
    """
    Run a Splunk SPL query and return results as a list of dicts.

    This is called by the agent for every diagnosis step — the primary
    intelligence retrieval mechanism.

    Args:
        spl:         SPL query string (e.g. 'index=neuroscale sourcetype=kserve:events')
        earliest:    Splunk time modifier (e.g. '-1h', '-24h', '-7d')
        latest:      Splunk time modifier (default 'now')
        max_results: Cap on returned rows

    Returns:
        List of result dicts (column name → value)
    """
    if DEMO_MODE:
        return _demo_results(spl)

    try:
        service = _get_service()
        kwargs = {
            "exec_mode": "blocking",
            "earliest_time": earliest,
            "latest_time": latest,
            "count": max_results,
        }
        # Splunk SDK requires the query to start with 'search' keyword
        if not spl.strip().startswith("search "):
            spl = "search " + spl.strip()
        job = service.jobs.create(spl, **kwargs)

        # Stream results
        results = []
        for result in splunk_results.JSONResultsReader(job.results(output_mode="json")):
            if isinstance(result, dict):
                results.append(result)
        return results

    except Exception as exc:
        console.print(f"[red]Splunk query error:[/red] {exc}")
        return [{"error": str(exc), "query": spl}]


def send_to_hec(event: dict, sourcetype: str, index: str = None) -> bool:
    """
    Send a structured event to Splunk via HTTP Event Collector (HEC).

    Used by the K8s→Splunk forwarder and the agent's self-reporting loop.
    """
    if DEMO_MODE:
        return True

    payload = {
        "event": event,
        "sourcetype": sourcetype,
        "index": index or SPLUNK_INDEX,
        "time": time.time(),
    }
    try:
        resp = requests.post(
            f"https://{SPLUNK_HOST}:{SPLUNK_HEC_PORT}/services/collector/event",
            headers={"Authorization": f"Splunk {SPLUNK_HEC_TOK}"},
            json=payload,
            verify=VERIFY_SSL,
            timeout=5,
        )
        return resp.status_code == 200
    except Exception as exc:
        console.print(f"[yellow]HEC send warning:[/yellow] {exc}")
        return False


def get_model_health(model_name: str = None, window: str = "-1h") -> list[dict]:
    """Query KServe inference service health events from Splunk."""
    name_filter = f'name="{model_name}"' if model_name else ""
    spl = f"""
        index={SPLUNK_INDEX} sourcetype="kserve:events" {name_filter}
        | stats count as event_count, values(reason) as reasons,
                values(message) as messages, latest(_time) as last_seen
          by name, namespace
        | sort -event_count
    """.strip()
    return run_spl_query(spl, earliest=window)


def get_policy_violations(resource: str = None, window: str = "-24h") -> list[dict]:
    """Query Kyverno admission denials from Splunk."""
    res_filter = f'resource="{resource}"' if resource else ""
    spl = f"""
        index={SPLUNK_INDEX} sourcetype="kyverno:violations" {res_filter}
        | table _time, resource, namespace, policy, action, message
        | sort -_time
    """.strip()
    return run_spl_query(spl, earliest=window)


def get_cost_attribution(window: str = "-6h") -> list[dict]:
    """Query OpenCost spend data from Splunk, ranked by namespace."""
    spl = f"""
        index={SPLUNK_INDEX} sourcetype="opencost:metrics"
        | stats sum(hourly_cost) as total_cost by namespace
        | eval total_cost=round(total_cost, 4)
        | sort -total_cost
    """.strip()
    return run_spl_query(spl, earliest=window)


def get_argocd_sync_events(app_name: str = None, window: str = "-2h") -> list[dict]:
    """Query ArgoCD application sync events from Splunk."""
    app_filter = f'app_name="{app_name}"' if app_name else ""
    spl = f"""
        index={SPLUNK_INDEX} sourcetype="argocd:events" {app_filter}
        | table _time, app_name, sync_status, health_status, message
        | sort -_time
    """.strip()
    return run_spl_query(spl, earliest=window)


def get_error_timeline(window: str = "-4h") -> list[dict]:
    """Get a timeline of all error events across all components — the big picture."""
    spl = f"""
        index={SPLUNK_INDEX} (type="Warning" OR action="DENY" OR sync_status="OutOfSync")
        | eval component=case(
            sourcetype=="kserve:events", "KServe",
            sourcetype=="kyverno:violations", "Kyverno",
            sourcetype=="argocd:events", "ArgoCD",
            sourcetype=="opencost:metrics", "OpenCost",
            true(), "Unknown"
          )
        | table _time, component, name, message
        | sort -_time
        | head 30
    """.strip()
    return run_spl_query(spl, earliest=window)


# ── Demo mode fallback data ───────────────────────────────────────────────────
def _demo_results(spl: str) -> list[dict]:
    """
    Return realistic synthetic results when DEMO_MODE=true.
    Used for the hackathon demo and local testing without a live Splunk instance.
    """
    spl_lower = spl.lower()

    if "kserve" in spl_lower:
        return [
            {
                "name": "neuroscale-bert-classifier",
                "namespace": "default",
                "event_count": "7",
                "reasons": "OOMKilled BackOff",
                "messages": "container 'kserve-container' exceeded memory limit of 256Mi; restarted",
                "last_seen": str(int(time.time()) - 300),
            },
            {
                "name": "neuroscale-sklearn-iris",
                "namespace": "default",
                "event_count": "1",
                "reasons": "Pulled",
                "messages": "Successfully pulled image",
                "last_seen": str(int(time.time()) - 3600),
            },
        ]

    elif "kyverno" in spl_lower:
        return [
            {
                "_time": str(int(time.time()) - 120),
                "resource": "gpt-heavy-v2",
                "namespace": "ml-team-b",
                "policy": "require-standard-labels-inferenceservice",
                "action": "DENY",
                "message": "InferenceService resources must set metadata.labels.owner and metadata.labels.cost-center",
            }
        ]

    elif "opencost" in spl_lower:
        return [
            {"namespace": "ml-team-b",   "total_cost": "0.8341"},
            {"namespace": "ml-team-a",   "total_cost": "0.1203"},
            {"namespace": "default",      "total_cost": "0.0441"},
            {"namespace": "kserve",       "total_cost": "0.0210"},
        ]

    elif "argocd" in spl_lower:
        return [
            {
                "_time": str(int(time.time()) - 60),
                "app_name": "neuroscale-bert-classifier",
                "sync_status": "OutOfSync",
                "health_status": "Degraded",
                "message": "ComparisonError: failed to sync",
            }
        ]

    elif "warning" in spl_lower or "deny" in spl_lower:
        return [
            {"_time": str(int(time.time()) - 300),  "component": "KServe",  "name": "neuroscale-bert-classifier", "message": "OOMKilled"},
            {"_time": str(int(time.time()) - 120),  "component": "Kyverno", "name": "gpt-heavy-v2",               "message": "DENY: missing cost-center label"},
            {"_time": str(int(time.time()) - 60),   "component": "ArgoCD",  "name": "neuroscale-bert-classifier", "message": "Sync OutOfSync"},
        ]

    return [{"result": "no matching demo data for this query", "spl": spl[:100]}]
