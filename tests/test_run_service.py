"""Unit tests for Engine 2 run_service — mocked provider client + Redis."""
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


async def test_opus_under_cap_counts_success():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    with patch.object(run_service, "_redis", redis), patch.object(
        run_service, "get_client", return_value=_client("a")
    ):
        result = await run_service.run_prompt("claude-opus", "p", uuid4())
    assert result["answer"] == "a"
    redis.incr.assert_awaited_once()
    redis.expire.assert_awaited_once()  # first success sets the 24h TTL


async def test_opus_at_cap_rejects_before_provider():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="2")
    with patch.object(run_service, "_redis", redis), patch.object(
        run_service, "get_client"
    ) as get_client:
        with pytest.raises(HTTPException) as exc:
            await run_service.run_prompt("claude-opus", "p", uuid4())
    assert exc.value.status_code == 429
    get_client.assert_not_called()  # cap blocks before any paid call


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
