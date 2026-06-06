# Splunk Setup Guide

This guide gets Splunk running locally so the NeuroScale Ops Agent has a live telemetry backend.

---

## Option A: Splunk Free Trial (Recommended for Judges)

1. Go to [splunk.com/en_us/download](https://www.splunk.com/en_us/download/splunk-enterprise.html)
2. Register for a free trial (60-day, no credit card)
3. Download Splunk Enterprise for Linux
4. Install and start:

```bash
tar -xzf splunk-*.tgz -C /opt
/opt/splunk/bin/splunk start --accept-license --answer-yes \
  --no-prompt --seed-passwd admin123
```

5. Splunk is now at `https://localhost:8000`

---

## Option B: Docker (Fastest — 2 minutes)

```bash
docker pull splunk/splunk:latest

docker run -d \
  --name splunk-neuroscale \
  -p 8000:8000 \
  -p 8088:8088 \
  -p 8089:8089 \
  -e SPLUNK_START_ARGS="--accept-license" \
  -e SPLUNK_PASSWORD="admin123" \
  splunk/splunk:latest
```

Wait ~60 seconds for Splunk to initialize, then check:

```bash
docker logs splunk-neuroscale | grep "Ansible playbook complete"
```

---

## Configure HTTP Event Collector (HEC)

### Via UI:
1. Log in to `https://localhost:8000` (admin / admin123)
2. Settings → Data Inputs → HTTP Event Collector
3. Click **New Token**
4. Name: `neuroscale-agent`
5. Default index: `neuroscale` (create it first if needed)
6. Copy the token — this is your `SPLUNK_HEC_TOKEN`

### Via CLI:
```bash
# Create index
curl -k -u admin:admin123 \
  https://localhost:8089/services/data/indexes \
  -d name=neuroscale

# Enable HEC globally
curl -k -u admin:admin123 \
  https://localhost:8089/services/data/inputs/http/http \
  -d disabled=0

# Create HEC token
curl -k -u admin:admin123 \
  https://localhost:8089/services/data/inputs/http \
  -d name=neuroscale-agent \
  -d index=neuroscale \
  -d sourcetype=neuroscale:k8s

# Get the token value
curl -k -u admin:admin123 \
  https://localhost:8089/services/data/inputs/http/neuroscale-agent \
  | grep -o 'token>[^<]*' | sed 's/token>//'
```

---

## Configure Splunk MCP Server

The NeuroScale Ops Agent connects to Splunk via the Model Context Protocol (MCP).

### Install MCP Server:
```bash
pip install splunk-mcp  # or follow Splunk's MCP documentation
```

### Configure:
```bash
# Start MCP server (points to your Splunk instance)
splunk-mcp serve \
  --host localhost \
  --port 8089 \
  --username admin \
  --password admin123 \
  --mcp-port 7070
```

Or set these in `.env`:
```
SPLUNK_MCP_HOST=localhost
SPLUNK_MCP_PORT=7070
```

---

## Create Alert Actions (Auto-trigger the Agent)

### Model Down Alert:
```spl
index=neuroscale sourcetype="neuroscale:models"
| where status="ModelDown" OR error_rate > 0.05
| stats count by model_name
| where count > 3
```

Action: Webhook → `http://localhost:5000/webhook/alert`

### Cost Spike Alert:
```spl
index=neuroscale sourcetype="neuroscale:costs"
| stats sum(hourly_cost) as total_cost by namespace
| where total_cost > 50
```

Action: Webhook → `http://localhost:5000/webhook/alert`

### Policy Violation Alert:
```spl
index=neuroscale sourcetype="neuroscale:policies"
| where action="BLOCK"
| stats count by policy_name
```

Action: Webhook → `http://localhost:5000/webhook/alert`

---

## Update Your .env

After setup, fill in `.env`:

```bash
SPLUNK_HOST=localhost
SPLUNK_PORT=8089
SPLUNK_HEC_PORT=8088
SPLUNK_HEC_TOKEN=<your-token-from-step-above>
SPLUNK_USER=admin
SPLUNK_PASSWORD=admin123
SPLUNK_INDEX=neuroscale
SPLUNK_MCP_HOST=localhost
SPLUNK_MCP_PORT=7070
```

---

## Verify Everything Works

```bash
# Test HEC ingest
curl -k \
  -H "Authorization: Splunk $SPLUNK_HEC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"event":{"test":"hello neuroscale"},"sourcetype":"neuroscale:test"}' \
  https://localhost:8088/services/collector/event

# Expected: {"text":"Success","code":0}

# Start the K8s forwarder (begins streaming synthetic data)
DEMO_MODE=true python3 splunk-integration/k8s_to_splunk.py

# Run the full smoke test
bash scripts/smoke-test-extended.sh
```

---

## Enable Splunk Hosted Models (AI Toolkit 5.7+)

Splunk Hosted Models are native generative AI models (Foundation-Sec, Cisco DTS, GPT-OSS-20B/120B)
accessible directly inside SPL via the `| ai` command. No external API calls — inference runs
entirely within the Splunk security perimeter.

> **Requirement:** Splunk Cloud Platform + AI Toolkit app v5.7 or later.
> On-prem Splunk Enterprise → DEMO_MODE or SPL-only fallback is used automatically.

### Step 1 — Install AI Toolkit from Splunkbase

1. Log in to your Splunk Cloud instance
2. Go to **Apps → Browse More Apps** → search `AI Toolkit`
3. Install **Splunk AI Toolkit** (v5.7+), or download from:
   [splunkbase.splunk.com/app/6393](https://splunkbase.splunk.com/app/6393)
4. Restart Splunk after install

### Step 2 — Enable Hosted Models

1. In Splunk Cloud, go to **AI Toolkit → Hosted Models**
2. Accept the AI model terms of service
3. Toggle **Enable Hosted Models** → On
4. Available models will appear: `foundation-sec-1.1-8b-instruct`, `cisco-tdt`, `gpt-oss-20b`, `gpt-oss-120b`

### Step 3 — Verify the `| ai` Command Works

Run this SPL in Splunk Search to confirm hosted models are operational:

```spl
| makeresults
| eval test_event="Kubernetes pod crashed due to OOMKilled"
| ai model="gpt-oss-20b" prompt="Summarize this incident in one sentence."
```

Expected: A result row with the model's response in the `_ai_response` or `response` field.

### Step 4 — Enable in NeuroScale Agent

Set this in your `.env`:

```bash
SPLUNK_CLOUD_HOSTED_MODELS=true
```

Then restart the agent — it will now use:
- **Foundation-Sec-1.1-8B** for Kyverno policy triage (`analyze_security_events`)
- **Cisco DTS** for resource forecasting & anomaly detection (`forecast_resource_usage`)
- **GPT-OSS-20B** for incident summarization (`summarize_incident`)
- **GPT-OSS-120B** for natural-language SPL generation (`generate_spl_query`)

### Hosted Models vs DEMO_MODE

| Mode | Behaviour |
|------|-----------|
| `DEMO_MODE=true` | Synthetic AI responses — no Splunk needed |
| `SPLUNK_CLOUD_HOSTED_MODELS=false` | SPL queries run; AI inference skipped (Enterprise fallback) |
| `SPLUNK_CLOUD_HOSTED_MODELS=true` | Full `| ai` command execution on Splunk Cloud |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Connection refused :8088` | HEC not enabled — run the Enable HEC CLI command above |
| `HTTP 403 on HEC` | Token wrong or disabled — recreate via UI |
| `HTTP 400 on HEC` | Bad JSON or index doesn't exist — create `neuroscale` index |
| `splunklib.binding.HTTPError: HTTP 401` | Wrong username/password in `.env` |
| Splunk Docker container not starting | Check Docker has 4GB RAM; `docker stats splunk-neuroscale` |
