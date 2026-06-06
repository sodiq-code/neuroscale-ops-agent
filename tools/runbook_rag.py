"""
Runbook RAG — Retrieval-Augmented Generation over docs/runbook.md.

Uses the real NeuroScale operational runbook — documented failure modes
from actual production incidents, not synthetic examples.

This gives the agent a knowledge base that no other hackathon entrant will have:
real CrashLoopBackOff recovery, Kyverno webhook disruption fixes,
KServe ingress misconfiguration root causes — all battle-tested.
"""
import re
import os
from pathlib import Path
from typing import Optional
from rich.console import Console

console = Console()

# Path to runbook relative to project root
RUNBOOK_PATH = Path(__file__).parent.parent / "docs" / "runbook.md"


class RunbookRAG:
    """Simple but effective RAG over structured runbook sections."""

    def __init__(self, runbook_path: Path = RUNBOOK_PATH):
        self.sections: dict[str, dict] = {}
        self._load(runbook_path)

    def _load(self, path: Path):
        """Parse runbook.md into sections keyed by heading."""
        if not path.exists():
            console.print(f"[yellow]Runbook not found at {path}[/yellow]")
            return

        text = path.read_text()
        # Split by level-2 headings
        raw_sections = re.split(r"\n## ", text)

        for raw in raw_sections:
            if not raw.strip():
                continue
            lines = raw.strip().split("\n")
            title = lines[0].strip().lstrip("# ")
            body = "\n".join(lines[1:]).strip()

            # Extract keywords from title + first paragraph
            keywords = self._extract_keywords(title + " " + body[:300])

            self.sections[title] = {
                "title": title,
                "body": body,
                "keywords": keywords,
                "full_text": f"## {title}\n\n{body}",
            }

        console.print(f"[green]Runbook loaded:[/green] {len(self.sections)} sections")

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract meaningful keywords for matching."""
        # Lowercase, remove punctuation, split
        words = re.sub(r"[^\w\s]", " ", text.lower()).split()
        # Filter short/common words
        stopwords = {"the", "a", "an", "is", "in", "on", "at", "to", "for",
                     "of", "and", "or", "but", "not", "with", "from", "by",
                     "this", "that", "are", "was", "were", "be", "been", "has"}
        return {w for w in words if len(w) > 3 and w not in stopwords}

    def lookup(self, symptom: str, top_k: int = 2) -> str:
        """
        Find the most relevant runbook sections for a given symptom description.

        Args:
            symptom: Natural language description of the problem
            top_k:   Number of sections to return

        Returns:
            Formatted string with top matching runbook sections
        """
        if not self.sections:
            return "Runbook unavailable. Proceed with standard Kubernetes debugging."

        symptom_keywords = self._extract_keywords(symptom)

        # Score each section by keyword overlap
        scored = []
        for title, section in self.sections.items():
            overlap = len(symptom_keywords & section["keywords"])
            # Bonus for exact phrase matches in title
            title_lower = title.lower()
            bonus = sum(2 for kw in symptom_keywords if kw in title_lower)
            scored.append((overlap + bonus, title, section))

        scored.sort(reverse=True, key=lambda x: x[0])

        # Return top-k sections
        results = []
        for score, title, section in scored[:top_k]:
            if score == 0:
                continue
            # Truncate long sections
            body_preview = section["body"][:1500]
            results.append(f"## {title}\n\n{body_preview}")

        if not results:
            return (
                "No matching runbook section found for this symptom.\n"
                "Recommend: check pod logs, describe the failing resource, "
                "and check ArgoCD application status."
            )

        return "\n\n---\n\n".join(results)

    def get_section(self, title_fragment: str) -> Optional[str]:
        """Get a specific section by partial title match."""
        fragment_lower = title_fragment.lower()
        for title, section in self.sections.items():
            if fragment_lower in title.lower():
                return section["full_text"]
        return None

    def list_sections(self) -> list[str]:
        """List all runbook section titles."""
        return list(self.sections.keys())


# Singleton instance — shared across the agent
_runbook: Optional[RunbookRAG] = None


def get_runbook() -> RunbookRAG:
    global _runbook
    if _runbook is None:
        _runbook = RunbookRAG()
    return _runbook


def lookup_runbook(symptom: str) -> str:
    """Public API: look up runbook procedures for a given symptom."""
    return get_runbook().lookup(symptom)
