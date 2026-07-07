"""Vaine learner cadence (Phase 8.2). Counts new Vaine feedback and flags
when the retrain threshold is met. GATED: it does NOT auto-fine-tune or swap
VAINE_MODEL — cost + quality stay deliberate. Sync engine, like reconcile.
"""
import logging

from sqlalchemy import text

from app.database_sync import sync_engine
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_RETRAIN_THRESHOLD = 200  # new voted Vaine prompts before a retrain is worthwhile


@celery_app.task(name="vaine_learn_task")
def vaine_learn_task() -> dict:
    """Surface 'N examples ready to retrain' — flag only, no auto-retrain."""
    with sync_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM prompts "
                "WHERE system_prompt_version LIKE 'vaine%' "
                "AND feedback_vote IS NOT NULL "
                "AND deleted_at IS NULL"
            )
        ).scalar_one()
    result = {
        "feedback_count": count,
        "threshold": _RETRAIN_THRESHOLD,
        "retrain_ready": count >= _RETRAIN_THRESHOLD,
    }
    logger.info("vaine_learn", extra=result)
    return result
