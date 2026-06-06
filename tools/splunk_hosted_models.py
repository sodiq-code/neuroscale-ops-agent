"""
Splunk Hosted Models — Native AI inference via SPL `| ai` command

This module integrates Splunk's hosted generative AI models directly into the
NeuroScale Ops Agent. Instead of calling external AI APIs, we pipe operational
data through Splunk's own AI layer — keeping everything inside the Splunk
security perimeter with zero data egress.

Hosted models available (Splunk Cloud Platform + AI Toolkit 5.7+):
  - foundation-sec-1.1-8b-instruct : Security-tuned 8B model for alert triage,
                                      incident summarisation, policy analysis
  - gpt-oss-120b                    : Large reasoning model for complex analysis
  - gpt-oss-20b                     : Fast model for quick summaries
  - cisco-tdt (Deep Time Series)    : Zero-shot time-series forecasting & anomaly

Usage (via SPL `| ai` command):
  index=neuroscale | head 50 | ai model=foundation-sec-1.1-8b-instruct
      prompt="Triage these security policy violations and rank by severity"

Docs:
  https://help.splunk.com/en/splunk-cloud-platform/get-started-with-splunk-ai
  https://www.splunk.com/en_us/products/ai-toolkit.html

NOTE: Splunk Hosted Models are a Splunk Cloud Platform feature — they require
the AI Toolkit app (version 5.7+) installed and a valid Developer License.
For Splunk Enterprise (on-prem) without Cloud, we fall back to SPL-only analysis
or DEMO_MODE synthetic responses.
"""
import os
import json
import time
from typing import Any
from rich.console import Console

from tools.splunk_client import run_spl_query, _get_service, DEMO_MODE, SPLUNK_INDEX

console = Console()

# ── Model identifiers (Splunk AI Toolkit 5.7+) ───────────────────────────────
MODEL_FOUNDATION_SEC  = "foundation-sec-1.1-8b-instruct"   # Security analysis
MODEL_GPT_OSS_120B    = "gpt-oss-120b"                      # Complex reasoning
MODEL_GPT_OSS_20B     = "gpt-oss-20b"                       # Fast summaries
MODEL_DEEP_TIME_SERIES = "cisco-tdt"                         # Time-series / anomaly

# Whether Splunk Cloud + AI Toolkit is available
SPLUNK_CLOUD_HOSTED_MODELS = os.getenv("SPLUNK_CLOUD_HOSTED_MODELS", "false").lower() == "true"


def _splunk_ai_query(base_spl: str, prompt: str, model: str, max_results: int = 20) -> dict:
    """
    Execute a Splunk SPL query that pipes results into the `| ai` command,
    invoking a Splunk-hosted generative AI model inline.

    This is the core mechanism for Splunk Hosted Model integration:
      1. Run the SPL to retrieve relevant events from the neuroscale index
      2. Pipe those events into `| ai model=<model> prompt="<prompt>"`
      3. Splunk's AI layer runs inference entirely within the Splunk perimeter
      4. Return the model's analysis as structured output

    Args:
        base_spl:    SPL to retrieve data (without the | ai portion)
        prompt:      Natural language instruction for the hosted model
        model:       Splunk hosted model identifier
        max_results: Max events to feed the model

    Returns:
        dict with keys: model, analysis, raw_results, spl_used, latency_ms
    """
    if DEMO_MODE:
        return _demo_ai_response(model, prompt, base_spl)

    if not SPLUNK_CLOUD_HOSTED_MODELS:
        # Splunk Enterprise / no AI Toolkit — do SPL-only analysis
        console.print(
            f"[yellow]⚠ Splunk Hosted Models require Splunk Cloud + AI Toolkit 5.7+. "
            f"Set SPLUNK_CLOUD_HOSTED_MODELS=true to enable. Falling back to SPL analysis.[/yellow]"
        )
        return _spl_only_fallback(base_spl, prompt, model)

    # Build the full SPL with | ai command
    # The | ai command is provided by AI Toolkit and runs the model server-side
    safe_prompt = prompt.replace('"', '\\"')
    full_spl = (
        f"{base_spl} | head {max_results} "
        f'| ai model="{model}" prompt="{safe_prompt}"'
    )

    console.print(f"[cyan]🤖 Splunk Hosted Model: {model}[/cyan]")
    console.print(f"[dim]SPL: {full_spl[:120]}...[/dim]")

    start = time.time()
    try:
        service = _get_service()
        kwargs = {
            "exec_mode": "blocking",
            "earliest_time": "-1h",
            "latest_time": "now",
            "count": 1,  # | ai returns one synthesised result row
        }
        job = service.jobs.create(full_spl, **kwargs)

        import splunklib.results as splunk_results
        reader = splunk_results.JSONResultsReader(job.results(output_mode="json"))
        rows = [r for r in reader if isinstance(r, dict)]
        latency_ms = int((time.time() - start) * 1000)

        # The | ai command returns a field typically named "_ai_response" or "response"
        analysis = ""
        for row in rows:
            analysis = (
                row.get("_ai_response")
                or row.get("response")
                or row.get("ai_response")
                or row.get("answer")
                or str(row)
            )
            if analysis:
                break

        return {
            "model": model,
            "analysis": analysis or "No response from hosted model.",
            "raw_results": rows,
            "spl_used": full_spl,
            "latency_ms": latency_ms,
            "source": "splunk_hosted_model",
        }

    except Exception as e:
        console.print(f"[red]Splunk Hosted Model error: {e}[/red]")
        return {
            "model": model,
            "analysis": f"Hosted model unavailable: {e}. Ensure AI Toolkit 5.7+ is installed.",
            "raw_results": [],
            "spl_used": full_spl,
            "latency_ms": 0,
            "source": "error",
            "error": str(e),
        }


def analyze_security_events(context: str = "", namespace: str = "all") -> dict:
    """
    Use Foundation-Sec model to triage Kyverno policy violations and
    security-relevant K8s events.

    Foundation-Sec-1.1-8B-Instruct is purpose-built for security operations:
    - Trained on 5B security tokens
    - Understands MITRE ATT&CK, Kubernetes security posture
    - Returns severity rankings, remediation steps, risk scores

    Args:
        context:   Additional context string to include in the prompt
        namespace: K8s namespace to scope analysis (or "all")

    Returns:
        dict with security analysis from Foundation-Sec model
    """
    ns_filter = f'namespace="{namespace}"' if namespace != "all" else ""
    base_spl = (
        f'index={SPLUNK_INDEX} sourcetype="neuroscale:policies" '
        f'{ns_filter} '
        f'| eval event_summary=action." | ".policy." | ".resource_name '
        f'| table _time, namespace, policy, action, resource_name, severity, event_summary'
    )

    prompt = (
        f"You are a Kubernetes security analyst. Analyze these Kyverno policy violations "
        f"from a production ML platform. For each violation: (1) assess severity HIGH/MEDIUM/LOW, "
        f"(2) explain the security risk, (3) provide a specific remediation command. "
        f"Prioritize violations that could lead to privilege escalation or data exfiltration. "
        f"{context}"
    )

    result = _splunk_ai_query(base_spl, prompt, MODEL_FOUNDATION_SEC)
    result["use_case"] = "security_triage"
    return result


def forecast_resource_usage(namespace: str = "inference", metric: str = "cost") -> dict:
    """
    Use Cisco Deep Time Series Model for zero-shot forecasting of
    resource consumption and anomaly detection.

    Cisco DTS Model capabilities:
    - Zero-shot: no training data required
    - Forecasts infrastructure metrics, application performance, network traffic
    - Detects anomalies in time-series (cost spikes, latency surges, error bursts)

    Args:
        namespace: K8s namespace to analyse
        metric:    "cost", "latency", "errors", or "replicas"

    Returns:
        dict with forecasted values and anomaly flags
    """
    metric_field_map = {
        "cost":     ("neuroscale:costs",    "hourly_cost"),
        "latency":  ("neuroscale:models",   "latency_p99"),
        "errors":   ("neuroscale:models",   "error_rate"),
        "replicas": ("neuroscale:argocd",   "replica_count"),
    }
    sourcetype, field = metric_field_map.get(metric, ("neuroscale:costs", "hourly_cost"))

    base_spl = (
        f'index={SPLUNK_INDEX} sourcetype="{sourcetype}" namespace="{namespace}" '
        f'| timechart span=5m avg({field}) as metric_value '
        f'| head 60'  # last 5 hours at 5m granularity
    )

    prompt = (
        f"Analyze this time-series of {metric} metrics for namespace '{namespace}'. "
        f"1) Identify any anomalies or sudden changes. "
        f"2) Forecast the next 30 minutes trend (increasing/stable/decreasing). "
        f"3) Flag if immediate action is needed and why. "
        f"Return structured output: anomaly_detected (yes/no), trend, forecast_30m, action_required."
    )

    result = _splunk_ai_query(base_spl, prompt, MODEL_DEEP_TIME_SERIES)
    result["use_case"] = "time_series_forecast"
    result["metric"] = metric
    result["namespace"] = namespace
    return result


def summarize_incident(alert_type: str, alert_data: dict) -> dict:
    """
    Use GPT-OSS-20B for fast incident summarization when an alert fires.
    Called automatically by alert-actions/trigger_agent.py.

    Args:
        alert_type: "model_down" | "cost_spike" | "policy_violation" | "argo_sync_fail"
        alert_data: Alert payload dict from Splunk webhook

    Returns:
        dict with: summary, root_cause_hypothesis, recommended_workflow, severity
    """
    # Pull recent events relevant to the alert type
    sourcetype_map = {
        "model_down":        "neuroscale:models",
        "cost_spike":        "neuroscale:costs",
        "policy_violation":  "neuroscale:policies",
        "argo_sync_fail":    "neuroscale:argocd",
    }
    sourcetype = sourcetype_map.get(alert_type, "neuroscale:models")

    base_spl = (
        f'index={SPLUNK_INDEX} sourcetype="{sourcetype}" '
        f'| head 30 '
        f'| table _time, namespace, status, message, severity'
    )

    alert_summary = json.dumps(alert_data, indent=2)[:500]  # truncate for prompt
    prompt = (
        f"A Splunk alert fired: '{alert_type}'. Alert data: {alert_summary}. "
        f"Based on the recent events below, provide: "
        f"1) A one-sentence incident summary. "
        f"2) Most likely root cause. "
        f"3) Recommended automated workflow: model_down | policy_violation | cost_spike | none. "
        f"4) Severity: critical | high | medium | low. "
        f"Be concise — this goes to an on-call engineer."
    )

    result = _splunk_ai_query(base_spl, prompt, MODEL_GPT_OSS_20B)
    result["use_case"] = "incident_summary"
    result["alert_type"] = alert_type
    return result


def generate_spl_query(natural_language_request: str) -> dict:
    """
    Use GPT-OSS-120B to generate SPL from natural language — AI Assistant pattern.
    This mimics the Splunk AI Assistant capability using the hosted model directly.

    Args:
        natural_language_request: Plain English description of what to query

    Returns:
        dict with: generated_spl, explanation, model
    """
    # Use the index schema as context
    base_spl = (
        f'index={SPLUNK_INDEX} '
        f'| head 5 '
        f'| table _time, namespace, sourcetype, status, message'
    )

    prompt = (
        f"You are a Splunk SPL expert. The user wants to query the '{SPLUNK_INDEX}' index "
        f"which has these sourcetypes: neuroscale:models (KServe inference), "
        f"neuroscale:costs (OpenCost spend), neuroscale:policies (Kyverno violations), "
        f"neuroscale:argocd (GitOps sync). "
        f"Write a valid SPL query for: '{natural_language_request}'. "
        f"Return ONLY the SPL query on the first line, then a brief explanation."
    )

    result = _splunk_ai_query(base_spl, prompt, MODEL_GPT_OSS_120B)
    result["use_case"] = "spl_generation"
    result["request"] = natural_language_request
    return result


# ── Demo mode responses ───────────────────────────────────────────────────────
def _demo_ai_response(model: str, prompt: str, base_spl: str) -> dict:
    """Realistic synthetic responses for demo mode — no Splunk Cloud needed."""
    demo_responses = {
        MODEL_FOUNDATION_SEC: {
            "analysis": (
                "SECURITY TRIAGE REPORT (Foundation-Sec-1.1-8B)\n\n"
                "HIGH: Policy 'disallow-root-containers' blocked pod 'inference-worker-7d9f' "
                "in namespace 'inference'. Root containers bypass Linux user namespaces — "
                "remediation: add securityContext.runAsNonRoot=true to pod spec.\n\n"
                "MEDIUM: Policy 'require-resource-limits' blocking 3 deployments. "
                "Unrestricted CPU/memory allows noisy-neighbour attacks on shared nodes. "
                "Remediation: kubectl set resources deployment <name> --limits=cpu=2,memory=4Gi\n\n"
                "LOW: Policy 'disallow-latest-tag' warning on 'backstage:latest'. "
                "Pin to digest or explicit semver tag to prevent supply-chain drift."
            ),
            "source": "splunk_hosted_model_demo",
        },
        MODEL_DEEP_TIME_SERIES: {
            "analysis": (
                "TIME SERIES FORECAST (Cisco Deep Time Series)\n\n"
                "anomaly_detected: YES\n"
                "trend: INCREASING (+34% over last 45 min)\n"
                "forecast_30m: Cost will reach $67.40/hr if current trajectory continues\n"
                "action_required: YES — scale down over-provisioned inference replicas "
                "in 'inference' namespace. neuroscale-bert-classifier has 8 replicas "
                "with <15% utilisation. Scale to 3 replicas to reduce cost by ~60%."
            ),
            "source": "splunk_hosted_model_demo",
        },
        MODEL_GPT_OSS_20B: {
            "analysis": (
                "INCIDENT SUMMARY (GPT-OSS-20B)\n\n"
                "Summary: KServe InferenceService 'neuroscale-bert-classifier' entered "
                "error state at 14:32 UTC due to OOMKilled container.\n\n"
                "Root cause: Memory limit (2Gi) insufficient for model warm-up with "
                "batch_size=32. Node memory pressure triggered eviction.\n\n"
                "Recommended workflow: model_down\n"
                "Severity: high"
            ),
            "source": "splunk_hosted_model_demo",
        },
        MODEL_GPT_OSS_120B: {
            "analysis": (
                "GENERATED SPL (GPT-OSS-120B)\n\n"
                "index=neuroscale sourcetype=\"neuroscale:costs\" "
                "| timechart span=1h sum(hourly_cost) by namespace "
                "| eval threshold=50 "
                "| where 'inference' > threshold\n\n"
                "Explanation: Aggregates hourly cost per namespace using OpenCost data, "
                "then filters to namespaces exceeding $50/hr threshold. "
                "Use this to trigger cost_spike workflow."
            ),
            "source": "splunk_hosted_model_demo",
        },
    }

    response = demo_responses.get(model, {
        "analysis": f"[DEMO] {model} responded to: {prompt[:80]}...",
        "source": "splunk_hosted_model_demo",
    })

    return {
        "model": model,
        "spl_used": base_spl,
        "latency_ms": 420,
        **response,
    }


def _spl_only_fallback(base_spl: str, prompt: str, model: str) -> dict:
    """
    When Splunk Hosted Models aren't available (Enterprise, no AI Toolkit),
    run the base SPL and return raw results with a note.
    """
    try:
        rows = run_spl_query(base_spl)
        summary = f"SPL returned {len(rows)} events. Hosted model '{model}' not available " \
                  f"(requires Splunk Cloud + AI Toolkit 5.7+). " \
                  f"Set SPLUNK_CLOUD_HOSTED_MODELS=true and ensure AI Toolkit is installed."
        return {
            "model": model,
            "analysis": summary,
            "raw_results": rows[:5],
            "spl_used": base_spl,
            "latency_ms": 0,
            "source": "spl_fallback",
        }
    except Exception as e:
        return {
            "model": model,
            "analysis": f"SPL fallback failed: {e}",
            "raw_results": [],
            "spl_used": base_spl,
            "latency_ms": 0,
            "source": "error",
        }
