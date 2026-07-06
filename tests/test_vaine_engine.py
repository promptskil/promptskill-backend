from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from app.services.vaine_engine import VaineInferenceError, agenerate


def _resp(text):
    choice = MagicMock()
    choice.message.content = text
    r = MagicMock()
    r.choices = [choice]
    return r


async def test_agenerate_returns_stripped_text():
    with patch("app.services.vaine_engine.openai.AsyncOpenAI") as ctor:
        ctor.return_value.chat.completions.create = AsyncMock(
            return_value=_resp("  rewrite  ")
        )
        assert await agenerate("topic") == "rewrite"


async def test_agenerate_raises_on_empty():
    with patch("app.services.vaine_engine.openai.AsyncOpenAI") as ctor:
        ctor.return_value.chat.completions.create = AsyncMock(return_value=_resp(""))
        with pytest.raises(VaineInferenceError):
            await agenerate("topic")


async def test_agenerate_wraps_transport_error():
    with patch("app.services.vaine_engine.openai.AsyncOpenAI") as ctor:
        ctor.return_value.chat.completions.create = AsyncMock(
            side_effect=openai.OpenAIError("boom")
        )
        with pytest.raises(VaineInferenceError):
            await agenerate("topic")
