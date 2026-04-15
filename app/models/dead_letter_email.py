"""Terminal record of email tasks that exhausted all retries.

Phase 4.4 — source of truth for email DLQ. Redis LIST is a hot-view
cache bounded at 1000; this table is the durable record for audit +
support lookups.

Semantics: forensic-only in Phase 4.4 (no replay path).
Replay deferred to Phase 15.
"""
from uuid import uuid4

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class DeadLetterEmail(Base):
    __tablename__ = "dead_letter_emails"

    # Separate PK from task_id — a task_id can produce multiple DLQ rows
    # across retry redelivery (acks_late + worker crash).
    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    task_id = Column(String, nullable=False)
    task_name = Column(String, nullable=False)
    # Full recipient email — internal-only forensic record.
    # Logs redact; DLQ stores full for support/audit.
    recipient = Column(String, nullable=False)
    error = Column(Text, nullable=False)
    retries_exhausted = Column(Integer, nullable=False)
    created_at = Column(
        DateTime, server_default=func.now(), nullable=False
    )
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_dlq_emails_created_at", "created_at"),
        Index("ix_dlq_emails_task_id", "task_id"),
    )
