"""Vaine Strengthener (Phase 5) — deterministic weak->strong substitution.

No LLM, no new words. v1 substitutes ADJECTIVE keys only (grammar-safe 1:1) and
strips filler; verbs AND quantifier phrases (e.g. "a lot") are deferred to v2
(they need conjugation/context). The `pos` field in the base lexicon gates which
keys apply — no NLP dependency.

Guarantee: tokens(out) is a subset of tokens(in) union the applied strong values
(the hard determinism gate).
"""
from __future__ import annotations

import re

_TOK = re.compile(r"[a-z']+")


def _case(src: str, repl: str) -> str:
    """Preserve the source token's case on the replacement."""
    if src.isupper():
        return repl.upper()
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def strengthen(text: str, profile: dict, lexicon: dict) -> str:
    """Apply the rule profile's strength SET (adjectives/phrases) + strip filler."""
    strength = profile.get("strength_set", {})
    strip = {w.lower() for w in profile.get("strip_only", [])}
    pos = {e["base_term"].lower(): e["pos"] for e in lexicon.get("entries", [])}
    # v1 gate: substitute only adjectives; verbs, nouns, quantifier phrases pass through.
    sub = {k: v for k, v in strength.items() if pos.get(k.lower()) == "adjective"}

    out = text
    # phrases before single words (longest key first) so multi-word keys match first
    for key in sorted(list(sub) + list(strip), key=lambda k: (-len(k), k)):
        pat = re.compile(r"\b" + re.escape(key) + r"\b", re.IGNORECASE)
        if key in strip:
            out = pat.sub("", out)
        else:
            out = pat.sub(lambda m, k=key: _case(m.group(0), sub[k]), out)

    # clean up whitespace / space-before-punctuation left by strips
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out).strip()
    return out


def new_tokens(src: str, out: str, applied_values) -> set[str]:
    """Return any tokens in `out` that are not in `src` or the applied strong values."""
    allowed = set(_TOK.findall(src.lower()))
    for value in applied_values:
        allowed |= set(_TOK.findall(value.lower()))
    return set(_TOK.findall(out.lower())) - allowed
