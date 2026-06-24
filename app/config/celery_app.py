from celery import Celery
from celery.schedules import crontab

celery_app = Celery(
    "document_tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)

celery_app.conf.task_routes = {
    "tasks.inject_verified_documents": {"queue": "documents"},
}

# Run every 2 minutes
celery_app.conf.beat_schedule = {
    "inject-verified-docs-every-2-mins": {
        "task": "tasks.inject_verified_documents",
        "schedule": crontab(minute="*/2"),  # every 2 minutes
    },
}
