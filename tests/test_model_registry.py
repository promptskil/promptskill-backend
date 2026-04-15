"""Model registry + fallback tests — Phase 5.

Gate coverage (from /build-checklist):
  Step 5.1 — version, system_prompt, anthropic_model_id present in
             all four JSON files (claude, chatgpt, gemini, grok)
  Step 5.2 — topic injected in all four fallback models
  Step 5.3 — MODEL_REGISTRY['claude']['version'] == 'v1'
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


# ─────────────────────── Step 5.1 gates ─────────────────────────────────

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


# ─────────────────────── Step 5.3 gate ──────────────────────────────────

def test_claude_version_is_v1():
    """Phase 5 closing gate per checklist Step 5.3."""
    assert MODEL_REGISTRY["claude"]["version"] == "v1"


def test_load_model_registry_is_idempotent():
    """Second call must not duplicate or corrupt the registry."""
    first = dict(MODEL_REGISTRY)
    load_model_registry()
    assert MODEL_REGISTRY == first


# ─────────────────────── Step 5.2 gates ─────────────────────────────────

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


# ─────────────────────── Loader guard coverage ──────────────────────────

def test_loader_rejects_reserved_version(tmp_path, monkeypatch):
    """If someone ever sets version='fallback' in a real config, the
    loader must refuse to start."""
    from app.services import generate_service as gs

    bad_dir = tmp_path / "prompts"
    bad_dir.mkdir()
    for m in _MODELS:
        cfg = {
            "version": "fallback" if m == "claude" else "v1",
            "model": m,
            "anthropic_model_id": "stub",
            "system_prompt": "stub",
        }
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
        cfg = {
            "version": "v1",
            "model": "WRONG" if m == "claude" else m,
            "anthropic_model_id": "stub",
            "system_prompt": "stub",
        }
        (bad_dir / f"{m}.json").write_text(json.dumps(cfg))

    monkeypatch.setattr(gs, "_PROMPTS_DIR", bad_dir)
    with pytest.raises(ValueError, match="does not match filename"):
        load_model_registry()

    monkeypatch.undo()
    load_model_registry()


# ─────────────────────── Startup hook registration ──────────────────────

def test_startup_hook_registered_on_app():
    """Guards against drift — if the @app.on_event('startup') decorator
    is removed from main.py, MODEL_REGISTRY would be empty in production."""
    from app.main import app
    startup_handlers = app.router.on_startup
    names = [h.__name__ for h in startup_handlers]
    assert "_load_registry_on_startup" in names


# ─────────────────────── File-presence sanity ──────────────────────────

def test_all_json_files_exist_on_disk():
    prompts_dir = Path(__file__).resolve().parent.parent / "app" / "prompts"
    for m in _MODEL_NAMES:
        assert (prompts_dir / f"{m}.json").exists()
