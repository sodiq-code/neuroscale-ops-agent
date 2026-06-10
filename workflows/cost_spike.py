"""
Workflow 3: Cost Spike → Agent Identifies Culprit → Agent Recommends Fix

Triggered when:
  - OpenCost data in Splunk shows unexpected namespace spend increase
  - A namespace exceeds its cost threshold (configurable per team)
  - Weekly cost review flags an outlier team

OpenCost → Splunk → Agent → specific namespace + root cause + ResourceQuota fix.
"""
from tools.splunk_client import get_cost_attribution, run_spl_query, send_to_hec
from tools.kubernetes_ops import get_opencost_by_namespace
from rich.console import Console
import time

console = Console()

# Cost thresholds per namespace (USD per 6h window)
# Teams exceeding these get flagged
COST_THRESHOLDS = {
    "default": 0.50,
    "ml-team-a": 0.30,
    "ml-team-b": 0.30,
    "kserve": 0.10,
}
DEFAULT_THRESHOLD = 0.25

# ResourceQuota templates for common overspend patterns
RESOURCE_QUOTA_TEMPLATE = """
apiVersion: v1
kind: ResourceQuota
metadata:
  name: {namespace}-budget-guard
  namespace: {namespace}
spec:
  hard:
    requests.cpu: "2"
    requests.memory: 4Gi
    limits.cpu: "4"
    limits.memory: 8Gi
    count/inferenceservices.serving.kserve.io: "3"  # Max 3 models per team
"""

LIMIT_RANGE_TEMPLATE = """
apiVersion: v1
kind: LimitRange
metadata:
  name: {namespace}-default-limits
  namespace: {namespace}
spec:
  limits:
  - default:
      cpu: 500m
      memory: 512Mi
    defaultRequest:
      cpu: 100m
      memory: 256Mi
    type: Container
"""


def run_cost_spike_workflow(window: str = "6h") -> dict:
    """
    Identify cost spikes across all namespaces, name the culprit team,
    and generate specific ResourceQuota + LimitRange recommendations.

    Args:
        window: Time window for cost analysis (e.g. '6h', '24h', '7d')

    Returns:
        Dict with cost breakdown, violating namespaces, and exact YAML fixes
    """
    console.rule("[bold magenta]Cost Spike Workflow")
    start_time = time.time()

    result = {
        "workflow": "cost_spike",
        "window": window,
        "total_cost": 0.0,
        "namespaces": [],
        "violations": [],
        "top_culprit": None,
        "recommendations": [],
        "duration_seconds": 0,
    }

    # Step 1: Query Splunk for cost attribution
    console.print("[blue]Step 1:[/blue] Querying Splunk for OpenCost metrics...")
    splunk_costs = get_cost_attribution(window=f"-{window}")

    # Step 2: Also query OpenCost directly for latest data
    console.print("[blue]Step 2:[/blue] Cross-referencing with OpenCost API...")
    direct_costs = get_opencost_by_namespace(window=window)

    # Merge: prefer Splunk data (aggregated), fall back to direct
    if splunk_costs and not (len(splunk_costs) == 1 and "error" in splunk_costs[0]):
        cost_data = splunk_costs
        cost_source = "splunk"
    else:
        cost_data = direct_costs
        cost_source = "opencost_direct"

    if not cost_data or (len(cost_data) == 1 and "error" in cost_data[0]):
        result["recommendations"].append({
            "type": "config",
            "message": "No cost data available. Ensure OpenCost is running and sending metrics to Splunk HEC.",
            "command": "kubectl get pods -n opencost",
        })
        return result

    result["namespaces"] = cost_data
    result["cost_source"] = cost_source

    # Step 3: Calculate totals and find violators
    total = 0.0
    violations = []

    for ns_data in cost_data:
        ns = ns_data.get("namespace", "unknown")
        # Handle both Splunk (total_cost string) and OpenCost (totalCost float)
        cost = float(ns_data.get("total_cost", ns_data.get("totalCost", 0)))
        total += cost

        threshold = COST_THRESHOLDS.get(ns, DEFAULT_THRESHOLD)
        if cost > threshold:
            overage_pct = round(((cost - threshold) / threshold) * 100, 1)
            violations.append({
                "namespace": ns,
                "cost": cost,
                "threshold": threshold,
                "overage_percent": overage_pct,
                "severity": "critical" if overage_pct > 100 else "warning",
            })

    result["total_cost"] = round(total, 4)
    result["violations"] = sorted(violations, key=lambda x: x["cost"], reverse=True)

    if result["violations"]:
        result["top_culprit"] = result["violations"][0]["namespace"]
        top_ns = result["top_culprit"]
        top_cost = result["violations"][0]["cost"]
        top_overage = result["violations"][0]["overage_percent"]

        console.print(f"[red]Top culprit:[/red] namespace '{top_ns}' | "
                      f"Cost: ${top_cost:.4f} | Over budget by {top_overage}%")

        # Step 4: Dig deeper into the top culprit via Splunk
        console.print(f"[blue]Step 4:[/blue] Investigating '{top_ns}' model spend via Splunk...")
        detail_spl = f"""
            index=neuroscale sourcetype="opencost:metrics" namespace="{top_ns}"
            | stats sum(hourly_cost) as cost, sum(cpu_cores) as cpu, sum(memory_bytes) as memory
              by pod_name
            | sort -cost
        """.strip()
        pod_costs = run_spl_query(detail_spl, earliest=f"-{window}")

        # Step 5: Generate recommendations
        result["recommendations"] = []

        # ResourceQuota recommendation
        result["recommendations"].append({
            "type": "resource_quota",
            "namespace": top_ns,
            "description": f"Apply a ResourceQuota to cap '{top_ns}' spending at budget levels",
            "yaml": RESOURCE_QUOTA_TEMPLATE.format(namespace=top_ns).strip(),
            "command": f"kubectl apply -f - <<'EOF'\n{RESOURCE_QUOTA_TEMPLATE.format(namespace=top_ns).strip()}\nEOF",
        })

        # LimitRange recommendation
        result["recommendations"].append({
            "type": "limit_range",
            "namespace": top_ns,
            "description": f"Apply a LimitRange to set default container resource constraints in '{top_ns}'",
            "yaml": LIMIT_RANGE_TEMPLATE.format(namespace=top_ns).strip(),
        })

        # Splunk alert recommendation
        result["recommendations"].append({
            "type": "splunk_alert",
            "description": "Create a Splunk alert to auto-trigger this workflow when cost spikes occur",
            "spl": f"""
                index=neuroscale sourcetype="opencost:metrics" namespace="{top_ns}"
                | timechart span=1h sum(hourly_cost) as cost
                | where cost > {COST_THRESHOLDS.get(top_ns, DEFAULT_THRESHOLD)}
            """.strip(),
        })

        if pod_costs and not (len(pod_costs) == 1 and "error" in pod_costs[0]):
            result["pod_cost_breakdown"] = pod_costs

    else:
        result["recommendations"].append({
            "type": "info",
            "message": f"All namespaces within budget thresholds. Total cluster cost: ${total:.4f} / {window}",
        })

    result["duration_seconds"] = round(time.time() - start_time, 2)

    # Log to Splunk
    send_to_hec({
        "event_type": "workflow_complete",
        "workflow": "cost_spike",
        "total_cost": result["total_cost"],
        "violations": len(result["violations"]),
        "top_culprit": result["top_culprit"],
        "duration_seconds": result["duration_seconds"],
    }, sourcetype="neuroscale:agent")

    return result
