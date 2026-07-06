"""Vaine Classifier (Phase 3) — simple|complex by character length.

Deterministic length check, NOT an ML model — a consequence of the frozen
class_boundary (facet-taxonomy: <=120 chars = simple; rationale: short inputs
can't carry enough detail to specify constraints).

Single source of truth: the threshold lives in facet-taxonomy.mock.json as
`class_boundary_max_chars`. `threshold_from_taxonomy()` reads it; SIMPLE_MAX_CHARS
is only a fallback default.
"""
from __future__ import annotations

SIMPLE_MAX_CHARS = 120  # fallback; canonical value lives in the taxonomy


def threshold_from_taxonomy(taxonomy: dict) -> int:
    """Read the canonical class boundary from the loaded facet taxonomy."""
    return int(taxonomy.get("class_boundary_max_chars", SIMPLE_MAX_CHARS))


def classify(topic: str, max_chars: int = SIMPLE_MAX_CHARS) -> str:
    """Return 'simple' if the trimmed topic is <= max_chars, else 'complex'."""
    return "simple" if len(topic.strip()) <= max_chars else "complex"
