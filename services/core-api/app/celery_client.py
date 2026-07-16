import logging

from celery import Celery

from app.config import settings

logger = logging.getLogger(__name__)

_celery: Celery | None = None


def _get_celery() -> Celery:
    global _celery
    if _celery is None:
        _celery = Celery("aria_core_api_producer", broker=settings.celery_broker_url)
        # A down Redis otherwise blocks on the OS-level TCP connect timeout
        # (tens of seconds) before send_task's own exception handling ever
        # gets a chance to swallow it — fail fast so an unreachable broker
        # doesn't stall the upload request it's supposed to be decoupled from.
        _celery.conf.broker_transport_options = {
            "socket_connect_timeout": 2,
            "socket_timeout": 2,
        }
    return _celery


def enqueue_document_processing(document_id: str) -> None:
    """Fire-and-forget enqueue of the worker's process_document task.

    core-api never imports worker's Celery app (that would couple the two
    services' deploys) — this is a standalone producer with no result
    backend, sending a task by name. If Redis is unreachable, the document
    stays `pending` and the upload request still succeeds; per M2's strict
    decoupling principle, the ingestion pipeline is allowed to degrade, the
    CRUD write is not.
    """
    try:
        _get_celery().send_task(
            "app.tasks.process_document.process_document", args=[document_id]
        )
    except Exception:
        logger.warning("failed to enqueue document processing for %s", document_id, exc_info=True)


def enqueue_document_deletion(document_id: str, storage_path: str) -> None:
    """Fire-and-forget enqueue of the worker's delete_document task, which
    removes the document from Chroma, S3, and Mongo. If Redis is
    unreachable, cleanup simply doesn't happen yet — per the same
    decoupling principle as `enqueue_document_processing`, callers must not
    depend on this succeeding for their own request to complete.
    """
    try:
        _get_celery().send_task(
            "app.tasks.delete_document.delete_document", args=[document_id, storage_path]
        )
    except Exception:
        logger.warning("failed to enqueue document deletion for %s", document_id, exc_info=True)
