import re
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError

from app import s3
from app.celery_client import enqueue_document_deletion, enqueue_document_processing
from app.config import settings
from app.dependencies import (
    SessionContext,
    get_current_session,
    get_db_dep,
    require_entity_access,
    validate_shared_with,
)
from app.ids import new_id
from app.schemas.documents import ALLOWED_MIME_TYPES, DocumentUploadMeta
from aria_auth import check_permission, has_shared_access
from aria_shared.models import Document, DocumentType

router = APIRouter(tags=["documents"])

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_PATH_CHARS = re.compile(r'[\\/\x00-\x1f\x7f]')


def _safe_storage_filename(filename: str) -> str:
    """Strip path separators/control chars so a crafted upload filename
    (e.g. containing `/` or `..`) can't escape its `{household}/{document}/`
    prefix in the S3 key."""
    name = _UNSAFE_PATH_CHARS.sub("_", filename).lstrip(".")
    return name or "file"


def _content_disposition(filename: str) -> str:
    """RFC 6266 attachment header: an ASCII-safe fallback `filename` plus a
    UTF-8 `filename*` for non-ASCII names, both stripped of control chars
    so a crafted upload filename can't inject extra headers or break
    encoding on download."""
    sanitized = _CONTROL_CHARS.sub("", filename) or "file"
    ascii_fallback = sanitized.encode("ascii", "replace").decode("ascii").replace('"', "'")
    quoted = quote(sanitized, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted}'


@router.post(
    "/documents",
    response_model=Document,
    status_code=status.HTTP_201_CREATED,
    response_model_by_alias=False,
)
async def upload_document(
    file: UploadFile = File(...),
    document_type: DocumentType = Form(...),
    entity_ids: list[str] = Form(...),
    shared_with: list[str] = Form(default=[]),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> Document:
    try:
        meta = DocumentUploadMeta(
            document_type=document_type, entity_ids=entity_ids, shared_with=shared_with
        )
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # [] means "shared with the whole household" — see schemas/documents.py.
    resolved_shared_with: str | list[str] = meta.shared_with or "household"
    await validate_shared_with(db, session.household_id, resolved_shared_with)

    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unsupported file type {file.content_type!r}; expected one of {sorted(ALLOWED_MIME_TYPES)}",
        )

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"file exceeds maximum upload size of {settings.max_upload_bytes} bytes",
        )

    # Every linked entity must exist in the caller's household, not be
    # archived, and the caller's role must be allowed to create against its
    # domain — same validation shape as logs.entity_id, extended to a list.
    entity_docs_by_id = {
        entity_doc["_id"]: entity_doc
        for entity_doc in await db.entities.find(
            {"_id": {"$in": meta.entity_ids}, "household_id": session.household_id}
        ).to_list(length=None)
    }
    for entity_id in meta.entity_ids:
        entity_doc = entity_docs_by_id.get(entity_id)
        if entity_doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"entity {entity_id} not found")
        if entity_doc.get("archived_at") is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"entity {entity_id} is archived"
            )
        check_permission(session.role, entity_doc["domain"], "create")
        if not has_shared_access(
            session, entity_doc.get("shared_with", "household"), entity_doc["created_by"]
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"entity {entity_id} not found")

    document_id = new_id()
    storage_path = f"{session.household_id}/{document_id}/{_safe_storage_filename(file.filename)}"

    try:
        await run_in_threadpool(s3.upload, storage_path, BytesIO(content), file.content_type)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not store file: {exc}"
        ) from exc

    now = datetime.now(timezone.utc)
    document = Document(
        id=document_id,
        household_id=session.household_id,
        entity_ids=meta.entity_ids,
        log_ids=[],
        document_type=meta.document_type,
        original_filename=file.filename,
        storage_path=storage_path,
        mime_type=file.content_type,
        file_size_bytes=len(content),
        page_count=None,
        processing_status="pending",
        processing_error=None,
        shared_with=resolved_shared_with,
        uploaded_by=session.user_id,
        uploaded_at=now,
    )
    await db.documents.insert_one(document.to_mongo())

    # Fire-and-forget: if Redis/worker is unreachable the document simply
    # stays `pending` — upload/list/view keep working via pure Mongo CRUD
    # per the strict decoupling principle.
    enqueue_document_processing(document.id)

    return document


@router.get(
    "/entities/{entity_id}/documents",
    response_model=list[Document],
    response_model_by_alias=False,
)
async def list_entity_documents(
    entity_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> list[Document]:
    await require_entity_access(db, session, entity_id)

    docs = (
        await db.documents.find({"entity_ids": entity_id, "household_id": session.household_id})
        .sort("uploaded_at", -1)
        .to_list(length=None)
    )
    # Being able to see the entity doesn't automatically mean every
    # document attached to it is shared with you too — a document's
    # `shared_with` can be narrower than its linked entity's.
    return [
        Document.model_validate(doc)
        for doc in docs
        if has_shared_access(session, doc.get("shared_with", "household"), doc["uploaded_by"])
    ]


async def _require_document(
    document_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> dict:
    doc = await db.documents.find_one(
        {"_id": document_id, "household_id": session.household_id}
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    # .get(), not [] — a document uploaded before `shared_with` existed has
    # no such key stored at all; missing means "household".
    if not has_shared_access(session, doc.get("shared_with", "household"), doc["uploaded_by"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return doc


@router.get("/documents/{document_id}", response_model=Document, response_model_by_alias=False)
async def get_document(doc: dict = Depends(_require_document)) -> Document:
    return Document.model_validate(doc)


@router.get("/documents/{document_id}/file")
async def download_document(doc: dict = Depends(_require_document)) -> StreamingResponse:
    document = Document.model_validate(doc)
    try:
        body = await run_in_threadpool(s3.stream, document.storage_path)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not retrieve file: {exc}"
        ) from exc
    return StreamingResponse(
        body.iter_chunks(),
        media_type=document.mime_type,
        headers={"Content-Disposition": _content_disposition(document.original_filename)},
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(_require_document),
) -> Response:
    document = Document.model_validate(doc)

    # Documents don't carry their own domain — permission is scoped through
    # each linked entity's domain, same shape as upload's per-entity check.
    if document.entity_ids:
        entity_docs = await db.entities.find(
            {"_id": {"$in": document.entity_ids}, "household_id": session.household_id}
        ).to_list(length=None)
        for entity_doc in entity_docs:
            check_permission(session.role, entity_doc["domain"], "delete")

    # The Mongo row goes away synchronously so the 204 response means "gone"
    # immediately, matching the prior behavior. S3/Chroma cleanup is handed
    # off to the same worker task the entity-cascade path uses — it takes
    # storage_path directly rather than looking the Mongo row back up, so
    # it works fine even though that row is already deleted by the time the
    # task runs.
    await db.documents.delete_one({"_id": document_id})
    enqueue_document_deletion(document.id, document.storage_path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
