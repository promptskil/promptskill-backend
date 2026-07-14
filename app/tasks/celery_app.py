"""Celery app instance — Phase 4.1.

Broker:   REDIS_URL if set, else memory:// (eager/dev fallback).
Backend:  None — email is fire-and-forget, no .get() callers.
Eager:    False default; tests override via conftest.
Beat:     intentionally omitted until referenced tasks exist.
Sync/async rule: full-stack-engineer L948-L954.
"""
import os
import ssl

from celery import Celery
from celery.schedules import crontab

_BROKER = os.getenv("REDIS_URL", "memory://")
# Upstash uses rediss:// (TLS). Without explicit SSL options Celery falls back to
# UNVERIFIED TLS; require cert verification for real (rediss) brokers only.
_BROKER_SSL = (
    {"ssl_cert_reqs": ssl.CERT_REQUIRED} if _BROKER.startswith("rediss://") else None
)

celery_app = Celery(
    "promptskill",
    broker=_BROKER,
    backend=None,
    include=[
        "app.tasks.email_task",
        "app.tasks.purge_task",
        "app.tasks.generate_task",
        "app.tasks.welcome_email_task",
        "app.tasks.reconcile_task",
        "app.tasks.verification_email_task",
        "app.tasks.learn_task",
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
    broker_use_ssl=_BROKER_SSL,
    beat_schedule={
        "weekly-purge": {
            "task": "purge_job_task",
            "schedule": crontab(hour=0, minute=0, day_of_week=1),
        },
        "daily-reconcile": {
            "task": "reconcile_subscriptions_task",
            "schedule": crontab(hour=3, minute=0),
        },
        "weekly-vaine-learn": {
            "task": "vaine_learn_task",
            "schedule": crontab(hour=1, minute=0, day_of_week=1),
        },
    },
)
