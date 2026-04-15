"""Fallback dispatch — maps model string to per-model generator.

Spec: /api L522-525 — on anthropic.APIStatusError (500),
      generate_fallback(topic) is called and prompt row is written with
      system_prompt_version = 'fallback'.

Public surface:
    get_fallback(model, topic) -> str
"""
from app.prompts.fallback import chatgpt, claude, gemini, grok

_DISPATCH = {
    "claude": claude.generate_fallback,
    "chatgpt": chatgpt.generate_fallback,
    "gemini": gemini.generate_fallback,
    "grok": grok.generate_fallback,
}


def get_fallback(model: str, topic: str) -> str:
    """Return fallback prompt text for the given model + topic.

    Raises KeyError if model is not one of the four registered models —
    caller should validate model against MODEL_REGISTRY before reaching
    the fallback path.
    """
    try:
        fn = _DISPATCH[model]
    except KeyError as exc:
        raise KeyError(
            f"No fallback registered for model={model!r}. "
            f"Expected one of {sorted(_DISPATCH)}."
        ) from exc
    return fn(topic)
