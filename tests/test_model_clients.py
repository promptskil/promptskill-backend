"""Model client adapter tests — Path B (Layer 7 v2).

Each adapter is tested for:
  - async happy path (agenerate returns string)
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


# ─────────────── SDK client reuse — constructed once per instance ──────────


@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_sdk_client_constructed_once():
    client = AnthropicClient()
    mock_api = MagicMock()
    mock_api.messages.create = AsyncMock(return_value=_anthropic_response("x"))
    with patch(
        "app.services.model_clients.anthropic.AsyncAnthropic", return_value=mock_api
    ) as ctor:
        await client.agenerate("sys", "a", "model")
        await client.agenerate("sys", "b", "model")
    assert ctor.call_count == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_openai_sdk_client_constructed_once():
    client = OpenAIClient()
    mock_api = MagicMock()
    mock_api.chat.completions.create = AsyncMock(return_value=_openai_response("x"))
    with patch(
        "app.services.model_clients.openai.AsyncOpenAI", return_value=mock_api
    ) as ctor:
        await client.agenerate("sys", "a", "gpt-4o")
        await client.agenerate("sys", "b", "gpt-4o")
    assert ctor.call_count == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_gemini_sdk_client_constructed_once():
    client = GeminiClient()
    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content = AsyncMock(
        return_value=_gemini_response("x")
    )
    with patch(
        "app.services.model_clients.genai.Client", return_value=mock_genai_client
    ) as ctor:
        await client.agenerate("sys", "a", "gemini-2.0-flash")
        await client.agenerate("sys", "b", "gemini-2.0-flash")
    assert ctor.call_count == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_grok_sdk_client_constructed_once_with_xai_config():
    client = GrokClient()
    mock_api = MagicMock()
    mock_api.chat.completions.create = AsyncMock(return_value=_openai_response("x"))
    with patch(
        "app.services.model_clients.openai.AsyncOpenAI", return_value=mock_api
    ) as ctor:
        await client.agenerate("sys", "a", "grok-3")
        await client.agenerate("sys", "b", "grok-3")
    assert ctor.call_count == 1
    assert ctor.call_args.kwargs["base_url"] == "https://api.x.ai/v1"


# ─────────────── empty system prompt → omitted per provider ──────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_omits_system_when_empty():
    client = AnthropicClient()
    mock_api = MagicMock()
    mock_api.messages.create = AsyncMock(return_value=_anthropic_response("x"))
    with patch(
        "app.services.model_clients.anthropic.AsyncAnthropic", return_value=mock_api
    ):
        await client.agenerate("", "user msg", "model")
    assert "system" not in mock_api.messages.create.call_args.kwargs


@pytest.mark.asyncio(loop_scope="session")
async def test_openai_omits_system_when_empty():
    client = OpenAIClient()
    mock_api = MagicMock()
    mock_api.chat.completions.create = AsyncMock(return_value=_openai_response("x"))
    with patch(
        "app.services.model_clients.openai.AsyncOpenAI", return_value=mock_api
    ):
        await client.agenerate("", "hello user", "gpt-4o")
    assert mock_api.chat.completions.create.call_args.kwargs["messages"] == [
        {"role": "user", "content": "hello user"}
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_gemini_omits_system_when_empty():
    client = GeminiClient()
    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content = AsyncMock(
        return_value=_gemini_response("x")
    )
    with patch(
        "app.services.model_clients.genai.Client", return_value=mock_genai_client
    ):
        await client.agenerate("", "hello", "gemini-2.0-flash")
    cfg = mock_genai_client.aio.models.generate_content.call_args.kwargs["config"]
    assert cfg.system_instruction is None


# ─────────────── astream — streaming deltas per provider ─────────────────


async def _aiter_list(items):
    for i in items:
        yield i


class _FakeAnthropicStream:
    def __init__(self, texts):
        self._texts = texts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    @property
    def text_stream(self):
        return _aiter_list(self._texts)


def _openai_event(delta, type_="content.delta"):
    ev = MagicMock()
    ev.type = type_
    ev.delta = delta
    return ev


class _FakeOpenAIStream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def __aiter__(self):
        return _aiter_list(self._events)


def _gemini_chunk(text):
    ch = MagicMock()
    ch.text = text
    return ch


@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_astream_yields_deltas():
    client = AnthropicClient()
    mock_api = MagicMock()
    mock_api.messages.stream = MagicMock(
        return_value=_FakeAnthropicStream(["He", "llo"])
    )
    with patch(
        "app.services.model_clients.anthropic.AsyncAnthropic", return_value=mock_api
    ):
        chunks = [c async for c in client.astream("", "hi", "model")]
    assert chunks == ["He", "llo"]
    kwargs = mock_api.messages.stream.call_args.kwargs
    assert "system" not in kwargs
    assert kwargs["tools"][0]["type"] == "web_search_20250305"
    assert kwargs["tools"][0]["max_uses"] == 5


@pytest.mark.asyncio(loop_scope="session")
async def test_openai_astream_uses_responses_web_search():
    client = OpenAIClient()
    mock_api = MagicMock()
    mock_api.responses.stream = MagicMock(
        return_value=_FakeOpenAIStream(
            [
                _openai_event("Live ", type_="response.output_text.delta"),
                _openai_event("news", type_="response.output_text.delta"),
                _openai_event(None, type_="response.completed"),
            ]
        )
    )
    with patch(
        "app.services.model_clients.openai.AsyncOpenAI", return_value=mock_api
    ):
        chunks = [c async for c in client.astream("", "gpu news", "gpt-5.5")]
    assert chunks == ["Live ", "news"]
    kwargs = mock_api.responses.stream.call_args.kwargs
    assert {"type": "web_search", "search_context_size": "low"} in kwargs["tools"]
    assert kwargs["input"] == "gpu news"
    assert kwargs["max_output_tokens"] >= 8192


@pytest.mark.asyncio(loop_scope="session")
async def test_openai_astream_incomplete_maps_error():
    client = OpenAIClient()
    mock_api = MagicMock()
    mock_api.responses.stream = MagicMock(
        return_value=_FakeOpenAIStream(
            [_openai_event(None, type_="response.incomplete")]
        )
    )
    with patch(
        "app.services.model_clients.openai.AsyncOpenAI", return_value=mock_api
    ):
        with pytest.raises(ProviderAPIError):
            [c async for c in client.astream("", "q", "gpt-5.5")]


@pytest.mark.asyncio(loop_scope="session")
async def test_gemini_astream_yields_deltas():
    client = GeminiClient()
    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content_stream = AsyncMock(
        return_value=_aiter_list(
            [_gemini_chunk("He"), _gemini_chunk("llo"), _gemini_chunk("")]
        )
    )
    with patch(
        "app.services.model_clients.genai.Client", return_value=mock_genai_client
    ):
        chunks = [c async for c in client.astream("", "hi", "gemini-2.0-flash")]
    assert chunks == ["He", "llo"]
    call = mock_genai_client.aio.models.generate_content_stream.call_args
    cfg = call.kwargs["config"]
    assert cfg.system_instruction is None
    assert cfg.tools[0].google_search is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_gemini_astream_rate_limit():
    from google.genai import errors as genai_errors

    client = GeminiClient()
    err = genai_errors.APIError.__new__(genai_errors.APIError)
    Exception.__init__(err, "rate limited")
    err.code = 429
    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content_stream = AsyncMock(side_effect=err)
    with patch(
        "app.services.model_clients.genai.Client", return_value=mock_genai_client
    ):
        with pytest.raises(ProviderRateLimitError):
            [c async for c in client.astream("", "q", "gemini-2.0-flash")]


@pytest.mark.asyncio(loop_scope="session")
async def test_grok_astream_uses_responses_web_search_xai():
    client = GrokClient()
    mock_api = MagicMock()
    mock_api.responses.stream = MagicMock(
        return_value=_FakeOpenAIStream(
            [
                _openai_event("hi", type_="response.output_text.delta"),
                _openai_event(None, type_="response.completed"),
            ]
        )
    )
    with patch(
        "app.services.model_clients.openai.AsyncOpenAI", return_value=mock_api
    ) as ctor:
        chunks = [c async for c in client.astream("", "q", "grok-4.5")]
    assert chunks == ["hi"]
    assert ctor.call_args.kwargs["base_url"] == "https://api.x.ai/v1"
    kwargs = mock_api.responses.stream.call_args.kwargs
    assert {"type": "web_search"} in kwargs["tools"]
    assert kwargs["input"] == "q"
    assert kwargs["max_output_tokens"] >= 8192


@pytest.mark.asyncio(loop_scope="session")
async def test_grok_astream_incomplete_maps_error():
    client = GrokClient()
    mock_api = MagicMock()
    mock_api.responses.stream = MagicMock(
        return_value=_FakeOpenAIStream(
            [_openai_event(None, type_="response.incomplete")]
        )
    )
    with patch(
        "app.services.model_clients.openai.AsyncOpenAI", return_value=mock_api
    ):
        with pytest.raises(ProviderAPIError):
            [c async for c in client.astream("", "q", "grok-4.5")]


@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_astream_rate_limit():
    import anthropic

    client = AnthropicClient()
    err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(err, "rate limited")
    mock_api = MagicMock()
    mock_api.messages.stream = MagicMock(side_effect=err)
    with patch(
        "app.services.model_clients.anthropic.AsyncAnthropic", return_value=mock_api
    ):
        with pytest.raises(ProviderRateLimitError):
            [c async for c in client.astream("sys", "hi", "model")]


@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_astream_api_error():
    import anthropic

    client = AnthropicClient()
    err = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
    Exception.__init__(err, "server error")
    mock_api = MagicMock()
    mock_api.messages.stream = MagicMock(side_effect=err)
    with patch(
        "app.services.model_clients.anthropic.AsyncAnthropic", return_value=mock_api
    ):
        with pytest.raises(ProviderAPIError):
            [c async for c in client.astream("sys", "hi", "model")]
