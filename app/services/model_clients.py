"""Multi-provider model client adapters — Path B (Layer 7 v2).

Each adapter normalises a provider's SDK into one interface:
  agenerate() — async, used by FastAPI generate_service.py

Exception normalisation:
  Provider-specific rate-limit and API errors are caught inside each
  adapter and re-raised as ProviderRateLimitError / ProviderAPIError.
  generate_service.py catches ONLY these common types — it never
  imports provider SDKs directly.

Spec: path-b-multi-provider-architecture.md — Requirements §1
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import anthropic
import openai
from google import genai
from google.genai import errors as genai_errors

from app.config import settings

# ─────────────────── Common exception types ───────────────────


class ProviderRateLimitError(Exception):
    """Provider returned HTTP 429 or equivalent."""

    def __init__(self, provider: str, original: Exception | None = None):
        self.provider = provider
        self.original = original
        super().__init__(f"{provider}: rate limited")


class ProviderAPIError(Exception):
    """Provider returned a non-rate-limit API error."""

    def __init__(self, provider: str, original: Exception | None = None):
        self.provider = provider
        self.original = original
        super().__init__(f"{provider}: API error — {original}")


# ─────────────────── Abstract base ────────────────────────────


class ModelClient(ABC):
    """Abstract base for all provider adapters."""

    @abstractmethod
    async def agenerate(
        self,
        system_prompt: str,
        user_message: str,
        model_id: str,
        max_tokens: int = 1000,
    ) -> str:
        """Async generation — FastAPI path."""
        ...


# ─────────────────── Anthropic ────────────────────────────────


class AnthropicClient(ModelClient):
    """Wraps anthropic SDK (async)."""

    def __init__(self) -> None:
        self._client: anthropic.AsyncAnthropic | None = None

    def _sdk(self) -> anthropic.AsyncAnthropic:
        # Lazy + cached: one client (with its httpx keep-alive pool) reused
        # across requests — no new TCP/TLS handshake per generate. Lazy so it
        # binds to the running event loop, not import time.
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY
            )
        return self._client

    async def agenerate(
        self,
        system_prompt: str,
        user_message: str,
        model_id: str,
        max_tokens: int = 1000,
    ) -> str:
        client = self._sdk()
        try:
            response = await client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return _extract_anthropic_text(response)
        except anthropic.RateLimitError as exc:
            raise ProviderRateLimitError("anthropic", exc) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderAPIError("anthropic", exc) from exc


def _extract_anthropic_text(response) -> str:
    """Extract text from Anthropic response, handling ContentBlock union."""
    text_value = next(
        (
            t
            for t in (getattr(b, "text", None) for b in response.content)
            if isinstance(t, str)
        ),
        None,
    )
    if text_value is None:
        raise ProviderAPIError(
            "anthropic", RuntimeError("response_missing_text_block")
        )
    return text_value


# ─────────────────── OpenAI ───────────────────────────────────


class OpenAIClient(ModelClient):
    """Wraps openai SDK (async).

    Subclassed by GrokClient with different base_url.
    """

    def __init__(self) -> None:
        self._client: openai.AsyncOpenAI | None = None

    def _get_base_url(self) -> str | None:
        """Override in subclasses to change the API endpoint."""
        return None  # default OpenAI endpoint

    def _get_api_key(self) -> str:
        """Override in subclasses for different env vars."""
        return settings.OPENAI_API_KEY

    def _token_param(self) -> str:
        """GPT-5 family requires max_completion_tokens, not max_tokens."""
        return "max_completion_tokens"

    def _sdk(self) -> openai.AsyncOpenAI:
        # Lazy + cached per instance — the Grok subclass caches its own client
        # built from the overridden base_url/api_key, never sharing OpenAI's.
        # Lazy so it binds to the running event loop, not import time.
        if self._client is None:
            self._client = openai.AsyncOpenAI(
                api_key=self._get_api_key(),
                base_url=self._get_base_url(),
            )
        return self._client

    async def agenerate(
        self,
        system_prompt: str,
        user_message: str,
        model_id: str,
        max_tokens: int = 1000,
    ) -> str:
        client = self._sdk()
        try:
            response = await client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                **{self._token_param(): max_tokens},
            )
            text = response.choices[0].message.content
            if not text:
                raise ProviderAPIError(
                    "openai", RuntimeError("response_missing_content")
                )
            return text
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError("openai", exc) from exc
        except openai.APIStatusError as exc:
            raise ProviderAPIError("openai", exc) from exc


# ─────────────────── Gemini (Google GenAI) ────────────────────


class GeminiClient(ModelClient):
    """Wraps google-genai SDK (async).

    SDK pattern:
      client.aio.models.generate_content(model=..., contents=...)

    Exception: google.genai.errors.APIError (check .code for 429).
    """

    def __init__(self) -> None:
        self._client: genai.Client | None = None

    def _sdk(self) -> genai.Client:
        # Lazy + cached: reuse one genai client across requests. Lazy so it
        # binds to the running event loop, not import time.
        if self._client is None:
            self._client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        return self._client

    async def agenerate(
        self,
        system_prompt: str,
        user_message: str,
        model_id: str,
        max_tokens: int = 1000,
    ) -> str:
        client = self._sdk()
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=user_message,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=max_tokens,
                ),
            )
            text = response.text
            if not text:
                raise ProviderAPIError(
                    "gemini", RuntimeError("response_missing_text")
                )
            return text
        except genai_errors.APIError as exc:
            if exc.code == 429:
                raise ProviderRateLimitError("gemini", exc) from exc
            raise ProviderAPIError("gemini", exc) from exc


# ─────────────────── Grok (xAI — OpenAI-compatible) ──────────


class GrokClient(OpenAIClient):
    """xAI Grok API — reuses OpenAI SDK with different base_url.

    No extra dependency. OpenAI SDK + base_url swap.
    """

    _XAI_BASE_URL = "https://api.x.ai/v1"

    def _get_base_url(self) -> str:
        return self._XAI_BASE_URL

    def _get_api_key(self) -> str:
        return settings.XAI_API_KEY

    def _token_param(self) -> str:
        return "max_tokens"  # xAI still accepts the classic param (Grok works today)


# ─────────────────── Client registry ──────────────────────────

_CLIENTS: dict[str, ModelClient] = {
    "anthropic": AnthropicClient(),
    "openai": OpenAIClient(),
    "gemini": GeminiClient(),
    "xai": GrokClient(),
}


def get_client(provider: str) -> ModelClient:
    """Return the ModelClient for a provider name.

    Raises KeyError if provider is not registered.
    """
    try:
        return _CLIENTS[provider]
    except KeyError:
        raise KeyError(
            f"Unknown provider {provider!r}. "
            f"Registered: {list(_CLIENTS.keys())}"
        )
