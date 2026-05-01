"""Fallback pipeline tests — alignment-enforced paths.

Coverage targets (per file × 4 models):
  - Empty / whitespace topic normalization    → line 165-166 each file
  - Each alignment gate failure (8 gates)     → restarts += 1 / continue blocks
  - MAX_RESTARTS exhausted                    → final error return
"""
from unittest.mock import patch

import pytest

from app.prompts.fallback import get_fallback

_MODELS = ("chatgpt", "claude", "gemini", "grok")

_MODULE = {
    "chatgpt": "app.prompts.fallback.chatgpt",
    "claude":  "app.prompts.fallback.claude",
    "gemini":  "app.prompts.fallback.gemini",
    "grok":    "app.prompts.fallback.grok",
}

# Alignment gates present in every model module.
# Each name maps to the attribute in the module that generate_fallback calls.
_ALIGN_GATES = [
    "align_intent",
    "align_structure",
    "align_validation",
    "align_judgment",
    "align_decision",
    "diagnose",
    "align_format",
    "align_examine",
]


# ─────────────────────── Empty / whitespace topic ─────────────────────────

@pytest.mark.parametrize("model", _MODELS)
def test_whitespace_topic_normalizes(model):
    """Whitespace-only input must not raise — pipeline normalizes to
    'this topic' and returns a valid prompt string."""
    result = get_fallback(model, "   ")
    assert isinstance(result, str)
    assert len(result) > 0
    assert result != "ERROR: Alignment failed after retries."


@pytest.mark.parametrize("model", _MODELS)
def test_empty_string_topic_normalizes(model):
    """Empty string triggers same normalization branch as whitespace."""
    result = get_fallback(model, "")
    assert isinstance(result, str)
    assert len(result) > 0
    assert result != "ERROR: Alignment failed after retries."


# ─────────────────────── Alignment gate failures ──────────────────────────

@pytest.mark.parametrize("model", _MODELS)
@pytest.mark.parametrize("gate", _ALIGN_GATES)
def test_alignment_gate_failure_exhausts_retries(model, gate):
    """Forcing any single alignment gate to always return False must exhaust
    MAX_RESTARTS and return the canonical error string."""
    path = f"{_MODULE[model]}.{gate}"
    with patch(path, return_value=(False, "forced-failure")):
        result = get_fallback(model, "test-topic-xyz")
    assert result == "ERROR: Alignment failed after retries."


# ─────────────────────── Error return string ──────────────────────────────

@pytest.mark.parametrize("model", _MODELS)
def test_error_return_is_string(model):
    """Exhausted pipeline must return a plain string, never raise."""
    path = f"{_MODULE[model]}.align_intent"
    with patch(path, return_value=(False, "forced")):
        result = get_fallback(model, "irrelevant")
    assert isinstance(result, str)


# ─────────────────────── Examine / output gate ────────────────────────────

@pytest.mark.parametrize("model", _MODELS)
def test_examine_failure_exhausts_retries(model):
    """Output (fruit) validation failure must exhaust retries."""
    path = f"{_MODULE[model]}.align_examine"
    with patch(path, return_value=(False, "output-failed")):
        result = get_fallback(model, "some-topic")
    assert result == "ERROR: Alignment failed after retries."


# ─────────────────────── Format gate ──────────────────────────────────────

@pytest.mark.parametrize("model", _MODELS)
def test_format_gate_failure_exhausts_retries(model):
    """Format alignment failure must exhaust retries."""
    path = f"{_MODULE[model]}.align_format"
    with patch(path, return_value=(False, "format-failed")):
        result = get_fallback(model, "format-test-topic")
    assert result == "ERROR: Alignment failed after retries."


# ─────────────────────── Diagnose gate ────────────────────────────────────

@pytest.mark.parametrize("model", _MODELS)
def test_diagnose_failure_exhausts_retries(model):
    """Diagnosis mismatch must exhaust retries."""
    path = f"{_MODULE[model]}.diagnose"
    with patch(path, return_value=(False, "diagnosis-mismatch")):
        result = get_fallback(model, "diagnose-test-topic")
    assert result == "ERROR: Alignment failed after retries."


# ─────────────────────── Happy path sanity ────────────────────────────────

@pytest.mark.parametrize("model", _MODELS)
def test_happy_path_returns_non_empty_string(model):
    """Unpatched pipeline must return a non-empty string containing topic."""
    topic = "neural-networks-abc"
    result = get_fallback(model, topic)
    assert isinstance(result, str)
    assert len(result) > 0
    assert topic in result
