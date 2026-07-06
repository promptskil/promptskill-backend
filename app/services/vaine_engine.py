"""Vaine Module (Phase 4) — the ONE generative step in the Vaine path.

The fine-tuned Llama (served by Together, OpenAI-compatible endpoint) turns a
raw topic into an expanded rewrite. Judgment is in the WEIGHTS (fine-tuned on
the Vaine dataset) — there is NO runtime system prompt. Everything downstream
(strengthen, format) is deterministic; this is the only LLM call in the Vaine
path. Mock this boundary in tests.
"""
from __future__ import annotations

import openai

from app.config import settings

_MAX_TOKENS = 1000
_TIMEOUT_SECONDS = 28.0  # under the 30s ceiling


class VaineInferenceError(Exception):
    """The Vaine Module (Together/Llama) call failed or returned no content."""


async def agenerate(
    topic: str,
    max_tokens: int = _MAX_TOKENS,
    timeout_s: float = _TIMEOUT_SECONDS,
) -> str:
    """Expand a raw topic into the Vaine Module's rewrite — one generative call.

    Calls the fine-tuned Llama on Together (OpenAI-compatible). No system
    prompt: judgment is in the weights. Stays under the 30s ceiling. Raises
    VaineInferenceError on transport failure or empty output.
    """
    client = openai.AsyncOpenAI(
        api_key=settings.TOGETHER_API_KEY,
        base_url=settings.VAINE_BASE_URL,
        timeout=timeout_s,
    )
    try:
        resp = await client.chat.completions.create(
            model=settings.VAINE_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": topic}],
        )
    except openai.OpenAIError as exc:
        raise VaineInferenceError(str(exc)) from exc
    text = resp.choices[0].message.content
    if not text:
        raise VaineInferenceError("empty completion")
    return text.strip()
