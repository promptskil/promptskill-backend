"""Vaine Format Filter (Phase 6) — deterministic structure pass.

Applies the model's format_template. v1-minimal: strip XML/markdown scaffolding and
normalize whitespace — present as one clean instruction. STRUCTURE-ONLY: no content
or word changes. Absorbs the old generate_service._strip_xml_tags. Content
restructuring (task/constraint reordering) is deferred to v2.
"""
from __future__ import annotations

import re

_XML = re.compile(r"</?[a-z_][a-z0-9_]*>", re.IGNORECASE)  # <task> </task> etc.
_WORD = re.compile(r"[A-Za-z0-9']+")


def format_prompt(text: str, profile: dict) -> str:
    """Apply the v1-minimal format: strip scaffolding, normalize whitespace."""
    out = _XML.sub(" ", text)                          # strip XML/pseudo-tags
    out = re.sub(r"(?m)^\s*[#>\-\*\+]+\s*", "", out)  # strip line-leading md
    out = re.sub(r"\n{3,}", "\n\n", out)               # collapse excess blank lines
    out = re.sub(r"[ \t]{2,}", " ", out)               # collapse runs of spaces
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)         # tighten punctuation
    return out.strip()


def content_words(s: str) -> list[str]:
    """Content tokens, for the structure-only invariant."""
    return _WORD.findall(s.lower())
