"""
Tests for neuroscale-ops-agent safety fixes:
  1. RAG margin gate (runbook_rag.py)
  2. Blast radius cap on patch_inference_service_memory (kubernetes_ops.py)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tools.runbook_rag import RunbookRAG
from tools.kubernetes_ops import patch_inference_service_memory, _parse_memory_gb, MAX_AUTO_MEMORY_GB


# ─── RAG Margin Gate Tests ────────────────────────────────────────────────────

class TestRAGMarginGate:
    """Verify the RAG lookup falls back when similarity is too low or ambiguous."""

    def setup_method(self):
        # Create an in-memory RAG with no real runbook file
        self.rag = RunbookRAG.__new__(RunbookRAG)
        self.rag.sections = {}

    def test_empty_runbook_returns_fallback(self):
        result = self.rag.lookup("crashloop backoff memory")
        assert "Runbook unavailable" in result

    def _add_sections(self, rag, titles_keywords):
        """Helper: inject fake sections into RAG."""
        for title, keywords in titles_keywords.items():
            rag.sections[title] = {
                "title": title,
                "body": f"Runbook body for {title}",
                "keywords": set(keywords),
                "full_text": f"## {title}",
            }

    def test_low_score_triggers_gate(self):
        """Score below RAG_MIN_SIMILARITY (0.65) → gate blocks, returns fallback."""
        # Only 1 keyword matches out of many query words → low normalised score
        self._add_sections(self.rag, {
            "ArgoCD CrashLoop Recovery": ["argocd"],
        })
        result = self.rag.lookup("completely unrelated zebra penguin antarctica")
        assert "Low confidence" in result or "No matching" in result

    def test_ambiguous_margin_triggers_gate(self):
        """Top-1 and top-2 scores too close → gate blocks."""
        # Both sections share the same keywords → identical scores → margin ≈ 0
        keywords = ["crashloop", "oomkill", "memory", "pod", "kubernetes"]
        self._add_sections(self.rag, {
            "Section A": keywords,
            "Section B": keywords,
        })
        result = self.rag.lookup("crashloop oomkill memory pod kubernetes restart")
        assert "Ambiguous" in result or "No matching" in result or "Low confidence" in result

    def test_high_score_clear_margin_passes(self):
        """Top-1 clearly above threshold with margin > 0.08 → returns result."""
        self._add_sections(self.rag, {
            "CrashLoop Recovery Guide": ["crashloop", "recovery", "pod", "kubernetes", "restart", "backoff"],
            "Cost Spike Analysis":      ["cost", "billing", "spend", "namespace"],
        })
        result = self.rag.lookup("crashloop recovery pod kubernetes restart backoff")
        # Should get actual content — not a low-confidence or ambiguous fallback
        assert "Low confidence" not in result and "Ambiguous" not in result
        assert "CrashLoop" in result or "## " in result


# ─── Blast Radius Tests ───────────────────────────────────────────────────────

class TestBlastRadius:
    """Verify patch_inference_service_memory refuses over-limit requests."""

    def test_parse_memory_gb_gibibytes(self):
        assert _parse_memory_gb("2Gi") == pytest.approx(2.0)
        assert _parse_memory_gb("512Mi") == pytest.approx(0.5, abs=0.01)
        assert _parse_memory_gb("1024Mi") == pytest.approx(1.0, abs=0.01)

    def test_parse_memory_gb_uppercase(self):
        assert _parse_memory_gb("4GI") == pytest.approx(4.0)

    def test_within_limit_passes(self):
        result = patch_inference_service_memory("test-svc", "default", "2Gi")
        # Should NOT be blocked
        assert result.get("blast_radius_blocked") is False
        assert result["success"] is True  # demo mode returns True

    def test_at_exact_limit_passes(self):
        result = patch_inference_service_memory("test-svc", "default", f"{MAX_AUTO_MEMORY_GB}Gi")
        assert result.get("blast_radius_blocked") is False

    def test_above_limit_blocked(self):
        result = patch_inference_service_memory("test-svc", "default", "8Gi")
        assert result["blast_radius_blocked"] is True
        assert result["success"] is False
        assert "8Gi" in result["error"]
        assert "escalate" in result["error"].lower() or "cap" in result["error"].lower() or "exceeds" in result["error"].lower()

    def test_very_large_limit_blocked(self):
        result = patch_inference_service_memory("test-svc", "default", "100Gi")
        assert result["blast_radius_blocked"] is True
        assert result["success"] is False

    def test_mi_limit_above_threshold_blocked(self):
        # 5000Mi ≈ 4.88 Gi → above 4 Gi cap
        result = patch_inference_service_memory("test-svc", "default", "5000Mi")
        assert result["blast_radius_blocked"] is True

    def test_mi_limit_within_threshold_passes(self):
        # 2048Mi = 2 Gi → within cap
        result = patch_inference_service_memory("test-svc", "default", "2048Mi")
        assert result.get("blast_radius_blocked") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
