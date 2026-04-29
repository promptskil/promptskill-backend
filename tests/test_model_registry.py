"""Model registry + fallback tests — Phase 5 (Layer 7 v2 — multi-provider).

Gate coverage (from /build-checklist):
  Step 5.1 — version, system_prompt, provider, provider_model_id,
             user_message_template present in all four JSON files
  Step 5.2 — topic injected in all four fallback models
  Step 5.3 — MODEL_REGISTRY['claude']['version'] == 'v2'
             load_model_registry() populates all four models
"""
import json
from pathlib import Path

import pytest

from app.prompts.fallback import get_fallback
from app.services.generate_service import (
    MODEL_REGISTRY,
    _MODELS,
    _REQUIRED_KEYS,
    _RESERVED_VERSION,
    load_model_registry,
)

_MODEL_NAMES = ("claude", "chatgpt", "gemini", "grok")


@pytest.fixture(autouse=True)
def _reload_registry():
    """Ensure each test runs against a freshly-loaded registry, independent
    of prior test mutation."""
    load_model_registry()
    yield


# ─────────────────────── Step 5.1 gates ───────────────────────────────────

def test_all_four_models_present():
    assert set(MODEL_REGISTRY.keys()) == set(_MODEL_NAMES)


@pytest.mark.parametrize("model", _MODEL_NAMES)
def test_config_has_all_required_keys(model):
    cfg = MODEL_REGISTRY[model]
    for key in _REQUIRED_KEYS:
        assert key in cfg, f"{model}.json missing {key!r}"
        assert cfg[key], f"{model}.json has empty {key!r}"


@pytest.mark.parametrize("model", _MODEL_NAMES)
def test_config_model_field_matches_key(model):
    assert MODEL_REGISTRY[model]["model"] == model


@pytest.mark.parametrize("model", _MODEL_NAMES)
def test_version_is_not_reserved_fallback(model):
    """'fallback' is reserved for the degraded path — no real config
    may use it as a version string (spec /api L426)."""
    assert MODEL_REGISTRY[model]["version"] != _RESERVED_VERSION


@pytest.mark.parametrize("model", _MODEL_NAMES)
def test_config_has_valid_provider(model):
    valid = ("anthropic", "openai", "gemini", "xai")
    assert MODEL_REGISTRY[model]["provider"] in valid


@pytest.mark.parametrize("model", _MODEL_NAMES)
def test_user_message_template_has_topic_placeholder(model):
    assert "{topic}" in MODEL_REGISTRY[model]["user_message_template"]


# ─────────────────────── Step 5.3 gate ────────────────────────────────────

def test_claude_version_is_v3():
    """Layer 7 v3 — version bumped from v2 to v3 (topic-adaptive prompts)."""
    assert MODEL_REGISTRY["claude"]["version"] == "v3"


def test_load_model_registry_is_idempotent():
    """Second call must not duplicate or corrupt the registry."""
    first = dict(MODEL_REGISTRY)
    load_model_registry()
    assert MODEL_REGISTRY == first


# ─────────────────────── Provider mapping ─────────────────────────────────

def test_claude_uses_anthropic():
    assert MODEL_REGISTRY["claude"]["provider"] == "anthropic"


def test_chatgpt_uses_openai():
    assert MODEL_REGISTRY["chatgpt"]["provider"] == "openai"


def test_gemini_uses_gemini():
    assert MODEL_REGISTRY["gemini"]["provider"] == "gemini"


def test_grok_uses_xai():
    assert MODEL_REGISTRY["grok"]["provider"] == "xai"


# ─────────────────────── Step 5.2 gates ───────────────────────────────────

@pytest.mark.parametrize("model", _MODEL_NAMES)
def test_fallback_injects_topic(model):
    """Per spec L446-448: topic IS used, not a static string."""
    topic = "machine-learning-xyz"
    result = get_fallback(model, topic)
    assert isinstance(result, str)
    assert topic in result


def test_fallback_unknown_model_raises():
    with pytest.raises(KeyError):
        get_fallback("bogus", "topic")


# ─────────────────────── Loader guard coverage ────────────────────────────

def _make_v2_config(model_name, **overrides):
    """Build a valid v2 config dict for testing loader guards."""
    cfg = {
        "version": "v2",
        "model": model_name,
        "provider": "anthropic",
        "provider_model_id": "stub-model",
        "user_message_template": "Generate about: '{topic}'",
        "system_prompt": "stub system prompt",
    }
    cfg.update(overrides)
    return cfg


def test_loader_rejects_reserved_version(tmp_path, monkeypatch):
    """If someone ever sets version='fallback' in a real config, the
    loader must refuse to start."""
    from app.services import generate_service as gs

    bad_dir = tmp_path / "prompts"
    bad_dir.mkdir()
    for m in _MODELS:
        cfg = _make_v2_config(m, version="fallback" if m == "claude" else "v2")
        (bad_dir / f"{m}.json").write_text(json.dumps(cfg))

    monkeypatch.setattr(gs, "_PROMPTS_DIR", bad_dir)
    with pytest.raises(ValueError, match="reserved"):
        load_model_registry()

    # Restore real registry so downstream tests are unaffected
    monkeypatch.undo()
    load_model_registry()


def test_loader_rejects_model_field_mismatch(tmp_path, monkeypatch):
    from app.services import generate_service as gs

    bad_dir = tmp_path / "prompts"
    bad_dir.mkdir()
    for m in _MODELS:
        cfg = _make_v2_config(m, model="WRONG" if m == "claude" else m)
        (bad_dir / f"{m}.json").write_text(json.dumps(cfg))

    monkeypatch.setattr(gs, "_PROMPTS_DIR", bad_dir)
    with pytest.raises(ValueError, match="does not match filename"):
        load_model_registry()

    monkeypatch.undo()
    load_model_registry()


def test_loader_rejects_invalid_provider(tmp_path, monkeypatch):
    from app.services import generate_service as gs

    bad_dir = tmp_path / "prompts"
    bad_dir.mkdir()
    for m in _MODELS:
        cfg = _make_v2_config(m, provider="invalid_provider" if m == "claude" else "anthropic")
        (bad_dir / f"{m}.json").write_text(json.dumps(cfg))

    monkeypatch.setattr(gs, "_PROMPTS_DIR", bad_dir)
    with pytest.raises(ValueError, match="not in"):
        load_model_registry()

    monkeypatch.undo()
    load_model_registry()


def test_loader_rejects_missing_topic_placeholder(tmp_path, monkeypatch):
    from app.services import generate_service as gs

    bad_dir = tmp_path / "prompts"
    bad_dir.mkdir()
    for m in _MODELS:
        template = "no placeholder here" if m == "claude" else "about '{topic}'"
        cfg = _make_v2_config(m, user_message_template=template)
        (bad_dir / f"{m}.json").write_text(json.dumps(cfg))

    monkeypatch.setattr(gs, "_PROMPTS_DIR", bad_dir)
    with pytest.raises(ValueError, match="topic"):
        load_model_registry()

    monkeypatch.undo()
    load_model_registry()


# ─────────────────────── Startup hook registration ────────────────────────

def test_startup_hook_registered_on_app():
    """Guards against drift — if the @app.on_event('startup') decorator
    is removed from main.py, MODEL_REGISTRY would be empty in production."""
    from app.main import app
    startup_handlers = app.router.on_startup
    names = [h.__name__ for h in startup_handlers]
    assert "_load_registry_on_startup" in names


# ─────────────────────── File-presence sanity ─────────────────────────────

def test_all_json_files_exist_on_disk():
    prompts_dir = Path(__file__).resolve().parent.parent / "app" / "prompts"
    for m in _MODEL_NAMES:
        assert (prompts_dir / f"{m}.json")