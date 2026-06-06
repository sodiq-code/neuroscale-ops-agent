"""
Splunk Custom Alert Action — trigger_agent.py

This is a Splunk Python SDK alert action that automatically triggers
the NeuroScale Ops Agent when a Splunk alert fires.

When Splunk detects:
  - >7 KServe error events in 1h → triggers model_down workflow
  - Any Kyverno DENY event → triggers policy_violation workflow
  - Namespace cost > threshold → triggers cost_spike workflow

Installation:
  1. Place this file in: $SPLUNK_HOME/etc/apps/neuroscale_ops/bin/
  2. Configure the alert action in Splunk UI under Settings → Alert Actions
  3. Set the AGENT_URL environment variable to your running agent endpoint

This is what makes the system fully autonomous — Splunk detects the problem,
calls this script, which calls the agent, which remediates the cluster.
"""
import sys
import os
import json
import logging
import requests
from pathlib import Path

# Splunk calls this script with alert info on stdin
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("neuroscale-alert-action")

AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8501")


def parse_splunk_alert() -> dict:
    """Parse Splunk alert payload from stdin."""
    try:
        payload = json.load(sys.stdin)
        return payload
    except Exception as exc:
        log.error(f"Failed to parse Splunk alert payload: {exc}")
        return {}


def detect_workflow(alert: dict) -> tuple[str, dict]:
    """
    Determine which workflow to trigger based on alert metadata.

    Returns:
        (workflow_name, workflow_args)
    """
    search_name = alert.get("search_name", "").lower()
    results = alert.get("result", {})

    if "kserve" in search_name or "model" in search_name:
        model_name = results.get("name", results.get("model_name"))
        return "model_down", {"model_name": model_name}

    elif "kyverno" in search_name or "policy" in search_name or "violation" in search_name:
        resource = results.get("resource", results.get("name"))
        return "policy_violation", {"resource_name": resource}

    elif "cost" in search_name or "opencost" in search_name or "spend" in search_name:
        return "cost_spike", {"window": "6h"}

    else:
        # Generic: pass alert description to the conversational agent
        return "chat", {"message": f"Splunk alert fired: {alert.get('search_name')}. Results: {json.dumps(results)[:200]}"}


def trigger_agent_workflow(workflow: str, args: dict) -> bool:
    """Call the NeuroScale Ops Agent API to run a workflow."""
    try:
        resp = requests.post(
            f"{AGENT_URL}/api/workflow",
            json={"workflow": workflow, "args": args},
            timeout=120,
        )
        if resp.status_code == 200:
            result = resp.json()
            log.info(f"Agent workflow '{workflow}' completed: {json.dumps(result)[:200]}")
            return True
        else:
            log.error(f"Agent returned {resp.status_code}: {resp.text[:200]}")
            return False
    except requests.exceptions.ConnectionError:
        log.warning(f"Agent not reachable at {AGENT_URL}. Running workflow locally.")
        return run_workflow_locally(workflow, args)
    except Exception as exc:
        log.error(f"Failed to trigger agent: {exc}")
        return False


def run_workflow_locally(workflow: str, args: dict) -> bool:
    """Fallback: run workflow directly if agent API is not reachable."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    try:
        if workflow == "model_down":
            from workflows.model_down import run_model_down_workflow
            result = run_model_down_workflow(**args)
            log.info(f"Model down workflow result: {result.get('root_cause')} → {result.get('action_taken')}")

        elif workflow == "policy_violation":
            from workflows.policy_violation import run_policy_violation_workflow
            result = run_policy_violation_workflow(**args)
            log.info(f"Policy violation workflow: found {result.get('total_violations')} violations")

        elif workflow == "cost_spike":
            from workflows.cost_spike import run_cost_spike_workflow
            result = run_cost_spike_workflow(**args)
            log.info(f"Cost spike workflow: culprit={result.get('top_culprit')}, total=${result.get('total_cost')}")

        return True
    except Exception as exc:
        log.error(f"Local workflow execution failed: {exc}")
        return False


def main():
    log.info("NeuroScale alert action triggered by Splunk")

    alert = parse_splunk_alert()
    if not alert:
        log.warning("Empty alert payload — exiting")
        sys.exit(0)

    log.info(f"Alert: {alert.get('search_name')} | Severity: {alert.get('severity')}")

    workflow, args = detect_workflow(alert)
    log.info(f"Triggering workflow: {workflow} with args: {args}")

    success = trigger_agent_workflow(workflow, args)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
