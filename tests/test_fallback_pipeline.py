"""Fallback pipeline tests -- alignment-enforced paths.

Coverage targets (per file x 4 models):
  - Empty / whitespace topic normalization    -> line 165-166 each file
  - Each alignment gate failure (8 gates)     -> restarts += 1 / continue blocks
  - MAX_RESTARTS exhausted                    -> final error return
  - score_confidence gate (chatgpt only)      -> confidence < 0.7 -> restart
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


# --- Empty / whitespace topic ---

@pytest.mark.parametrize("model", _MODELS)
def test_whitespace_topic_normalizes(model):
    result = get_fallback(model, "   ")
    assert isinstance(result, str)
    assert len(result) > 0
    assert result != "ERROR: Alignment failed after retries."


@pytest.mark.parametrize("model", _MODELS)
def test_empty_string_topic_normalizes(model):
    result = get_fallback(model, "")
    assert isinstance(result, str)
    assert len(result) > 0
    assert result != "ERROR: Alignment failed after retries."


# --- Alignment gate failures ---

@pytest.mark.parametrize("model", _MODELS)
@pytest.mark.parametrize("gate", _ALIGN_GATES)
def test_alignment_gate_failure_exhausts_retries(model, gate):
    path = f"{_MODULE[model]}.{gate}"
    with patch(path, return_value=(False, "forced-failure")):
        result = get_fallback(model, "test-topic-xyz")
    assert result == "ERROR: Alignment failed after retries."


# --- Error return string ---

@pytest.mark.parametrize("model", _MODELS)
def test_error_return_is_string(model):
    path = f"{_MODULE[model]}.align_intent"
    with patch(path, return_value=(False, "forced")):
        result = get_fallback(model, "irrelevant")
    assert isinstance(result, str)


# --- Examine / output gate ---

@pytest.mark.parametrize("model", _MODELS)
def test_examine_failure_exhausts_retries(model):
    path = f"{_MODULE[model]}.align_examine"
    with patch(path, return_value=(False, "output-failed")):
        result = get_fallback(model, "some-topic")
    assert result == "ERROR: Alignment failed after retries."


# --- Format gate ---

@pytest.mark.parametrize("model", _MODELS)
def test_format_gate_failure_exhausts_retries(model):
    path = f"{_MODULE[model]}.align_format"
    with patch(path, return_value=(False, "format-failed")):
        result = get_fallback(model, "format-test-topic")
    assert result == "ERROR: Alignment failed after retries."


# --- Diagnose gate ---

@pytest.mark.parametrize("model", _MODELS)
def test_diagnose_failure_exhausts_retries(model):
    path = f"{_MODULE[model]}.diagnose"
    with patch(path, return_value=(False, "diagnosis-mismatch")):
        result = get_fallback(model, "diagnose-test-topic")
    assert result == "ERROR: Alignment failed after retries."


# --- score_confidence gate (chatgpt only) ---

def test_chatgpt_low_confidence_exhausts_retries():
    with patch(
        "app.prompts.fallback.chatgpt.score_confidence", return_value=0.5
    ):
        result = get_fallback("chatgpt", "some-topic")
    assert result == "ERROR: Alignment failed after retries."


def test_chatgpt_high_confidence_returns_prompt():
    topic = "market value of neural networks"
    result = get_fallback("chatgpt", topic)
    assert isinstance(result, str)
    assert result != "ERROR: Alignment failed after retries."


# --- intent classification (chatgpt only) ---

def test_chatgpt_market_topic_routes_analyze_market():
    from app.prompts.fallback.chatgpt import interpret_intent
    intent = interpret_intent("measure market value of Vaine")
    assert intent["action"] == "analyze_market"
    assert "analyze and quantify" in intent["goal"]
    assert intent["confidence"] > 0


def test_chatgpt_compare_topic_routes_compare():
    from app.prompts.fallback.chatgpt import interpret_intent
    intent = interpret_intent("compare Claude vs ChatGPT")
    assert intent["action"] == "compare"
    assert "compare" in intent["goal"]


def test_chatgpt_how_topic_routes_instruction():
    from app.prompts.fallback.chatgpt import interpret_intent
    intent = interpret_intent("how to build a startup")
    assert intent["action"] == "instruction"
    assert "step-by-step" in intent["goal"]


def test_chatgpt_no_signal_routes_research():
    from app.prompts.fallback.chatgpt import interpret_intent
    intent = interpret_intent("neural-networks-xyz")
    assert intent["action"] == "research"
    assert intent["confidence"] == 0.0


# --- Happy path sanity ---

@pytest.mark.parametrize("model", _MODELS)
def test_happy_path_returns_non_empty_string(model):
    topic = "neural-networks-abc"
    result = get_fallback(model, topic)
    assert isinstance(result, str)
    assert len(result) > 0
    assert topic in result


import importlib

# --- describe_section branch coverage ---

@pytest.mark.parametrize("model", _MODELS)
def test_describe_section_quantified_value_with_verb(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    result = mod.describe_section("Quantified Value", ["calculate"])
    assert "calculate" in result


@pytest.mark.parametrize("model", _MODELS)
def test_describe_section_comparison_branch(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    result = mod.describe_section("Comparison", [])
    assert "compare" in result


@pytest.mark.parametrize("model", _MODELS)
def test_describe_section_market_problem_branch(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    result = mod.describe_section("Market Problem", [])
    assert "problem" in result


# --- verb extraction ---

@pytest.mark.parametrize("model", _MODELS)
def test_interpret_intent_extracts_calculate_and_estimate(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    intent = mod.interpret_intent("calculate and estimate the market size")
    assert "calculate" in intent["verbs"]
    assert "estimate" in intent["verbs"]


@pytest.mark.parametrize("model", _MODELS)
def test_interpret_intent_extracts_measure_and_compare(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    intent = mod.interpret_intent("measure and compare AI models")
    assert "measure" in intent["verbs"]
    assert "compare" in intent["verbs"]


# --- dimension branch coverage ---

@pytest.mark.parametrize("model", _MODELS)
def test_select_structure_adds_market_problem_section(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    intent = mod.interpret_intent("the core problem in AI industry")
    structure = mod.select_structure(intent)
    assert "Market Problem" in structure["sections"]


@pytest.mark.parametrize("model", _MODELS)
def test_select_structure_adds_comparison_and_solution_sections(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    intent = mod.interpret_intent("compare vs solution to solve the challenge")
    structure = mod.select_structure(intent)
    assert "Comparison" in structure["sections"]
    assert "Solution" in structure["sections"]


# --- select_structure action overrides ---

@pytest.mark.parametrize("model", _MODELS)
def test_select_structure_instruction_override(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    intent = mod.interpret_intent("how to build a startup")
    structure = mod.select_structure(intent)
    assert structure["sections"][0] == "Goal"
    assert "Steps" in structure["sections"]


@pytest.mark.parametrize("model", _MODELS)
def test_select_structure_explain_override(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    intent = mod.interpret_intent("explain why inflation happens")
    structure = mod.select_structure(intent)
    assert "Example" in structure["sections"]
    assert "Requirements" in structure["sections"]


# --- trace function coverage ---

@pytest.mark.parametrize("model", _MODELS)
def test_trace_routes_format_failures(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    assert mod.trace("Output failed") == "format"
    assert mod.trace("Format misaligned") == "format"


@pytest.mark.parametrize("model", _MODELS)
def test_trace_routes_all_non_format_failures(model):
    mod = importlib.import_module(f"app.prompts.fallback.{model}")
    assert mod.trace("Decision misaligned") == "decision"
    assert mod.trace("Judgment failed") == "judgment"
    assert mod.trace("Validation failed") == "validation"
    assert mod.trace("Structure misaligned") == "structure"
    assert mod.trace("unknown failure") == "intent"
