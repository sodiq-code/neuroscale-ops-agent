# NeuroScale Ops Agent

> **Autonomous AI-powered operations for Kubernetes ML platforms, built on Splunk.**

[![CI](https://github.com/sodiq-code/neuroscale-ops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/sodiq-code/neuroscale-ops-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![Splunk](https://img.shields.io/badge/Splunk-MCP%20%2B%20HEC-FF5733)](https://splunk.com)
[![Built for](https://img.shields.io/badge/Hackathon-Splunk%20Agentic%20Ops%202026-blueviolet)](https://splunk.devpost.com)
[![Track](https://img.shields.io/badge/Track-Platform%20%26%20Developer%20Experience-green)](https://splunk.devpost.com)
[![Bonus](https://img.shields.io/badge/Bonus-Best%20Use%20of%20Splunk%20MCP%20Server-orange)](https://splunk.devpost.com)
[![Bonus](https://img.shields.io/badge/Bonus-Best%20Use%20of%20Splunk%20Developer%20Tools-orange)](https://splunk.devpost.com)
[![Bonus](https://img.shields.io/badge/Bonus-Best%20Use%20of%20Splunk%20Hosted%20Models-orange)](https://splunk.devpost.com)

---

## What This Is

**NeuroScale Ops Agent** is a GPT-4o-powered autonomous agent that monitors, detects, and self-heals a Kubernetes-based machine learning platform — using **Splunk as its single pane of glass**.

**Hackathon Track:** Platform & Developer Experience  
**Bonus Targets:** Best Use of Splunk MCP Server · Best Use of Splunk Hosted Models · Best Use of Splunk Developer Tools

It extends [NeuroScale Platform](https://github.com/sodiq-code/neuroscale-platform) (ArgoCD + KServe + Kyverno + OpenCost + Backstage) with:

- **Real-time telemetry ingestion** → K8s events stream into Splunk via HEC (4 concurrent threads)
- **SPL-powered anomaly detection** → Splunk alerts fire on model failures, cost spikes, and policy violations
- **MCP-connected reasoning** → Agent queries live Splunk data via Model Context Protocol
- **Runbook RAG** → Every action is grounded in documented runbooks, not hallucination
- **Autonomous remediation** → 3 self-healing workflows execute without human intervention
- **Operator dashboard** → Streamlit UI with reasoning transparency and manual override

---

## Demo

> **Watch the demo:** *(Upload to YouTube after recording — see [`docs/DEMO_GUIDE.md`](docs/DEMO_GUIDE.md) for the script)*

Run it yourself in 2 minutes — no cluster or Splunk required:

```bash
git clone https://github.com/sodiq-code/neuroscale-ops-agent
cd neuroscale-ops-agent
bash scripts/setup.sh
DEMO_MODE=true streamlit run ui/app.py
```

Open `http://localhost:8501` and ask the agent anything.

---

## Architecture

```
K8s Cluster (k3d)
  ├── KServe (model inference)
  ├── Kyverno (policy engine)      ──► splunk-integration/k8s_to_splunk.py
  ├── OpenCost (cost monitoring)        (4 threads, 30s interval, HEC)
  └── ArgoCD (GitOps)                        │
                                        Splunk Index: neuroscale
                                        ├── Alerts (SPL thresholds)
                                        └── MCP Server ──► agent/core.py (GPT-4o)
                                                                │
                                                    ┌───────────┼───────────┐
                                                    ▼           ▼           ▼
                                              runbook_rag  splunk_client  k8s_ops
                                                    │           │           │
                                                    └──── workflows/ ───────┘
                                                              │
                                                        ui/app.py (Streamlit)
```

See [`architecture_diagram.md`](architecture_diagram.md) for the full Mermaid diagram with component details.

---

## Splunk AI Capabilities Used

This project leverages the following Splunk AI capabilities from [dev.splunk.com](https://dev.splunk.com):

| Capability | Implementation | Prize Target |
|-----------|----------------|-------------|
| **[Splunk MCP Server](https://dev.splunk.com/enterprise/docs/devtools/mcp/)** | Agent queries live Splunk data mid-reasoning via Model Context Protocol | Best Use of MCP Server ($1K) |
| **[Splunk Hosted Models](https://www.splunk.com/en_us/products/ai-toolkit.html)** | `tools/splunk_hosted_models.py` — Foundation-Sec (security triage), Cisco Deep Time Series (forecasting), GPT-OSS-120B (SPL generation) via `\| ai` SPL command | Best Use of Hosted Models ($1K) |
| **[Python SDK for AI](https://dev.splunk.com/enterprise/docs/devtools/python/)** | `tools/splunk_client.py` — SDK-based REST queries, index management, SPL execution | Best Use of Developer Tools ($1K) |
| **[HEC Ingestion](https://docs.splunk.com/Documentation/Splunk/latest/Data/UsetheHTTPEventCollector)** | `splunk-integration/k8s_to_splunk.py` — 4 threads, structured JSON events | Core capability |
| **SPL Queries** | Model health, cost breakdown, policy violations, ArgoCD status | Core capability |
| **Alert Webhooks** | `splunk-integration/alert-actions/trigger_agent.py` — Splunk alert fires → agent acts | AI for Splunk Apps pattern |
| **Token Auth** | HEC token + REST API token (no OAuth — Controlled Availability per hackathon rules) | Compliant |
| **Custom Index** | `neuroscale` index with 4 sourcetypes: `neuroscale:models`, `neuroscale:costs`, `neuroscale:policies`, `neuroscale:argocd` | Core capability |

---

## Agent Capabilities (11 Tools)

| Tool | What It Does |
|------|-------------|
| `splunk_query` | Run arbitrary SPL against the neuroscale index |
| `get_model_status` | Query KServe inference service health via Splunk |
| `get_cost_breakdown` | Per-namespace hourly cost from OpenCost→Splunk |
| `check_policy_violations` | Kyverno blocks and warnings from Splunk |
| `get_argocd_status` | GitOps sync status from Splunk |
| `get_runbook_guidance` | RAG retrieval from the platform runbook |
| `restart_inference_service` | kubectl rollout restart on KServe |
| `trigger_argocd_sync` | Force-sync an ArgoCD application |
| `scale_deployment` | kubectl scale on any namespace/deployment |
| `get_cluster_events` | Recent K8s events for any namespace |
| `send_splunk_event` | Write agent actions back to Splunk for audit trail |

---

## Self-Healing Workflows

### 1. Model Down (`workflows/model_down.py`)
**Trigger:** KServe model error rate > 5% for 5 minutes  
**Steps:**
1. Pull model telemetry from Splunk
2. Check ArgoCD sync status
3. Retrieve runbook steps via RAG
4. Restart InferenceService
5. Poll until healthy (5 retries × 30s)
6. Write resolution event to Splunk

### 2. Policy Violation (`workflows/policy_violation.py`)
**Trigger:** Kyverno BLOCK action in Splunk  
**Steps:**
1. Identify the violating resource and policy
2. Retrieve compliance runbook
3. Annotate resource for review
4. Trigger ArgoCD sync to restore desired state
5. Verify no new violations within 60s

### 3. Cost Spike (`workflows/cost_spike.py`)
**Trigger:** Hourly namespace cost > $50 threshold  
**Steps:**
1. Pull OpenCost breakdown from Splunk
2. Identify highest-spend namespace
3. Check if workloads are over-provisioned
4. Scale down replica count (with user approval prompt)
5. Project new cost and log to Splunk

---

## Quick Start

### Prerequisites

- Python 3.11+
- `kubectl` configured (or use `DEMO_MODE=true`)
- Splunk instance with HEC enabled (or use `DEMO_MODE=true`)
- OpenAI API key

### 1. Clone and install

```bash
git clone https://github.com/sodiq-code/neuroscale-ops-agent
cd neuroscale-ops-agent
bash scripts/setup.sh
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your keys
```

Key variables:

```env
OPENAI_API_KEY=sk-...
SPLUNK_HOST=localhost
SPLUNK_HEC_TOKEN=your-hec-token
DEMO_MODE=false   # set true to skip live infrastructure
```

### 3. Start the data forwarder

```bash
# Streams K8s events into Splunk every 30s
python3 splunk-integration/k8s_to_splunk.py
```

### 4. Run the agent UI

```bash
streamlit run ui/app.py
# → http://localhost:8501
```

### Demo mode (no infrastructure)

```bash
DEMO_MODE=true streamlit run ui/app.py
```

All cluster and Splunk calls return realistic synthetic data. Full agent reasoning still fires.

---

## Splunk Setup

See [`docs/SPLUNK_SETUP.md`](docs/SPLUNK_SETUP.md) for:
- Docker-based Splunk in 2 minutes
- HEC token creation (UI + CLI)
- MCP server configuration
- Alert action webhook setup

---

## Repository Structure

```
neuroscale-ops-agent/
├── agent/
│   └── core.py                          # GPT-4o function-calling loop (14 tools)
├── tools/
│   ├── splunk_client.py                 # Splunk HEC + SDK + SPL query engine
│   ├── runbook_rag.py                   # Keyword RAG over runbook.md
│   └── kubernetes_ops.py                # kubectl / ArgoCD / KServe operations
├── workflows/
│   ├── model_down.py                    # Model failure → auto-recovery
│   ├── policy_violation.py              # Kyverno violation → remediation
│   └── cost_spike.py                    # Cost spike → scale-down
├── splunk-integration/
│   ├── k8s_to_splunk.py                 # 4-thread real-time K8s→Splunk forwarder
│   └── alert-actions/
│       └── trigger_agent.py             # Splunk alert webhook handler
├── ui/
│   └── app.py                           # Streamlit operator dashboard
├── docs/
│   ├── runbook.md                       # Source runbook (from neuroscale-platform)
│   ├── SPLUNK_SETUP.md                  # Splunk Docker + HEC + MCP setup
│   └── DEMO_GUIDE.md                    # Hackathon demo video script
├── scripts/
│   ├── setup.sh                         # One-command setup
│   └── smoke-test-extended.sh           # Full connectivity smoke test
├── k8s-manifests/                       # Inherited from neuroscale-platform
├── .github/workflows/ci.yml             # Lint + import smoke test CI
├── .env.example                         # Environment variable template
├── requirements.txt                     # Python dependencies
├── architecture_diagram.md              # Mermaid architecture diagram
└── LICENSE                              # MIT
```

---

## From NeuroScale Platform → Ops Agent

This project builds directly on [NeuroScale Platform](https://github.com/sodiq-code/neuroscale-platform) which includes: ArgoCD GitOps, KServe model serving, Kyverno policy enforcement, OpenCost monitoring, Backstage developer portal, and k3d local clusters.

**What's new in this project:**

| Component | Added |
|-----------|-------|
| `splunk-integration/` | New — real-time K8s→Splunk pipeline |
| `agent/` | New — GPT-4o agentic reasoning layer |
| `tools/splunk_client.py` | New — Splunk SDK + HEC + MCP client |
| `tools/runbook_rag.py` | New — RAG over existing runbook |
| `tools/kubernetes_ops.py` | New — programmatic cluster operations |
| `workflows/` | New — 3 autonomous remediation workflows |
| `ui/` | New — Streamlit operator dashboard |
| `docs/runbook.md` | Imported from source repo |

---

## Smoke Test

```bash
# Full test (requires Splunk + env vars)
bash scripts/smoke-test-extended.sh

# Demo mode (no infrastructure needed)
bash scripts/smoke-test-extended.sh --demo
```

Checks: Python imports, file integrity, syntax, Splunk HEC connectivity, agent module loads, runbook RAG, workflow imports, README completeness, MIT license.

---

## Hackathon Context

Built for the **Splunk Agentic Ops Hackathon 2026**.

- **Target tracks:** Platform & Dev Experience + MCP Bonus
- **Key differentiators:**
  - Real existing platform (not toy demo) extended with Splunk
  - MCP-connected agent with full function-calling reasoning
  - Demo mode works offline — zero friction for judges
  - 4 Splunk sourcetypes, 11 agent tools, 3 self-healing workflows
  - Runbook RAG grounds every action in documented procedures

---

## License

MIT — see [LICENSE](LICENSE).

---

## Author

**Sodiq Jimoh** — DevOps & Cloud Engineer  
[GitHub](https://github.com/sodiq-code) · [LinkedIn](https://linkedin.com/in/sodiq-jimoh-afsod)
