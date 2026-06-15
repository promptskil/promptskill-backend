"""Celery app instance — Phase 4.1.

Broker:   REDIS_URL if set, else memory:// (eager/dev fallback).
Backend:  None — email is fire-and-forget, no .get() callers.
Eager:    False default; tests override via conftest.
Beat:     intentionally omitted until referenced tasks exist.
Sync/async rule: full-stack-engineer L948-L954.
"""
import os

from celery import Celery
from celery.schedules import crontab

_BROKER = os.getenv("REDIS_URL", "memory://")

celery_app = Celery(
    "promptskill",
    broker=_BROKER,
    backend=None,
    include=[
        "app.tasks.email_task",
        "app.tasks.purge_task",
        "app.tasks.generate_task",
        "app.tasks.welcome_email_task",
        "app.tasks.business_invite_email_task",
        "app.tasks.reconcile_task",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_always_eager=False,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "weekly-purge": {
            "task": "purge_job_task",
            "schedule": crontab(hour=0, minute=0, day_of_week=1),
        },
        "daily-reconcile": {
            "task": "reconcile_subscriptions_task",
            "schedule": crontab(hour=3, minute=0),
        },
    },
)
