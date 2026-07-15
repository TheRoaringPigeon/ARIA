from app.celery_app import celery_app


@celery_app.task(name="app.tasks.ping")
def ping() -> str:
    """Scaffolding-stage task: proves the broker/backend wiring works.
    Real OCR/chunk/embed tasks land in a follow-up pass.
    """
    return "pong"
