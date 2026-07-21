"""Unit tests for Engine 2 run_service — mocked provider client + Redis."""
import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import run_service
from app.services.model_clients import ProviderAPIError, ProviderRateLimitError


def _client(answer="answer"):
    c = AsyncMock()
    c.agenerate = AsyncMock(return_value=answer)
    return c


async def test_uncapped_success():
    with patch.object(run_service, "get_client", return_value=_client("hi")):
        result = await run_service.run_prompt("chatgpt", "prompt", uuid4())
    assert result == {"model": "chatgpt", "answer": "hi"}


async def test_passthrough_sends_empty_system_prompt():
    # Engine 2 is a pure passthrough: the finished Vaine prompt goes through as
    # the user message with NO system layer, so the model returns natural output.
    c = _client("hi")
    with patch.object(run_service, "get_client", return_value=c):
        await run_service.run_prompt("chatgpt", "finished vaine prompt", uuid4())
    assert c.agenerate.call_args.kwargs["system_prompt"] == ""
    assert c.agenerate.call_args.kwargs["user_message"] == "finished vaine prompt"


async def test_provider_error_maps_502():
    c = AsyncMock()
    c.agenerate = AsyncMock(
        side_effect=ProviderAPIError("openai", RuntimeError("boom"))
    )
    with patch.object(run_service, "get_client", return_value=c):
        with pytest.raises(HTTPException) as exc:
            await run_service.run_prompt("chatgpt", "p", uuid4())
    assert exc.value.status_code == 502


async def test_rate_limit_maps_429():
    c = AsyncMock()
    c.agenerate = AsyncMock(side_effect=ProviderRateLimitError("openai"))
    with patch.object(run_service, "get_client", return_value=c):
        with pytest.raises(HTTPException) as exc:
            await run_service.run_prompt("chatgpt", "p", uuid4())
    assert exc.value.status_code == 429


async def test_timeout_maps_502(monkeypatch):
    monkeypatch.setattr(run_service, "_TIMEOUT_SECONDS", 0.01)

    async def _hang(*args, **kwargs):
        await asyncio.sleep(0.5)

    c = AsyncMock()
    c.agenerate = _hang

    with patch.object(run_service, "get_client", return_value=c):
        with pytest.raises(HTTPException) as exc:
            await run_service.run_prompt("chatgpt", "p", uuid4())

    assert exc.value.status_code == 502
