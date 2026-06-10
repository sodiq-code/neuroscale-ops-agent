#!/usr/bin/env bash
# ============================================================
# NeuroScale Ops Agent — Extended Smoke Test
# Runs original platform checks + Splunk HEC + MCP + Agent
# Usage: ./scripts/smoke-test-extended.sh [--demo]
# ============================================================

set -uo pipefail

DEMO_MODE="${1:-}"
PASS=0
FAIL=0
SKIP=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}✅ PASS${NC} — $1"; PASS=$((PASS+1)); }
fail() { echo -e "${RED}❌ FAIL${NC} — $1"; FAIL=$((FAIL+1)); }
skip() { echo -e "${YELLOW}⏭  SKIP${NC} — $1"; SKIP=$((SKIP+1)); }
section() { echo -e "\n${BLUE}══════════════════════════════════════${NC}"; echo -e "${BLUE} $1${NC}"; echo -e "${BLUE}══════════════════════════════════════${NC}"; }

# Load env
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs) 2>/dev/null || true
fi

section "1. Python Environment"

python3 -c "import openai" 2>/dev/null && pass "openai importable" || fail "openai not installed"
python3 -c "import splunklib" 2>/dev/null && pass "splunk-sdk importable" || fail "splunk-sdk not installed"
python3 -c "import streamlit" 2>/dev/null && pass "streamlit importable" || fail "streamlit not installed"
python3 -c "import kubernetes" 2>/dev/null && pass "kubernetes importable" || fail "kubernetes not installed"

section "2. Source File Integrity"

FILES=(
  "agent/core.py"
  "tools/splunk_client.py"
  "tools/runbook_rag.py"
  "tools/kubernetes_ops.py"
  "workflows/model_down.py"
  "workflows/policy_violation.py"
  "workflows/cost_spike.py"
  "splunk-integration/k8s_to_splunk.py"
  "splunk-integration/alert-actions/trigger_agent.py"
  "ui/app.py"
  ".env.example"
  "requirements.txt"
  "LICENSE"
  "README.md"
  "architecture_diagram.md"
)

for f in "${FILES[@]}"; do
  [ -f "$f" ] && pass "exists: $f" || fail "missing: $f"
done

section "3. Python Syntax Check"

PYTHON_FILES=(
  "agent/core.py"
  "tools/splunk_client.py"
  "tools/runbook_rag.py"
  "tools/kubernetes_ops.py"
  "workflows/model_down.py"
  "workflows/policy_violation.py"
  "workflows/cost_spike.py"
  "splunk-integration/k8s_to_splunk.py"
  "splunk-integration/alert-actions/trigger_agent.py"
  "ui/app.py"
)

for f in "${PYTHON_FILES[@]}"; do
  if [ -f "$f" ]; then
    python3 -m py_compile "$f" 2>/dev/null && pass "syntax OK: $f" || fail "syntax error: $f"
  fi
done

section "4. Environment Configuration"

[ -n "${OPENAI_API_KEY:-}" ] && pass "OPENAI_API_KEY set" || fail "OPENAI_API_KEY not set"
[ -n "${SPLUNK_HOST:-}" ] && pass "SPLUNK_HOST set" || skip "SPLUNK_HOST not set (demo mode OK)"
[ -n "${SPLUNK_HEC_TOKEN:-}" ] && pass "SPLUNK_HEC_TOKEN set" || skip "SPLUNK_HEC_TOKEN not set (demo mode OK)"

section "5. Splunk HEC Connectivity"

SPLUNK_HOST="${SPLUNK_HOST:-localhost}"
SPLUNK_HEC_PORT="${SPLUNK_HEC_PORT:-8088}"
SPLUNK_HEC_TOKEN="${SPLUNK_HEC_TOKEN:-}"

if [ "$DEMO_MODE" = "--demo" ]; then
  skip "Splunk HEC test skipped (--demo mode)"
elif [ -z "$SPLUNK_HEC_TOKEN" ]; then
  skip "Splunk HEC test skipped (no token)"
else
  HEC_RESPONSE=$(curl -sk -o /dev/null -w "%{http_code}" \
    -H "Authorization: Splunk $SPLUNK_HEC_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"event":{"test":"smoke-test"},"sourcetype":"neuroscale:smoke"}' \
    "https://${SPLUNK_HOST}:${SPLUNK_HEC_PORT}/services/collector/event" 2>/dev/null || echo "000")

  if [ "$HEC_RESPONSE" = "200" ]; then
    pass "Splunk HEC reachable (HTTP 200)"
  elif [ "$HEC_RESPONSE" = "000" ]; then
    fail "Splunk HEC unreachable — check SPLUNK_HOST:SPLUNK_HEC_PORT"
  else
    fail "Splunk HEC returned HTTP $HEC_RESPONSE"
  fi
fi

section "6. Splunk REST API Connectivity"

SPLUNK_PORT="${SPLUNK_PORT:-8089}"
SPLUNK_USER="${SPLUNK_USER:-admin}"
SPLUNK_PASSWORD="${SPLUNK_PASSWORD:-}"

if [ "$DEMO_MODE" = "--demo" ]; then
  skip "Splunk REST API test skipped (--demo mode)"
elif [ -z "$SPLUNK_PASSWORD" ]; then
  skip "Splunk REST API test skipped (no password)"
else
  REST_RESPONSE=$(curl -sk -o /dev/null -w "%{http_code}" \
    -u "${SPLUNK_USER}:${SPLUNK_PASSWORD}" \
    "https://${SPLUNK_HOST}:${SPLUNK_PORT}/services/search/jobs" 2>/dev/null || echo "000")

  [ "$REST_RESPONSE" = "200" ] && pass "Splunk REST API reachable" || fail "Splunk REST API HTTP $REST_RESPONSE"
fi

section "7. Agent Module Import Test"

DEMO_MODE_ENV="${DEMO_MODE:+true}"
DEMO_MODE_ENV="${DEMO_MODE_ENV:-false}"

python3 -c "
import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('DEMO_MODE', 'true')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-smoke')
try:
    from tools.runbook_rag import RunbookRAG, lookup_runbook, get_runbook
    rag = RunbookRAG()
    result = rag.lookup('model not ready')
    assert len(result) > 10
    print('RAG OK')
except Exception as e:
    print(f'RAG FAIL: {e}')
    sys.exit(1)
" 2>/dev/null && pass "RunbookRAG loads and queries" || fail "RunbookRAG import/query failed"

python3 -c "
import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('DEMO_MODE', 'true')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-smoke')
try:
    import tools.kubernetes_ops as k8s
    assert hasattr(k8s, 'get_inference_services')
    assert hasattr(k8s, 'restart_inference_service')
    print('K8sOps OK')
except Exception as e:
    print(f'K8sOps FAIL: {e}')
    sys.exit(1)
" 2>/dev/null && pass "kubernetes_ops loads in demo mode" || fail "kubernetes_ops failed"

python3 -c "
import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('DEMO_MODE', 'true')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-smoke')
try:
    import tools.splunk_client as sc
    assert hasattr(sc, 'run_spl_query')
    assert hasattr(sc, 'send_to_hec')
    print('SplunkClient OK')
except Exception as e:
    print(f'SplunkClient FAIL: {e}')
    sys.exit(1)
" 2>/dev/null && pass "splunk_client loads" || fail "splunk_client failed"

section "8. Workflow Import Test"

for wf in model_down policy_violation cost_spike; do
  python3 -c "
import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('DEMO_MODE', 'true')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-smoke')
import importlib
m = importlib.import_module('workflows.${wf}')
print('OK')
" 2>/dev/null && pass "workflow: ${wf}" || fail "workflow: ${wf}"
done

section "9. README Completeness"

if [ -f README.md ]; then
  for kw in "Quick Start" "Architecture" "Splunk" "demo" "self-healing"; do
    grep -qi "$kw" README.md && pass "README contains: $kw" || fail "README missing: $kw"
  done
else
  fail "README.md not found"
fi

section "10. License Check"

if [ -f LICENSE ]; then
  grep -qi "MIT" LICENSE && pass "MIT License present" || fail "License file exists but not MIT"
else
  fail "LICENSE file missing"
fi

# ─── Summary ─────────────────────────────────────────────────
echo ""
echo -e "${BLUE}══════════════════════════════════════${NC}"
echo -e "  Results: ${GREEN}${PASS} passed${NC}  ${RED}${FAIL} failed${NC}  ${YELLOW}${SKIP} skipped${NC}"
echo -e "${BLUE}══════════════════════════════════════${NC}"

if [ "$FAIL" -gt 0 ]; then
  echo -e "${RED}Some checks failed. Fix issues before submitting.${NC}"
  exit 1
else
  echo -e "${GREEN}All checks passed. Ready to submit!${NC}"
  exit 0
fi
