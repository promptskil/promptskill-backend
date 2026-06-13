"""Per-user understanding layer — read full history, compute pattern, upsert.

Isolation contract: every read keys on the caller's user_id. The user_id
predicate is the cross-account guard and must never be dropped. Unlike
history_service, the deleted_at filter is intentionally omitted —
soft-deleted prompts ARE included in the pattern (product decision).

Composition role: build_understanding_block() returns a CONTENT overlay
that generate_service appends to the model's user_message. The json
system_prompt stays the FILTER — this layer never touches it.

Recompute runs in a FastAPI BackgroundTask (recompute_profile) AFTER the
/generate response is sent, on its OWN AsyncSession — the request session
is already closed by then.
"""
from __future__ import annotations

import json
from collections import Counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.prompt import Prompt, PromptVote
from app.models.user_profile import UserProfile


async def compute_profile(user_id: UUID, db: AsyncSession) -> dict:
    """Aggregate the user's OWN prompts into a pattern dict.

    ISOLATION: the user_id predicate is mandatory. NO deleted_at filter —
    soft-deleted prompts are included (product decision).
    """
    rows = (
        await db.execute(
            select(Prompt).where(Prompt.user_id == user_id)
        )
    ).scalars().all()

    ups = sum(1 for r in rows if r.feedback_vote == PromptVote.up)
    downs = sum(1 for r in rows if r.feedback_vote == PromptVote.down)
    topics = Counter(r.topic for r in rows if r.topic)
    models = Counter(r.model for r in rows if r.model)

    return {
        "prompt_count": len(rows),
        "top_topics": [t for t, _ in topics.most_common(5)],
        "preferred_model": models.most_common(1)[0][0] if models else None,
        "vote_accept_ratio": (ups / (ups + downs)) if (ups + downs) else None,
        "cadence": None,  # reserved — derive from created_at spacing later
    }


async def upsert_profile(user_id: UUID, data: dict, db: AsyncSession) -> None:
    """Insert or update the caller's profile row."""
    prof = await db.get(UserProfile, user_id)
    if prof is None:
        prof = UserProfile(user_id=user_id)
        db.add(prof)
    prof.prompt_count = data["prompt_count"]
    prof.top_topics = json.dumps(data["top_topics"])
    prof.preferred_model = data["preferred_model"]
    prof.vote_accept_ratio = data["vote_accept_ratio"]
    prof.cadence = data["cadence"]
    await db.commit()


async def get_profile(user_id: UUID, db: AsyncSession) -> UserProfile | None:
    """Read ONLY the caller's profile. user_id is the key AND the guard."""
    return await db.get(UserProfile, user_id)


def build_understanding_block(prof: UserProfile | None) -> str:
    """Content overlay injected into the model's user_message.

    Returns '' for a thin/absent profile (below MIN_HISTORY) so new users
    get no injection. Size-bounded by construction.
    """
    if prof is None or prof.prompt_count < settings.UNDERSTANDING_MIN_HISTORY:
        return ""
    parts: list[str] = []
    topics = ", ".join(json.loads(prof.top_topics or "[]"))
    if topics:
        parts.append(f"recurring topics: {topics}")
    if prof.preferred_model:
        parts.append(f"prefers {prof.preferred_model}")
    if prof.vote_accept_ratio is not None:
        parts.append(f"prior acceptance {prof.vote_accept_ratio:.0%}")
    if not parts:
        return ""
    return "User context — " + "; ".join(parts) + "."


async def recompute_profile(user_id: UUID) -> None:
    """BackgroundTask entrypoint — runs AFTER the /generate response.

    Opens its OWN session; the request-scoped db is already closed.
    Caller-scoped: every read inside keys on this user_id.
    """
    async with AsyncSessionLocal() as db:
        data = await compute_profile(user_id, db)
        await upsert_profile(user_id, data, db)
