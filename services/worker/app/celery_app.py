from celery import Celery

from app.config import settings

celery_app = Celery("aria_worker", broker=settings.broker_url, backend=settings.result_backend)
celery_app.autodiscover_tasks(["app"])
