"""
NeuroScale Ops Agent — Core Agentic Loop

This is the brain of the system. It uses OpenAI function-calling to let the LLM
decide which Splunk queries to run, which runbook sections to consult, and which
remediation actions to take — all in a closed loop.

Architecture:
  User Query → LLM decides tools → Splunk returns data → Runbook consulted
  → Remediation action taken → LLM synthesizes response → User sees root cause + fix

Every action is logged back to Splunk via HEC so the agent's own operations
are observable in the same dashboard it uses for diagnosis.
"""
import os
import json
import time
from typing import Any, Generator
from openai import OpenAI
from rich.console import Console

from tools.splunk_client import (
    run_spl_query, get_model_health, get_policy_violations,
    get_cost_attribution, get_argocd_sync_events, get_error_timeline,
    send_to_hec,
)
from tools.runbook_rag import lookup_runbook
from tools.splunk_hosted_models import (
    analyze_security_events,
    forecast_resource_usage,
    summarize_incident,
    generate_spl_query,
)
from tools.kubernetes_ops import (
    argocd_sync, restart_inference_service, patch_inference_service_memory,
    get_inference_services, get_argocd_status, get_opencost_by_namespace,
)

console = Console()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "llama-3.1-70b-versatile")
DEMO_MODE    = os.getenv("DEMO_MODE", "false").lower() == "true"

# ── Tool Definitions (OpenAI function calling) ────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_splunk",
            "description": (
                "Run a Splunk SPL query to retrieve real observability data from the "
                "NeuroScale cluster. Use this FIRST before any diagnosis or action. "
                "Queries KServe inference events, Kyverno policy violations, OpenCost "
                "cost metrics, and ArgoCD sync events."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spl": {"type": "string", "description": "The SPL query to execute"},
                    "earliest": {"type": "string", "default": "-1h",
                                 "description": "Splunk time modifier (e.g. -1h, -24h, -7d)"},
                },
                "required": ["spl"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_model_health",
            "description": "Get KServe InferenceService health events from Splunk. Shows errors, OOM kills, restart counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_name": {"type": "string", "description": "Specific model name, or omit for all models"},
                    "window": {"type": "string", "default": "-1h"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_policy_violations",
            "description": "Get Kyverno admission denial events from Splunk. Shows which resources were blocked and why.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {"type": "string", "description": "Resource name to filter on"},
                    "window": {"type": "string", "default": "-24h"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cost_attribution",
            "description": "Get OpenCost spend data from Splunk, ranked by namespace. Use when investigating cost spikes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string", "default": "-6h"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_error_timeline",
            "description": "Get a cross-component error timeline from Splunk. Best for initial triage — shows KServe, Kyverno, ArgoCD errors together.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string", "default": "-4h"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_runbook",
            "description": (
                "Search the NeuroScale operational runbook for recovery procedures. "
                "The runbook contains real battle-tested procedures for: ArgoCD CrashLoopBackOff, "
                "Kyverno webhook disruption, KServe InferenceService stuck, Backstage crashes, "
                "GitHub token expiry, CI false-green on policy checks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symptom": {"type": "string", "description": "The symptom or error you want a procedure for"},
                },
                "required": ["symptom"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_argocd_sync",
            "description": "Trigger an ArgoCD hard refresh and sync for a specific application. Use after diagnosing an OutOfSync or Degraded application.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "ArgoCD application name"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_inference_service",
            "description": "Restart a stuck KServe InferenceService by cycling its predictor pod. Use after confirming the issue in Splunk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "InferenceService name"},
                    "namespace": {"type": "string", "default": "default"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_memory_limit",
            "description": "Increase the memory limit of a KServe InferenceService to fix OOMKilled errors. Only call this when Splunk confirms OOM as root cause.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "InferenceService name"},
                    "namespace": {"type": "string", "default": "default"},
                    "new_limit": {"type": "string", "default": "512Mi",
                                  "description": "New memory limit (e.g. 512Mi, 1Gi)"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cluster_overview",
            "description": "Get a current snapshot of all InferenceServices and ArgoCD applications in the cluster.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cost_direct",
            "description": "Query OpenCost directly (not via Splunk) for real-time namespace cost attribution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string", "default": "6h"},
                },
                "required": [],
            },
        },
    },
    # ── Splunk Hosted Models (AI Toolkit 5.7+) ─────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "splunk_security_analysis",
            "description": (
                "Use Splunk's Foundation-Sec-1.1-8B hosted model to perform AI-powered "
                "security triage on Kyverno policy violations and K8s security events. "
                "This model runs INSIDE Splunk — data never leaves the Splunk perimeter. "
                "Use this when a policy violation alert fires or when a security posture "
                "assessment is requested. Returns severity ranking, risk explanation, "
                "and specific remediation commands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "context": {
                        "type": "string",
                        "description": "Additional context to pass to the model (e.g. alert details)",
                    },
                    "namespace": {
                        "type": "string",
                        "description": "K8s namespace to scope analysis, or 'all'",
                        "default": "all",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "splunk_forecast",
            "description": (
                "Use Splunk's Cisco Deep Time Series hosted model for zero-shot forecasting "
                "and anomaly detection on operational metrics (cost, latency, errors, replicas). "
                "This model runs INSIDE Splunk — no training data required. "
                "Use this to predict resource exhaustion, detect cost trajectory before it spikes, "
                "or flag anomalous latency patterns before they cause SLA breaches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "K8s namespace to analyse",
                        "default": "inference",
                    },
                    "metric": {
                        "type": "string",
                        "enum": ["cost", "latency", "errors", "replicas"],
                        "description": "Metric to forecast",
                        "default": "cost",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "splunk_generate_spl",
            "description": (
                "Use Splunk's GPT-OSS-120B hosted model to generate SPL queries from "
                "natural language. This is the AI Assistant pattern — describe what you "
                "want to query in plain English and get valid SPL back. "
                "Use this when you need a custom SPL query that isn't covered by the "
                "standard tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "description": "Natural language description of what to query",
                    },
                },
                "required": ["request"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are the NeuroScale Ops Agent — an autonomous incident commander for a 
production Kubernetes MLOps platform. You operate with precision, speed, and complete transparency.

## Platform Architecture
- **ArgoCD**: GitOps controller (7 applications managed)
- **KServe**: AI model serving on Kubernetes (InferenceService CRD)
- **Kyverno**: Policy-as-code admission controller (5 cluster policies)
- **OpenCost**: Real-time cost attribution per namespace
- **Backstage**: Developer golden path portal (scaffolded model deployments)
- **Splunk**: Observability brain (KServe events, Kyverno violations, OpenCost metrics, ArgoCD sync events)
- **Splunk Hosted Models**: Foundation-Sec-1.1-8B (security triage), Cisco Deep Time Series (forecasting), GPT-OSS-120B (SPL generation) — all run inside Splunk, zero data egress

## How You Operate
1. **Always query Splunk first.** Never diagnose without data.
2. **Always check the runbook.** It contains real recovery procedures from actual incidents.
3. **Show your reasoning step by step.** Use clear sections: 🔍 Diagnosis → 📖 Runbook → ⚡ Action → ✅ Outcome.
4. **Never take destructive actions.** No deletes on production resources — only restarts and patches.
5. **Always report prevention.** Root cause + action + "how to prevent this again."

## Response Format
Structure every response like this:
```
🔍 **Splunk Intelligence** — what the data shows
📖 **Runbook Reference** — applicable recovery procedure  
⚡ **Action Taken** — what you did
✅ **Outcome** — current state after remediation
🛡️ **Prevention** — how to avoid this in future
```

Be direct and technical. Platform engineers don't want fluff — they want root cause, 
action taken, and prevention in 200 words or less.
"""


# ── Tool Dispatch ─────────────────────────────────────────────────────────────
def dispatch_tool(name: str, args: dict) -> Any:
    """Execute a tool call and return its result."""
    start = time.time()

    if name == "query_splunk":
        result = run_spl_query(args["spl"], args.get("earliest", "-1h"))
    elif name == "get_model_health":
        result = get_model_health(args.get("model_name"), args.get("window", "-1h"))
    elif name == "get_policy_violations":
        result = get_policy_violations(args.get("resource"), args.get("window", "-24h"))
    elif name == "get_cost_attribution":
        result = get_cost_attribution(args.get("window", "-6h"))
    elif name == "get_error_timeline":
        result = get_error_timeline(args.get("window", "-4h"))
    elif name == "lookup_runbook":
        result = lookup_runbook(args["symptom"])
    elif name == "trigger_argocd_sync":
        result = argocd_sync(args["app_name"])
    elif name == "restart_inference_service":
        result = restart_inference_service(args["name"], args.get("namespace", "default"))
    elif name == "patch_memory_limit":
        result = patch_inference_service_memory(
            args["name"], args.get("namespace", "default"), args.get("new_limit", "512Mi")
        )
    elif name == "get_cluster_overview":
        result = {
            "inference_services": get_inference_services(),
            "argocd_apps": get_argocd_status(),
        }
    elif name == "get_cost_direct":
        result = get_opencost_by_namespace(args.get("window", "6h"))
    # ── Splunk Hosted Models ──────────────────────────────────────────────────
    elif name == "splunk_security_analysis":
        result = analyze_security_events(
            context=args.get("context", ""),
            namespace=args.get("namespace", "all"),
        )
    elif name == "splunk_forecast":
        result = forecast_resource_usage(
            namespace=args.get("namespace", "inference"),
            metric=args.get("metric", "cost"),
        )
    elif name == "splunk_generate_spl":
        result = generate_spl_query(args["request"])
    else:
        result = {"error": f"Unknown tool: {name}"}

    duration_ms = round((time.time() - start) * 1000)

    # Log tool execution back to Splunk (agent observability)
    send_to_hec({
        "event_type": "agent_tool_call",
        "tool": name,
        "args": args,
        "duration_ms": duration_ms,
        "result_count": len(result) if isinstance(result, list) else 1,
    }, sourcetype="neuroscale:agent")

    return result


# ── Main Agent Loop ───────────────────────────────────────────────────────────
class NeuroScaleOpsAgent:
    """
    Multi-turn agentic loop with full tool-calling support.
    Maintains conversation history for context across turns.
    """

    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        )
        self.history: list[dict] = []
        self.tool_calls_log: list[dict] = []  # For UI transparency panel

    def reset(self):
        self.history = []
        self.tool_calls_log = []

    def run(self, user_message: str) -> tuple[str, list[dict]]:
        """
        Run the agent for a single user message.

        Returns:
            (response_text, tool_calls_log)
        """
        self.tool_calls_log = []
        self.history.append({"role": "user", "content": user_message})

        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history

        max_iterations = 8  # Prevent infinite loops
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            response = self.client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.1,  # Low temp for precise ops decisions
            )

            msg = response.choices[0].message

            # No more tool calls — we have the final answer
            if not msg.tool_calls:
                final_text = msg.content or ""
                self.history.append({"role": "assistant", "content": final_text})
                return final_text, self.tool_calls_log

            # Execute tool calls
            messages.append(msg)  # Append assistant message with tool_calls

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                tool_args = json.loads(tc.function.arguments)

                console.print(f"[cyan]→ Tool:[/cyan] {tool_name}({json.dumps(tool_args)[:80]})")

                result = dispatch_tool(tool_name, tool_args)
                result_str = json.dumps(result, indent=2)

                # Log for UI transparency panel
                self.tool_calls_log.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result_preview": result_str[:500],
                    "result_count": len(result) if isinstance(result, list) else 1,
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        return "Max iterations reached. Please try a more specific query.", self.tool_calls_log

    def run_streaming(self, user_message: str) -> Generator[str, None, None]:
        """
        Stream the agent's reasoning token by token.
        For the Streamlit UI — shows thinking in real time.
        """
        # Run full response first, then yield
        response, _ = self.run(user_message)
        for word in response.split():
            yield word + " "
            time.sleep(0.02)


# Singleton
_agent: NeuroScaleOpsAgent | None = None

def get_agent() -> NeuroScaleOpsAgent:
    global _agent
    if _agent is None:
        _agent = NeuroScaleOpsAgent()
    return _agent
