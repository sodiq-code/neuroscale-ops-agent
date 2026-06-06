"""
Workflow 2: Policy Violation → Agent Explains → Agent Guides Fix

Triggered when:
  - Developer's model deployment is rejected by Kyverno
  - CI pipeline blocks a PR due to policy simulation failure
  - Splunk shows Kyverno DENY events for a specific resource

This workflow gives developers instant, specific guidance on WHY their
deployment was blocked and EXACTLY what to change — no Slack thread required.
"""
from tools.splunk_client import get_policy_violations, send_to_hec
from tools.runbook_rag import lookup_runbook
from rich.console import Console
import time
import os

console = Console()

# Map policy names → human-readable explanations + fix instructions
POLICY_GUIDANCE = {
    "require-standard-labels-inferenceservice": {
        "what": "Your InferenceService is missing required governance labels.",
        "why": "Every model must have 'owner' and 'cost-center' labels for cost attribution and accountability.",
        "fix": """Add these labels to your Backstage form or InferenceService YAML:
```yaml
metadata:
  labels:
    owner: your-team-name        # e.g. ml-platform, data-science
    cost-center: your-cc-code    # e.g. cc-ml-001
```
In the Backstage template: fill the 'Owner label' and 'Cost center label' fields.""",
        "policy_file": "infrastructure/kyverno/policies/require-standard-labels-inferenceservice.yaml",
    },
    "require-standard-labels-deployment": {
        "what": "Your Deployment is missing required governance labels.",
        "why": "All Deployments must carry owner and cost-center labels for operational tracking.",
        "fix": "Add `metadata.labels.owner` and `metadata.labels.cost-center` to your Deployment spec.",
        "policy_file": "infrastructure/kyverno/policies/require-standard-labels-deployment.yaml",
    },
    "require-resource-requests-limits": {
        "what": "Your workload is missing CPU/memory resource requests or limits.",
        "why": "Unbounded resource consumption can starve other models and cause OOMKilled cascades across the cluster.",
        "fix": """Add resource constraints to your container spec:
```yaml
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi
```
For InferenceServices, set these under `spec.predictor.model.resources`.""",
        "policy_file": "infrastructure/kyverno/policies/require-resource-requests-limits.yaml",
    },
    "disallow-latest-image-tag": {
        "what": "Your container image uses the ':latest' tag.",
        "why": "The ':latest' tag is non-deterministic — it can pull a different image on every restart, making deployments non-reproducible.",
        "fix": "Pin your image to a specific SHA or semantic version tag. Example: `myimage:v1.2.3` or `myimage@sha256:abc123`",
        "policy_file": "infrastructure/kyverno/policies/disallow-latest-image-tag.yaml",
    },
    "disallow-root-containers": {
        "what": "Your container runs as root (UID 0).",
        "why": "Root containers are a critical security risk — a container escape gives full host access.",
        "fix": """Add a security context to your container spec:
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  allowPrivilegeEscalation: false
```""",
        "policy_file": "infrastructure/kyverno/policies/disallow-root-containers.yaml",
    },
}


def run_policy_violation_workflow(resource_name: str = None,
                                   namespace: str = "default") -> dict:
    """
    Query Splunk for policy violations and generate developer-friendly explanations.

    Args:
        resource_name: Specific resource to investigate (None = check all recent)
        namespace: Kubernetes namespace

    Returns:
        Dict with violations, explanations, and exact fix instructions
    """
    console.rule("[bold yellow]Policy Violation Workflow")
    start_time = time.time()

    result = {
        "workflow": "policy_violation",
        "resource": resource_name or "all",
        "violations": [],
        "explanations": [],
        "runbook_section": None,
        "total_violations": 0,
        "duration_seconds": 0,
    }

    # Step 1: Query Splunk for policy violation events
    console.print("[blue]Step 1:[/blue] Querying Splunk for Kyverno DENY events...")
    violations = get_policy_violations(resource_name, window="-24h")

    if not violations or (len(violations) == 1 and "error" in violations[0]):
        result["explanations"].append({
            "summary": "No policy violations found in Splunk for the past 24 hours.",
            "note": "If a deployment was just rejected, ensure Kyverno events are flowing to Splunk via the HEC forwarder.",
        })
        return result

    result["violations"] = violations
    result["total_violations"] = len(violations)

    # Step 2: Enrich each violation with guidance
    console.print(f"[yellow]Found {len(violations)} violation(s). Generating explanations...")

    for violation in violations:
        policy = violation.get("policy", "unknown-policy")
        resource = violation.get("resource", "unknown")
        ns = violation.get("namespace", namespace)
        raw_message = violation.get("message", "")
        timestamp = violation.get("_time", "")

        guidance = POLICY_GUIDANCE.get(policy, {
            "what": f"Resource '{resource}' was rejected by policy '{policy}'.",
            "why": "A Kyverno cluster policy enforces this constraint.",
            "fix": f"Review the policy: {policy}\nRun: kubectl describe clusterpolicy {policy}",
            "policy_file": f"infrastructure/kyverno/policies/{policy}.yaml",
        })

        explanation = {
            "resource": resource,
            "namespace": ns,
            "policy": policy,
            "timestamp": timestamp,
            "raw_message": raw_message,
            "what_happened": guidance["what"],
            "why_it_matters": guidance["why"],
            "how_to_fix": guidance["fix"],
            "policy_file": guidance.get("policy_file", ""),
        }
        result["explanations"].append(explanation)

    # Step 3: Consult runbook for CI-related guidance
    runbook = lookup_runbook("policy violation admission webhook denied CI guardrails")
    result["runbook_section"] = runbook[:400]

    result["duration_seconds"] = round(time.time() - start_time, 2)

    # Log to Splunk
    send_to_hec({
        "event_type": "workflow_complete",
        "workflow": "policy_violation",
        "resource": resource_name,
        "violations_found": result["total_violations"],
        "duration_seconds": result["duration_seconds"],
    }, sourcetype="neuroscale:agent")

    return result
