# Demo Guide — NeuroScale Ops Agent

A step-by-step walkthrough for the hackathon demo video.
Target: 3–5 minutes. Capture everything in one take using screen recording.

---

## Setup Before Recording

```bash
# Terminal 1: Start the agent UI
cd neuroscale-ops-agent
DEMO_MODE=true streamlit run ui/app.py

# Terminal 2: Keep this ready to show logs
tail -f /tmp/neuroscale-agent.log 2>/dev/null || echo "Log file not created yet"
```

Open browser to `http://localhost:8501`

---

## Scene 1 — Introduction (0:00–0:20)

**Say:**
> "NeuroScale Ops Agent is an autonomous AI agent that monitors your Kubernetes-based ML platform
> using Splunk as the observability backbone. It detects anomalies, reasons over runbooks,
> and self-heals your cluster — without human intervention."

**Show:** The Streamlit dashboard welcome screen.

---

## Scene 2 — Architecture Overview (0:20–0:45)

**Show:** Open `architecture_diagram.md` rendered in GitHub (or VS Code preview).

**Say:**
> "Four data streams flow from the cluster into Splunk in real-time:
> model inference metrics, cost events from OpenCost, policy violations from Kyverno,
> and ArgoCD sync status. The GPT-4o agent consumes these via Splunk's MCP interface,
> consults the runbook RAG system, and executes remediation workflows."

---

## Scene 3 — Live Splunk Data (0:45–1:15)

**In the chat, type:**
```
Show me the current model health status
```

**What happens:**
1. Agent calls `splunk_query` → runs SPL against `neuroscale` index
2. Returns model status table with latency + error rates
3. Reasoning panel shows the SPL query used

**Say:**
> "The agent queries Splunk directly using SPL. Every response cites its data source."

---

## Scene 4 — Cost Analysis (1:15–1:45)

**In the chat, type:**
```
Which namespace is spending the most? Are we within budget?
```

**What happens:**
1. Agent calls `get_cost_breakdown` → queries OpenCost data forwarded to Splunk
2. Returns per-namespace cost table
3. If over threshold, recommends scale-down

**Say:**
> "OpenCost data flows into Splunk every 30 seconds. The agent can spot cost spikes
> and recommend or execute namespace scale-downs autonomously."

---

## Scene 5 — Policy Violation Detection (1:45–2:15)

**In the chat, type:**
```
Any Kyverno policy violations in the last hour?
```

**What happens:**
1. Agent calls `check_policy_violations`
2. Returns violation list with policy names and namespaces
3. Offers to trigger the remediation workflow

**Say:**
> "Kyverno policy events stream into Splunk tagged with policy name and action.
> The agent can detect blocking violations and trigger automated remediation."

---

## Scene 6 — Self-Healing Workflow (2:15–3:15)

**In the chat, type:**
```
Simulate a model outage for neuroscale-bert-classifier and auto-remediate
```

**What happens:**
1. Agent queries Splunk for model status → detects `ModelDown`
2. Calls `get_runbook_guidance` → retrieves recovery steps from the runbook
3. Triggers `model_down` workflow:
   - Checks ArgoCD sync status
   - Restarts the KServe InferenceService
   - Polls until healthy
4. Sends post-remediation Splunk event
5. Shows "Model restored" confirmation

**Say:**
> "This is the self-healing loop: detect in Splunk, reason with runbook RAG,
> act on Kubernetes, verify, report back to Splunk. No human required."

---

## Scene 7 — Runbook RAG (3:15–3:40)

**In the chat, type:**
```
Walk me through the runbook steps for a GPU OOM error
```

**What happens:**
1. Agent calls `get_runbook_guidance` with "GPU OOM"
2. Returns the exact runbook section with steps highlighted
3. Agent offers to execute the first remediation step

**Say:**
> "The RAG system retrieves the most relevant runbook section so the agent
> always acts on documented, approved procedures — not hallucination."

---

## Scene 8 — Closing (3:40–4:00)

**Show:** GitHub repo README, CI badge, architecture diagram.

**Say:**
> "NeuroScale Ops Agent extends an existing open-source MLOps platform
> with Splunk-native observability and GPT-4o autonomous reasoning.
> Everything runs in demo mode with zero infrastructure required.
> Check the README for setup instructions. Thanks."

---

## Recording Tips

- Use [OBS Studio](https://obsproject.com/) or `ffmpeg` screen capture
- Resolution: 1920×1080, 30fps
- Mic check before starting — audio quality matters
- Keep terminal font size at 18px+ for readability
- Disable notifications before recording

```bash
# Quick screen record with ffmpeg (Linux)
ffmpeg -video_size 1920x1080 -framerate 30 -f x11grab -i :0.0 \
  -f pulse -i default \
  demo-neuroscale-ops-agent.mp4
# Press Ctrl+C to stop
```

---

## What to Highlight for Judges

| Criterion | Your Proof Point |
|-----------|-----------------|
| Splunk Integration | HEC ingest + SPL queries + alert webhooks |
| MCP Usage | `splunk_client.py` MCP connection |
| Agentic Reasoning | GPT-4o function-calling, 11 tools |
| Real Use Case | MLOps platform monitoring (not toy demo) |
| Code Quality | CI/CD, syntax checks, modular architecture |
| Documentation | This guide + SPLUNK_SETUP.md + README |
