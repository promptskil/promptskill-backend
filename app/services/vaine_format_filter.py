"""Vaine Format Filter (Phase 6, v2) — deterministic per-model structure pass.

Reads the model's format_template.structure and shapes the single Vaine output:
  - "xml"      (Claude): <task>{lead}</task> + <requirements> bullet list
  - "numbered" (ChatGPT/Gemini/Grok): "{lead}:" then 1. 2. 3. items
  - "plain"/absent: v1-minimal (strip scaffolding, normalize) — no structure added

STRUCTURE-ONLY: content is preserved; only sanctioned structural tokens (tag
names, list numbers) plus the (unchanged) strength substitutions may differ from
the input. Absorbs the old generate_service._strip_xml_tags.
"""
from __future__ import annotations

import re

_XML = re.compile(r"</?[a-z_][a-z0-9_]*>", re.IGNORECASE)  # <task> </task> etc.
_WORD = re.compile(r"[A-Za-z0-9']+")
# list lead-ins: "{lead}, covering a, b, and c" | "including ..." | "...: a, b, c"
_LEADIN = re.compile(
    r"^(?P<lead>.*?)(?:,?\s+(?:covering|including|such as)\s+|:\s+)(?P<tail>.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _clean(text: str) -> str:
    """v1-minimal cleanup: strip scaffolding, normalize whitespace."""
    out = _XML.sub(" ", text)
    out = re.sub(r"(?m)^\s*[#>\-\*\+]+\s*", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out.strip()


def _split(text: str) -> tuple[str, list[str]]:
    """Split '{lead}, covering a, b, and c' -> (lead, [a, b, c]); else (text, [])."""
    m = _LEADIN.match(text)
    if not m:
        return text, []
    lead = m.group("lead").strip().rstrip(",")
    tail = m.group("tail").strip().rstrip(".")
    parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", tail)
    items = [p.strip().rstrip(".") for p in parts if p.strip()]
    return (lead, items) if len(items) >= 2 else (text, [])


def format_prompt(text: str, profile: dict) -> str:
    """Apply the profile's structure (xml | numbered | plain) to the Vaine output."""
    structure = (profile.get("format_template") or {}).get("structure", "plain")
    body = _clean(text)
    if structure == "plain":
        return body
    lead, items = _split(body)
    if not items:  # no list detected -> minimal fallback
        return f"<task>{lead}</task>" if structure == "xml" else lead
    if structure == "numbered":
        lines = "\n".join(f"{i + 1}. {it}" for i, it in enumerate(items))
        return f"{lead}:\n{lines}"
    if structure == "xml":
        reqs = "\n".join(f"- {it}" for it in items)
        return f"<task>{lead}</task>\n<requirements>\n{reqs}\n</requirements>"
    return body  # unknown structure -> safe default


def content_words(s: str) -> list[str]:
    """Content tokens, for the structure-only invariant."""
    return _WORD.findall(s.lower())
