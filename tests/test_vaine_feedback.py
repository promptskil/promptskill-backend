from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.models.prompt import PromptVote
from app.services.history_service import export_vaine_feedback


async def test_export_vaine_feedback_maps_polarity():
    up = SimpleNamespace(
        topic="get clients", prompt_text="write a plan",
        feedback_vote=PromptVote("up"), created_at=datetime(2026, 7, 7),
    )
    down = SimpleNamespace(
        topic="raise prices", prompt_text="analyze pricing",
        feedback_vote=PromptVote("down"), created_at=datetime(2026, 7, 7),
    )
    res = MagicMock()
    res.scalars.return_value.all.return_value = [up, down]
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)

    out = await export_vaine_feedback(db)
    assert out[0]["polarity"] == "positive"
    assert out[1]["polarity"] == "negative"
    assert out[0]["source"] == "feedback"
    assert out[0]["input"] == "get clients"
    assert out[0]["target_rewrite"] == "write a plan"
