"""
Workflow 1: Model Down → Agent Diagnoses → Agent Fixes

Triggered when:
  - KServe InferenceService shows READY=False in Splunk
  - OOMKilled events detected for a predictor pod
  - Splunk alert threshold exceeded (7+ error events in 1h)

The agent queries Splunk, reads the runbook, determines root cause,
takes action (restart or memory patch), and reports outcome.
All steps are logged back to Splunk for observability.
"""
from tools.splunk_client import get_model_health, get_error_timeline, send_to_hec
from tools.runbook_rag import lookup_runbook
from tools.kubernetes_ops import restart_inference_service, patch_inference_service_memory
from rich.console import Console
import time

console = Console()


def run_model_down_workflow(model_name: str = None) -> dict:
    """
    Autonomous model-down diagnosis and remediation.

    This is a scripted (non-LLM) version of the workflow — deterministic,
    fast, and suitable for automated alert-triggered remediation.

    Args:
        model_name: Specific model to investigate (None = check all)

    Returns:
        Dict with root_cause, action_taken, outcome, prevention
    """
    console.rule("[bold red]Model Down Workflow")
    start_time = time.time()

    result = {
        "workflow": "model_down",
        "model": model_name or "all",
        "splunk_data": [],
        "root_cause": None,
        "action_taken": None,
        "action_result": None,
        "runbook_section": None,
        "prevention": None,
        "duration_seconds": 0,
    }

    # Step 1: Query Splunk for model health events
    console.print("[blue]Step 1:[/blue] Querying Splunk for KServe events...")
    health_data = get_model_health(model_name, window="-1h")
    result["splunk_data"] = health_data

    if not health_data or (len(health_data) == 1 and "error" in health_data[0]):
        result["root_cause"] = "No data in Splunk — cluster may not be sending events to HEC"
        result["prevention"] = "Verify k8s-to-splunk forwarder is running: kubectl get pods -n neuroscale-ops"
        return result

    # Step 2: Identify the most problematic model
    target = None
    for entry in health_data:
        reasons = entry.get("reasons", "").lower()
        if any(bad in reasons for bad in ["oom", "crash", "error", "backoff", "kill"]):
            target = entry
            break

    if not target and health_data:
        target = health_data[0]  # Take most frequent

    if not target:
        result["root_cause"] = "No critical events found — all models appear healthy"
        return result

    target_name = target.get("name", model_name or "unknown")
    reasons = target.get("reasons", "").lower()
    messages = target.get("messages", "")
    event_count = int(target.get("event_count", 0))

    console.print(f"[yellow]Target model:[/yellow] {target_name} | Events: {event_count} | Reasons: {reasons}")

    # Step 3: Root cause classification
    if "oom" in reasons or "memory" in messages.lower():
        result["root_cause"] = f"OOMKilled — memory limit exceeded. Model '{target_name}' restarted {event_count}x in 1h."
        symptom = "OOMKilled memory limit exceeded container restart"

    elif "backoff" in reasons or "crashloop" in reasons.lower():
        result["root_cause"] = f"CrashLoopBackOff — predictor pod failing repeatedly ({event_count} events)."
        symptom = "CrashLoopBackOff predictor pod failing"

    elif "image" in reasons or "pull" in reasons:
        result["root_cause"] = f"ImagePullError — cannot pull predictor container image for '{target_name}'."
        symptom = "ImagePullBackOff container image pull error"

    else:
        result["root_cause"] = f"Unknown degradation — {event_count} warning events for '{target_name}'. Reasons: {reasons}"
        symptom = f"unknown model failure {reasons}"

    # Step 4: Consult runbook
    console.print("[blue]Step 3:[/blue] Consulting runbook...")
    runbook_section = lookup_runbook(symptom)
    result["runbook_section"] = runbook_section[:500]  # Preview

    # Step 5: Take action based on root cause
    console.print("[blue]Step 4:[/blue] Taking remediation action...")

    if "oom" in result["root_cause"].lower():
        # Patch memory first, then restart
        patch_result = patch_inference_service_memory(target_name, new_limit="512Mi")
        result["action_taken"] = f"Memory limit patched to 512Mi for '{target_name}'"
        result["action_result"] = patch_result

        if patch_result.get("success"):
            result["prevention"] = (
                "Set memory requests/limits in Backstage template defaults. "
                "Add Splunk alert: >5 OOM events in 30min → auto-patch + notify."
            )

    else:
        # Restart the predictor pod
        restart_result = restart_inference_service(target_name)
        result["action_taken"] = f"Predictor pod restarted for '{target_name}'"
        result["action_result"] = restart_result

        if restart_result.get("success"):
            result["prevention"] = (
                "Enable Kyverno liveness probe policy. "
                "Add Splunk alert: >3 restarts in 15min → auto-trigger this workflow."
            )

    result["duration_seconds"] = round(time.time() - start_time, 2)

    # Log outcome to Splunk
    send_to_hec({
        "event_type": "workflow_complete",
        "workflow": "model_down",
        "model": target_name,
        "root_cause": result["root_cause"],
        "action": result["action_taken"],
        "success": result["action_result"].get("success", False) if result["action_result"] else False,
        "duration_seconds": result["duration_seconds"],
    }, sourcetype="neuroscale:agent")

    return result
