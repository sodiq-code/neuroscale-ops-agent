# NeuroScale Ops Agent — Architecture

## System Overview

```mermaid
flowchart TD
    subgraph K8S["☸️ Kubernetes Cluster (k3d)"]
        direction TB
        ARGO["ArgoCD\nGitOps Controller"]
        KSERVE["KServe\nModel Inference"]
        KYVERNO["Kyverno\nPolicy Engine"]
        OPENCOST["OpenCost\nCost Monitoring"]
        BACKSTAGE["Backstage\nDev Portal"]
        ARGO --> KSERVE
        KYVERNO --> KSERVE
        OPENCOST --> KSERVE
    end

    subgraph FORWARDER["📡 Splunk Data Pipeline"]
        direction TB
        FWD["k8s_to_splunk.py\n4-thread HEC Forwarder"]
        HEC["Splunk HEC\n:8088"]
        FWD -->|"model metrics\ncost events\npolicy violations\nargo sync events"| HEC
    end

    subgraph SPLUNK["🔍 Splunk Platform"]
        direction TB
        IDX["Index: neuroscale"]
        ALERTS["Splunk Alerts\n(SPL threshold rules)"]
        MCP["Splunk MCP Server\nModel Context Protocol"]
        IDX --> ALERTS
        IDX --> MCP
    end

    subgraph AGENT["🤖 NeuroScale Ops Agent"]
        direction TB
        CORE["agent/core.py\nGPT-4o Function Calling"]
        RAG["runbook_rag.py\nRunbook Retrieval"]
        SPLUNK_CLIENT["splunk_client.py\nSPL Query Engine"]
        K8S_OPS["kubernetes_ops.py\nCluster Operator"]
        CORE --> RAG
        CORE --> SPLUNK_CLIENT
        CORE --> K8S_OPS
    end

    subgraph WORKFLOWS["⚡ Autonomous Workflows"]
        direction TB
        WF1["model_down.py\nModel Recovery"]
        WF2["policy_violation.py\nCompliance Remediation"]
        WF3["cost_spike.py\nCost Optimization"]
    end

    subgraph UI["🖥️ Operator UI"]
        direction TB
        STREAMLIT["Streamlit Dashboard\n:8501"]
        CHAT["Chat Interface\n+ Reasoning Panel"]
        STREAMLIT --> CHAT
    end

    %% Data flow
    K8S --> FWD
    HEC --> IDX
    ALERTS -->|"webhook trigger"| AGENT
    MCP -->|"live SPL queries"| AGENT
    AGENT --> WORKFLOWS
    WORKFLOWS --> K8S_OPS
    K8S_OPS -->|"kubectl / ArgoCD API"| K8S
    AGENT --> UI
    STREAMLIT -->|"user query"| CORE

    style K8S fill:#326CE5,color:#fff,stroke:#326CE5
    style SPLUNK fill:#FF5733,color:#fff,stroke:#FF5733
    style AGENT fill:#7C3AED,color:#fff,stroke:#7C3AED
    style UI fill:#059669,color:#fff,stroke:#059669
    style FORWARDER fill:#D97706,color:#fff,stroke:#D97706
    style WORKFLOWS fill:#DC2626,color:#fff,stroke:#DC2626
```

## Data Flow Narrative

### 1. Ingestion (Real-time)
Every 30 seconds, `k8s_to_splunk.py` runs 4 concurrent threads:
- **Model Thread** → KServe inference service status, latency, error rates
- **Cost Thread** → OpenCost namespace spend, hourly cost deltas
- **Policy Thread** → Kyverno admission webhook violations
- **ArgoCD Thread** → GitOps sync status, out-of-sync applications

All events land in Splunk index `neuroscale` with structured sourcetypes.

### 2. Detection (Splunk Alerts)
SPL-based threshold alerts fire when:
- Model error rate > 5% for 5 minutes → triggers `model_down` workflow
- Hourly cost delta > $50 → triggers `cost_spike` workflow
- Policy violation count > 0 → triggers `policy_violation` workflow

Alert webhook hits `splunk-integration/alert-actions/trigger_agent.py`.

### 3. Reasoning (GPT-4o Agent)
The agent receives the alert context and:
1. Queries runbook RAG for relevant remediation steps
2. Pulls live telemetry from Splunk via SPL
3. Inspects cluster state via kubectl/ArgoCD API
4. Selects the correct workflow to execute
5. Returns structured action plan with confidence score

### 4. Remediation (Autonomous Actions)
Workflows execute in order:
- Gather evidence → Analyze root cause → Execute fix → Verify → Report

### 5. Observability (UI)
Every agent decision is visible in the Streamlit dashboard:
- Reasoning steps panel (chain-of-thought)
- Splunk query results inline
- Action log with timestamps
- Manual override controls

## Component Matrix

| Component | Tech | Purpose |
|-----------|------|---------|
| `agent/core.py` | OpenAI GPT-4o | Orchestration, function-calling |
| `tools/splunk_client.py` | Splunk SDK + HEC | Telemetry ingestion & SPL queries |
| `tools/runbook_rag.py` | Keyword RAG | Runbook retrieval for grounding |
| `tools/kubernetes_ops.py` | `kubectl` subprocess | Cluster inspection & remediation |
| `splunk-integration/` | Python threads | Real-time K8s → Splunk pipeline |
| `workflows/` | Python | Domain-specific remediation logic |
| `ui/app.py` | Streamlit | Operator-facing chat dashboard |
