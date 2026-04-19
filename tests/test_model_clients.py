"""Model client adapter tests — Path B (Layer 7 v2).

Each adapter is tested for:
  - async happy path (agenerate returns string)
  - sync happy path (generate returns string)
  - rate limit → ProviderRateLimitError
  - API error → ProviderAPIError

Mocking strategy:
  - Each provider SDK is patched at the adapter call boundary
  - No real API calls — all mocked responses
  - get_client() dispatch tested for all registered providers
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.model_clients import (
    AnthropicClient,
    GeminiClient,
    GrokClient,
    OpenAIClient,
    ProviderAPIError,
    ProviderRateLimitError,
    get_client,
)


# ─────────────────────── get_client dispatch ──────────────────────────────


def test_get_client_returns_anthropic():
    assert isinstance(get_client("anthropic"), AnthropicClient)


def test_get_client_returns_openai():
    assert isinstance(get_client("openai"), OpenAIClient)


def test_get_client_returns_gemini():
    assert isinstance(get_client("gemini"), GeminiClient)


def test_get_client_returns_xai():
    assert isinstance(get_client("xai"), GrokClient)


def test_get_client_unknown_raises():
    with pytest.raises(KeyError, match="Unknown provider"):
        get_client("bogus")


# ─────────────────────── Anthropic adapter ────────────────────────────────


def _anthropic_response(text="test output"):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_async_happy_path():
    client = AnthropicClient()
    mock_api = MagicMock()
    mock_api.messages.create = AsyncMock(return_value=_anthropic_response("hello"))
    with patch("app.services.model_clients.anthropic.AsyncAnthropic", return_value=mock_api):
        result = await client.agenerate("sys", "user msg", "claude-sonnet-4-6")
    assert result == "hello"


def test_anthropic_sync_happy_path():
    client = AnthropicClient()
    mock_api = MagicMock()
    mock_api.messages.create.return_value = _anthropic_response("sync hello")
    with patch("app.services.model_clients.anthropic.Anthropic", return_value=mock_api):
        result = client.generate("sys", "user msg", "claude-sonnet-4-6")
    assert result == "sync hello"


@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_async_rate_limit():
    import anthropic
    client = AnthropicClient()
    err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(err, "rate limited")
    mock_api = MagicMock()
    mock_api.messages.create = AsyncMock(side_effect=err)
    with patch("app.services.model_clients.anthropic.AsyncAnthropic", return_value=mock_api):
        with pytest.raises(ProviderRateLimitError) as exc_info:
            await client.agenerate("sys", "msg", "model")
    assert exc_info.value.provider == "anthropic"


@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_async_api_error():
    import anthropic
    client = AnthropicClient()
    err = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
    Exception.__init__(err, "server error")
    mock_api = MagicMock()
    mock_api.messages.create = AsyncMock(side_effect=err)
    with patch("app.services.model_clients.anthropic.AsyncAnthropic", return_value=mock_api):
        with pytest.raises(ProviderAPIError) as exc_info:
            await client.agenerate("sys", "msg", "model")
    assert exc_info.value.provider == "anthropic"


# ─────────────────────── OpenAI adapter ───────────────────────────────────


def _openai_response(text="test output"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    return resp


@pytest.mark.asyncio(loop_scope="session")
async def test_openai_async_happy_path():
    client = OpenAIClient()
    mock_api = MagicMock()
    mock_api.chat.completions.create = AsyncMock(return_value=_openai_response("openai out"))
    with patch("app.services.model_clients.openai.AsyncOpenAI", return_value=mock_api):
        result = await client.agenerate("sys", "msg", "gpt-4o")
    assert result == "openai out"


def test_openai_sync_happy_path():
    client = OpenAIClient()
    mock_api = MagicMock()
    mock_api.chat.completions.create.return_value = _openai_response("sync openai")
    with patch("app.services.model_clients.openai.OpenAI", return_value=mock_api):
        result = client.generate("sys", "msg", "gpt-4o")
    assert result == "sync openai"


@pytest.mark.asyncio(loop_scope="session")
async def test_openai_async_rate_limit():
    import openai
    client = OpenAIClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {}
    err = openai.RateLimitError(
        message="rate limited",
        response=mock_resp,
        body=None,
    )
    mock_api = MagicMock()
    mock_api.chat.completions.create = AsyncMock(side_effect=err)
    with patch("app.services.model_clients.openai.AsyncOpenAI", return_value=mock_api):
        with pytest.raises(ProviderRateLimitError) as exc_info:
            await client.agenerate("sys", "msg", "gpt-4o")
    assert exc_info.value.provider == "openai"


# ─────────────────────── Grok adapter (extends OpenAI) ────────────────────


def test_grok_uses_xai_base_url():
    client = GrokClient()
    assert client._get_base_url() == "https://api.x.ai/v1"


@pytest.mark.asyncio(loop_scope="session")
async def test_grok_async_happy_path():
    client = GrokClient()
    mock_api = MagicMock()
    mock_api.chat.completions.create = AsyncMock(return_value=_openai_response("grok out"))
    with patch("app.services.model_clients.openai.AsyncOpenAI", return_value=mock_api):
        result = await client.agenerate("sys", "msg", "grok-3")
    assert result == "grok out"


# ─────────────────────── Gemini adapter ───────────────────────────────────


def _gemini_response(text="gemini output"):
    resp = MagicMock()
    resp.text = text
    return resp


@pytest.mark.asyncio(loop_scope="session")
async def test_gemini_async_happy_path():
    client = GeminiClient()
    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content = AsyncMock(
        return_value=_gemini_response("gemini out")
    )
    with patch("app.services.model_clients.genai.Client", return_value=mock_genai_client):
        result = await client.agenerate("sys", "msg", "gemini-2.0-flash")
    assert result == "gemini out"


def test_gemini_sync_happy_path():
    client = GeminiClient()
    mock_genai_client = MagicMock()
    mock_genai_client.models.generate_content.return_value = _gemini_response("sync gemini")
    with patch("app.services.model_clients.genai.Client", return_value=mock_genai_client):
        result = client.generate("sys", "msg", "gemini-2.0-flash")
    assert result == "sync gemini"


@pytest.mark.asyncio(loop_scope="session")
async def test_gemini_async_rate_limit():
    from google.genai import errors as genai_errors
    client = GeminiClient()
    err = genai_errors.APIError.__new__(genai_errors.APIError)
    Exception.__init__(err, "rate limited")
    err.code = 429
    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content = AsyncMock(side_effect=err)
    with patch("app.services.model_clients.genai.Client", return_value=mock_genai_client):
        with pytest.raises(ProviderRateLimitError) as exc_info:
            await client.agenerate("sys", "msg", "gemini-2.0-flash")
    assert exc_info.value.provider == "gemini"


@pytest.mark.asyncio(loop_scope="session")
async def test_gemini_async_api_error():
    from google.genai import errors as genai_errors
    client = GeminiClient()
    err = genai_errors.APIError.__new__(genai_errors.APIError)
    Exception.__init__(err, "server error")
    err.code = 500
    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content = AsyncMock(side_effect=err)
    with patch("app.services.model_clients.genai.Client", return_value=mock_genai_client):
        with pytest.raises(ProviderAPIError) as exc_info:
            await client.agenerate("sys", "msg", "gemini-2.0-flash")
    assert exc_info.value.provider == "gemini"
