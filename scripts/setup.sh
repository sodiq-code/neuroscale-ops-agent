#!/usr/bin/env bash
# ============================================================
# NeuroScale Ops Agent — One-Command Setup
# Usage: bash scripts/setup.sh
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${BLUE}[setup]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   NeuroScale Ops Agent — Setup Script    ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── 1. Python version check ──────────────────────────────────
info "Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
  error "Python 3.10+ required, found $PYTHON_VERSION"
fi
success "Python $PYTHON_VERSION OK"

# ── 2. Install dependencies ───────────────────────────────────
info "Installing Python dependencies..."
pip install -r requirements.txt --quiet 2>&1 | tail -5 || \
  pip install -r requirements.txt --break-system-packages --quiet 2>&1 | tail -5
success "Dependencies installed"

# ── 3. Environment file ───────────────────────────────────────
info "Setting up environment file..."
if [ ! -f .env ]; then
  cp .env.example .env
  success "Created .env from .env.example"
  warn "Fill in your API keys in .env before running!"
else
  warn ".env already exists — skipping copy"
fi

# ── 4. Validate required env vars ─────────────────────────────
info "Checking environment variables..."
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs) 2>/dev/null || true
fi

MISSING=()
[ -z "${OPENAI_API_KEY:-}" ] && MISSING+=("OPENAI_API_KEY")

if [ ${#MISSING[@]} -gt 0 ]; then
  warn "Missing required env vars: ${MISSING[*]}"
  warn "Set them in .env before running the agent"
else
  success "Required env vars present"
fi

# ── 5. Demo mode check ────────────────────────────────────────
DEMO_MODE="${DEMO_MODE:-false}"
if [ "$DEMO_MODE" = "true" ]; then
  warn "DEMO_MODE=true — using synthetic data (no live cluster/Splunk needed)"
fi

# ── 6. Create __init__.py files ───────────────────────────────
info "Ensuring package structure..."
touch agent/__init__.py
touch tools/__init__.py
touch workflows/__init__.py
touch splunk-integration/__init__.py 2>/dev/null || true
success "Package __init__.py files created"

# ── 7. Verify syntax of core files ───────────────────────────
info "Syntax checking core files..."
SYNTAX_OK=true
for f in agent/core.py tools/splunk_client.py tools/runbook_rag.py tools/kubernetes_ops.py; do
  if [ -f "$f" ]; then
    python3 -m py_compile "$f" 2>/dev/null || { warn "Syntax error in $f"; SYNTAX_OK=false; }
  fi
done
$SYNTAX_OK && success "All core files pass syntax check"

# ── 8. Quick smoke test ───────────────────────────────────────
info "Running quick import smoke test..."
python3 -c "
import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('DEMO_MODE', 'true')
os.environ.setdefault('OPENAI_API_KEY', '${OPENAI_API_KEY:-sk-placeholder}')
from tools.runbook_rag import RunbookRAG
from tools.kubernetes_ops import KubernetesOps
from tools.splunk_client import SplunkClient
print('All core modules import OK')
" && success "Core modules import successfully" || warn "Import test failed — check dependencies"

# ── Done ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           Setup Complete! 🚀              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo "  Next steps:"
echo "  1. Edit .env with your API keys"
echo "  2. Start Splunk: docs/SPLUNK_SETUP.md"
echo "  3. Run the UI:   streamlit run ui/app.py"
echo "  4. Or demo mode: DEMO_MODE=true streamlit run ui/app.py"
echo ""
echo "  Smoke test: bash scripts/smoke-test-extended.sh --demo"
echo ""
