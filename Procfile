web: uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers
worker: celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2
beat: celery -A app.tasks.celery_app beat --loglevel=info
