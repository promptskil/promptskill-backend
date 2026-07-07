import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services import generate_service
from app.services.generate_service import (
    load_model_registry,
    load_vaine_data,
    vaine_generate,
)
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


def _mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


async def test_generate_prompt_vaine_path_returns_metadata(monkeypatch):
    load_model_registry()
    load_vaine_data()
    monkeypatch.setattr(settings, "VAINE_ENABLED", True)
    db = _mock_db()
    with patch(
        "app.services.generate_service.vaine_engine.agenerate",
        new=AsyncMock(return_value="write a clear plan to get clients"),
    ):
        result = await generate_service.generate_prompt(
            model="claude",
            topic="help me get more clients",
            user_id=uuid.uuid4(),
            app_version="1.0.0",
            db=db,
        )
    assert result["metadata"]["engine"] == "vaine"
    assert result["metadata"]["class"] in ("simple", "complex")
    assert result["prompt"]
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


async def test_generate_prompt_vaine_error_maps_to_504(monkeypatch):
    load_model_registry()
    load_vaine_data()
    monkeypatch.setattr(settings, "VAINE_ENABLED", True)
    db = _mock_db()
    with patch(
        "app.services.generate_service.vaine_engine.agenerate",
        new=AsyncMock(side_effect=VaineInferenceError("boom")),
    ):
        with pytest.raises(HTTPException) as exc:
            await generate_service.generate_prompt(
                model="claude",
                topic="x",
                user_id=uuid.uuid4(),
                app_version="1.0.0",
                db=db,
            )
    assert exc.value.status_code == 504
