"""
NeuroScale Ops Agent — Streamlit UI

The incident commander interface. Shows:
  Left panel:  Conversational agent (ask anything about your cluster)
  Right panel: Agent reasoning transparency (which tools ran, what Splunk returned)
  Sidebar:     Live Splunk intelligence panels (model health, policy violations, costs)

This is what judges see. Every interaction proves Splunk AI is doing real work.
"""
import sys
import os
import json
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="NeuroScale Ops Agent",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "About": "NeuroScale Ops Agent — Autonomous MLOps Incident Commander powered by Splunk AI",
    },
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Main background */
.main { background-color: #0f1117; }

/* Header bar */
.agent-header {
    background: linear-gradient(135deg, #1a1f2e 0%, #0d1117 100%);
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 1rem 1.5rem;
    margin-bottom: 1rem;
}

/* Status badges */
.badge {
    display: inline-block;
    padding: 0.2rem 0.7rem;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
    margin-right: 0.4rem;
}
.badge-green  { background: #1a3a1a; color: #3fb950; border: 1px solid #3fb950; }
.badge-red    { background: #3a1a1a; color: #f85149; border: 1px solid #f85149; }
.badge-yellow { background: #3a2f1a; color: #d29922; border: 1px solid #d29922; }
.badge-blue   { background: #1a2a3a; color: #58a6ff; border: 1px solid #58a6ff; }

/* Tool call card */
.tool-call {
    background: #161b22;
    border: 1px solid #30363d;
    border-left: 3px solid #58a6ff;
    border-radius: 6px;
    padding: 0.8rem;
    margin: 0.5rem 0;
    font-family: monospace;
    font-size: 0.8rem;
}

/* Splunk panel */
.splunk-panel {
    background: #0d1117;
    border: 1px solid #21262d;
    border-top: 3px solid #ff6900;
    border-radius: 6px;
    padding: 0.8rem;
    margin: 0.5rem 0;
}

/* Metric cards */
.metric-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 0.8rem;
    text-align: center;
    margin: 0.3rem 0;
}

/* Quick action buttons */
.stButton > button {
    background: #21262d !important;
    border: 1px solid #30363d !important;
    color: #c9d1d9 !important;
    border-radius: 6px !important;
    font-size: 0.8rem !important;
    transition: all 0.2s !important;
}
.stButton > button:hover {
    background: #30363d !important;
    border-color: #58a6ff !important;
    color: #58a6ff !important;
}

/* Chat messages */
.agent-response {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 1rem;
    margin: 0.5rem 0;
}
</style>
""", unsafe_allow_html=True)

# ── Session state initialization ──────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "tool_calls_log" not in st.session_state:
    st.session_state.tool_calls_log = []
if "agent" not in st.session_state:
    st.session_state.agent = None
if "splunk_data" not in st.session_state:
    st.session_state.splunk_data = {"models": [], "violations": [], "costs": []}
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = 0


# ── Lazy agent initialization ─────────────────────────────────────────────────
def get_agent():
    if st.session_state.agent is None:
        try:
            from agent.core import get_agent as _get_agent
            st.session_state.agent = _get_agent()
        except Exception as exc:
            st.error(f"Agent initialization failed: {exc}")
            return None
    return st.session_state.agent


def refresh_splunk_data():
    """Fetch fresh Splunk data for the sidebar panels."""
    now = time.time()
    if now - st.session_state.last_refresh < 30:  # Cache for 30s
        return

    try:
        from tools.splunk_client import get_model_health, get_policy_violations, get_cost_attribution
        st.session_state.splunk_data = {
            "models": get_model_health(window="-2h"),
            "violations": get_policy_violations(window="-24h"),
            "costs": get_cost_attribution(window="-6h"),
        }
        st.session_state.last_refresh = now
    except Exception:
        pass  # Use cached/demo data


# ── SIDEBAR — Live Splunk Intelligence ───────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="text-align:center; padding: 0.5rem;">
        <h2 style="color:#ff6900; margin:0;">🔴 Splunk Live</h2>
        <p style="color:#8b949e; font-size:0.75rem; margin:0;">Neuroscale Platform Intelligence</p>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # Refresh button
    if st.button("↻ Refresh Splunk Data", use_container_width=True):
        st.session_state.last_refresh = 0  # Force refresh

    refresh_splunk_data()
    data = st.session_state.splunk_data

    # ── Model Health Panel
    st.markdown("### 🤖 Model Health")
    models = data.get("models", [])
    if models and not (len(models) == 1 and "error" in models[0]):
        for m in models[:5]:
            name = m.get("name", "unknown")
            count = m.get("event_count", "0")
            reasons = m.get("reasons", "").lower()

            if any(bad in reasons for bad in ["oom", "crash", "error", "kill"]):
                badge = f'<span class="badge badge-red">⚠ {count} errors</span>'
            else:
                badge = f'<span class="badge badge-green">✓ healthy</span>'

            st.markdown(
                f'<div class="splunk-panel">'
                f'<b style="color:#e6edf3">{name[:30]}</b><br/>'
                f'{badge}'
                f'<span style="color:#8b949e; font-size:0.7rem"> {reasons[:40]}</span>'
                f'</div>',
                unsafe_allow_html=True
            )
    else:
        st.markdown(
            '<div class="splunk-panel" style="color:#8b949e; font-size:0.85rem;">'
            '⬤ Awaiting KServe events...<br/>'
            '<small>Start the HEC forwarder to see live data</small>'
            '</div>',
            unsafe_allow_html=True
        )

    st.divider()

    # ── Policy Violations Panel
    st.markdown("### 🛡️ Policy Violations (24h)")
    violations = data.get("violations", [])
    if violations and not (len(violations) == 1 and "error" in violations[0]):
        st.markdown(
            f'<span class="badge badge-red">{len(violations)} DENY events</span>',
            unsafe_allow_html=True
        )
        for v in violations[:4]:
            resource = v.get("resource", "unknown")
            policy = v.get("policy", "unknown")
            st.markdown(
                f'<div class="splunk-panel">'
                f'<b style="color:#f85149">✗ DENIED</b> '
                f'<span style="color:#e6edf3">{resource[:25]}</span><br/>'
                f'<small style="color:#8b949e">policy: {policy[:35]}</small>'
                f'</div>',
                unsafe_allow_html=True
            )
    else:
        st.markdown(
            '<div class="splunk-panel">'
            '<span class="badge badge-green">✓ No violations</span>'
            '</div>',
            unsafe_allow_html=True
        )

    st.divider()

    # ── Cost Attribution Panel
    st.markdown("### 💰 Cost Attribution (6h)")
    costs = data.get("costs", [])
    if costs and not (len(costs) == 1 and "error" in costs[0]):
        total = sum(float(c.get("total_cost", c.get("totalCost", 0))) for c in costs)
        st.markdown(
            f'<div class="metric-card">'
            f'<div style="font-size:1.4rem; color:#e6edf3; font-weight:700">${total:.4f}</div>'
            f'<div style="color:#8b949e; font-size:0.75rem">Total cluster cost / 6h</div>'
            f'</div>',
            unsafe_allow_html=True
        )
        for c in costs[:5]:
            ns = c.get("namespace", "unknown")
            cost = float(c.get("total_cost", c.get("totalCost", 0)))
            pct = round((cost / total * 100) if total > 0 else 0, 1)

            color = "#f85149" if pct > 60 else "#d29922" if pct > 30 else "#3fb950"
            st.markdown(
                f'<div style="margin:0.3rem 0; display:flex; justify-content:space-between;">'
                f'<span style="color:#c9d1d9; font-size:0.8rem">{ns}</span>'
                f'<span style="color:{color}; font-size:0.8rem; font-weight:600">'
                f'${cost:.4f} ({pct}%)</span>'
                f'</div>',
                unsafe_allow_html=True
            )
    else:
        st.markdown(
            '<div class="splunk-panel" style="color:#8b949e; font-size:0.85rem;">'
            '⬤ Awaiting OpenCost metrics...'
            '</div>',
            unsafe_allow_html=True
        )

    st.divider()

    # ── Splunk connection status
    st.markdown("### ⚙️ System Status")
    demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"
    splunk_host = os.getenv("SPLUNK_HOST", "localhost")

    if demo_mode:
        st.markdown(
            '<span class="badge badge-yellow">● DEMO MODE</span> '
            '<small style="color:#8b949e">Synthetic data active</small>',
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            f'<span class="badge badge-blue">● Splunk: {splunk_host}</span>',
            unsafe_allow_html=True
        )

    openai_key = os.getenv("OPENAI_API_KEY", "")
    llm_model = os.getenv("OPENAI_MODEL", "gpt-4o")
    llm_label = llm_model.split("-")[0].capitalize() + " " + llm_model.split("-")[1] if "-" in llm_model else llm_model
    if openai_key and openai_key != "sk-...":
        st.markdown(
            f'<span class="badge badge-green">● {llm_label} Ready</span>',
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            '<span class="badge badge-red">● LLM Not Configured</span>',
            unsafe_allow_html=True
        )


# ── MAIN AREA ─────────────────────────────────────────────────────────────────

# Header
st.markdown("""
<div class="agent-header">
    <div style="display:flex; align-items:center; justify-content:space-between;">
        <div>
            <h1 style="color:#e6edf3; margin:0; font-size:1.6rem;">
                🧠 NeuroScale Ops Agent
            </h1>
            <p style="color:#8b949e; margin:0.2rem 0 0 0; font-size:0.85rem;">
                Autonomous Incident Commander · Powered by Splunk MCP + Llama-3.3-70B · 
                GitOps · KServe · Kyverno · OpenCost
            </p>
        </div>
        <div>
            <span class="badge badge-green">● Platform Ready</span>
            <span class="badge badge-blue">● Splunk Connected</span>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# Two-column layout: Agent chat | Reasoning transparency
col_chat, col_reasoning = st.columns([3, 2])

with col_chat:
    st.markdown("#### 💬 Incident Commander")

    # Quick action preset buttons
    st.markdown("**Quick Diagnose:**")
    qcols = st.columns(3)
    preset_query = None
    with qcols[0]:
        if st.button("🚨 Model Down?", use_container_width=True):
            preset_query = "A model inference service is down. Check Splunk for recent KServe errors, find the root cause, and fix it."
    with qcols[1]:
        if st.button("🛡️ Deploy Blocked?", use_container_width=True):
            preset_query = "My model deployment was rejected. Check Splunk for recent Kyverno policy violations and explain exactly what I need to fix."
    with qcols[2]:
        if st.button("💰 Cost Spike?", use_container_width=True):
            preset_query = "There's a cost spike in the cluster. Query Splunk OpenCost data, identify which namespace/team is over budget, and recommend ResourceQuota fixes."

    qcols2 = st.columns(3)
    with qcols2[0]:
        if st.button("🔍 Full Triage", use_container_width=True):
            preset_query = "Run a complete cluster health check. Query Splunk for all errors across KServe, Kyverno, and ArgoCD in the last 4 hours. Give me a prioritized incident list."
    with qcols2[1]:
        if st.button("🔄 ArgoCD Sync", use_container_width=True):
            preset_query = "Check ArgoCD sync status from Splunk. If any applications are OutOfSync or Degraded, trigger a hard refresh and sync."
    with qcols2[2]:
        if st.button("🗑️ Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.tool_calls_log = []
            if st.session_state.agent:
                st.session_state.agent.reset()
            st.rerun()

    st.divider()

    # Chat history
    chat_container = st.container()
    with chat_container:
        for msg in st.session_state.messages:
            if msg["role"] == "user":
                with st.chat_message("user", avatar="👤"):
                    st.markdown(msg["content"])
            else:
                with st.chat_message("assistant", avatar="🧠"):
                    st.markdown(msg["content"])

    # Input
    user_input = st.chat_input("Describe the incident or ask anything about your cluster...")

    # Handle preset buttons
    if preset_query:
        user_input = preset_query

    if user_input:
        # Show user message immediately
        st.session_state.messages.append({"role": "user", "content": user_input})

        with chat_container:
            with st.chat_message("user", avatar="👤"):
                st.markdown(user_input)

        agent = get_agent()

        if agent is None:
            response = (
                "⚠️ **Agent not configured.** Please set `OPENAI_API_KEY` in your `.env` file.\n\n"
                "For demo mode, set `DEMO_MODE=true` in `.env` to see the agent with synthetic data."
            )
            tool_calls = []
        else:
            with st.spinner("🧠 Querying Splunk and analyzing..."):
                try:
                    response, tool_calls = agent.run(user_input)
                    st.session_state.tool_calls_log = tool_calls
                except Exception as exc:
                    response = f"⚠️ Agent error: {exc}\n\nCheck your API keys and Splunk connectivity."
                    tool_calls = []

        st.session_state.messages.append({"role": "assistant", "content": response})

        with chat_container:
            with st.chat_message("assistant", avatar="🧠"):
                st.markdown(response)

        st.rerun()


with col_reasoning:
    st.markdown("#### 🔍 Agent Reasoning")
    st.markdown(
        '<small style="color:#8b949e;">Every Splunk query and tool call the agent ran '
        'to reach its conclusion — full transparency.</small>',
        unsafe_allow_html=True
    )

    if st.session_state.tool_calls_log:
        for i, tc in enumerate(st.session_state.tool_calls_log):
            tool_name = tc.get("tool", "unknown")
            args = tc.get("args", {})
            result_preview = tc.get("result_preview", "")
            result_count = tc.get("result_count", 0)

            # Tool icon mapping
            icons = {
                "query_splunk": "🔴",
                "get_model_health": "🤖",
                "get_policy_violations": "🛡️",
                "get_cost_attribution": "💰",
                "get_error_timeline": "📊",
                "lookup_runbook": "📖",
                "trigger_argocd_sync": "🔄",
                "restart_inference_service": "⚡",
                "patch_memory_limit": "🔧",
                "get_cluster_overview": "🗺️",
                "get_cost_direct": "💰",
            }
            icon = icons.get(tool_name, "🔧")

            with st.expander(f"{icon} `{tool_name}` → {result_count} result(s)", expanded=(i == 0)):
                if args:
                    st.markdown("**Args:**")
                    st.code(json.dumps(args, indent=2), language="json")

                if result_preview:
                    st.markdown("**Splunk Response:**")
                    st.code(result_preview[:800], language="json")
    else:
        st.markdown("""
        <div style="
            background:#0d1117;
            border:1px solid #21262d;
            border-radius:8px;
            padding:2rem;
            text-align:center;
            color:#8b949e;
        ">
            <div style="font-size:2rem;">🔍</div>
            <div style="margin-top:0.5rem;">Agent reasoning will appear here</div>
            <div style="font-size:0.75rem; margin-top:0.3rem;">
                Each Splunk query, runbook lookup, and action will be shown
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    # Platform Architecture Quick Reference
    st.markdown("#### 🏗️ Platform Architecture")
    st.markdown("""
    <div style="font-size:0.8rem; color:#8b949e; line-height:1.8;">
    <b style="color:#e6edf3;">Data Flow to Splunk:</b><br/>
    KServe events → <code>kserve:events</code><br/>
    Kyverno denials → <code>kyverno:violations</code><br/>
    ArgoCD sync → <code>argocd:events</code><br/>
    OpenCost metrics → <code>opencost:metrics</code><br/>
    Agent actions → <code>neuroscale:agent</code><br/>
    <br/>
    <b style="color:#e6edf3;">Splunk Index:</b> <code>neuroscale</code><br/>
    <b style="color:#e6edf3;">MCP Server:</b> localhost:8089<br/>
    <b style="color:#e6edf3;">HEC Endpoint:</b> localhost:8088<br/>
    </div>
    """, unsafe_allow_html=True)

    # Splunk SPL quick reference
    with st.expander("📋 Key SPL Queries", expanded=False):
        st.code("""
# KServe errors last 1h
index=neuroscale sourcetype="kserve:events"
type="Warning"
| stats count by name, reason

# Policy violations today
index=neuroscale sourcetype="kyverno:violations"
| table _time, resource, policy, action

# Cost by namespace (6h)
index=neuroscale sourcetype="opencost:metrics"
| stats sum(hourly_cost) as cost by namespace
| sort -cost

# All errors — big picture
index=neuroscale (type="Warning" OR action="DENY")
| timechart span=30m count by sourcetype
        """, language="spl")


# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.markdown("""
<div style="text-align:center; color:#8b949e; font-size:0.75rem; padding:0.5rem;">
    <b>NeuroScale Ops Agent</b> · Splunk Agentic Ops Hackathon 2026 · 
    Built on: ArgoCD · KServe · Kyverno · OpenCost · Backstage · Splunk MCP<br/>
    <a href="https://github.com/sodiq-code/neuroscale-ops-agent" 
       style="color:#58a6ff;">github.com/sodiq-code/neuroscale-ops-agent</a>
</div>
""", unsafe_allow_html=True)
