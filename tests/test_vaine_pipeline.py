from unittest.mock import AsyncMock, patch

import pytest

from app.services.generate_service import load_vaine_data, vaine_generate
from app.services.vaine_engine import VaineInferenceError


async def test_vaine_generate_runs_full_pipeline():
    load_vaine_data()
    with patch(
        "app.services.generate_service.vaine_engine.agenerate",
        new=AsyncMock(return_value="write a good clear plan to get clients"),
    ):
        out = await vaine_generate("claude", "help me get more clients")
    assert out["prompt"] and isinstance(out["prompt"], str)
    assert out["class"] in ("simple", "complex")


async def test_vaine_generate_unknown_model_raises():
    load_vaine_data()
    with pytest.raises(VaineInferenceError):
        await vaine_generate("nonexistent-model", "topic")
