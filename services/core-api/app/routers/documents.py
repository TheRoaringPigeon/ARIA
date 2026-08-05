import re
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from PIL import Image, ImageOps
from pydantic import ValidationError
from pymongo import ReturnDocument

from app import s3
from app.celery_client import (
    enqueue_document_deletion,
    enqueue_document_processing,
    enqueue_finalize_document_draft,
)
from app.config import settings
from app.dependencies import (
    SessionContext,
    get_current_session,
    get_db_dep,
    require_entity_access,
    validate_shared_with,
)
from app.ids import new_id
from app.schemas.documents import (
    ALLOWED_MIME_TYPES,
    DocumentDraftCreateMeta,
    DocumentUploadMeta,
    DraftPageReorderMeta,
)
from aria_auth import check_permission, has_shared_access
from aria_shared.models import Document, DocumentDraft, DocumentDraftPage, DocumentType

router = APIRouter(tags=["documents"])

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_PATH_CHARS = re.compile(r'[\\/\x00-\x1f\x7f]')


def _safe_storage_filename(filename: str) -> str:
    """Strip path separators/control chars so a crafted upload filename
    (e.g. containing `/` or `..`) can't escape its `{household}/{document}/`
    prefix in the S3 key."""
    name = _UNSAFE_PATH_CHARS.sub("_", filename).lstrip(".")
    return name or "file"


def _normalize_image_orientation(content: bytes, mime_type: str) -> bytes:
    """Re-encodes with EXIF rotation baked into the pixels, quality=90.
    No-op for PDFs (no EXIF-rotation concept at this layer) and for images
    with no orientation tag. Dropping the original EXIF blob on re-encode
    also strips any embedded GPS/location data before the photo lands in a
    shared household document."""
    if mime_type not in ("image/jpeg", "image/png"):
        return content
    image = ImageOps.exif_transpose(Image.open(BytesIO(content)))
    buf = BytesIO()
    image.save(buf, format="JPEG" if mime_type == "image/jpeg" else "PNG", quality=90)
    return buf.getvalue()


async def _check_entity_access(
    db: AsyncIOMotorDatabase, session: SessionContext, entity_ids: list[str]
) -> None:
    """Every linked entity must exist in the caller's household, not be
    archived, and the caller's role must be allowed to create against its
    domain. Shared by `upload_document` and the draft-create endpoint —
    the validation is identical, only the DTO differs.
    """
    entity_docs_by_id = {
        entity_doc["_id"]: entity_doc
        for entity_doc in await db.entities.find(
            {"_id": {"$in": entity_ids}, "household_id": session.household_id}
        ).to_list(length=None)
    }
    for entity_id in entity_ids:
        entity_doc = entity_docs_by_id.get(entity_id)
        if entity_doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"entity {entity_id} not found")
        if entity_doc.get("archived_at") is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"entity {entity_id} is archived"
            )
        if entity_doc.get("pending_delete_at") is not None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"entity {entity_id} not found"
            )
        check_permission(session.role, entity_doc["domain"], "create")
        if not has_shared_access(
            session, entity_doc.get("shared_with", "household"), entity_doc["created_by"]
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"entity {entity_id} not found")


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
    content = await run_in_threadpool(_normalize_image_orientation, content, file.content_type)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"file exceeds maximum upload size of {settings.max_upload_bytes} bytes",
        )

    await _check_entity_access(db, session, meta.entity_ids)

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


# --- Mobile multi-photo document capture (M12) -----------------------------
#
# A document_drafts row is a staging area for photos captured but not yet
# stitched into a real Document — see docs/plans/m12-mobile-photo-capture.md.


async def _require_draft(
    draft_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> dict:
    draft = await db.document_drafts.find_one(
        {"_id": draft_id, "household_id": session.household_id}
    )
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "draft not found")
    if not has_shared_access(session, draft.get("shared_with", "household"), draft["created_by"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "draft not found")
    return draft


@router.post(
    "/documents/drafts",
    response_model=DocumentDraft,
    status_code=status.HTTP_201_CREATED,
    response_model_by_alias=False,
)
async def create_document_draft(
    body: DocumentDraftCreateMeta,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> DocumentDraft:
    await validate_shared_with(db, session.household_id, body.shared_with)
    await _check_entity_access(db, session, body.entity_ids)

    now = datetime.now(timezone.utc)
    draft = DocumentDraft(
        id=new_id(),
        household_id=session.household_id,
        entity_ids=body.entity_ids,
        document_type=body.document_type,
        shared_with=body.shared_with,
        created_by=session.user_id,
        created_at=now,
        last_activity_at=now,
        pages=[],
        status="capturing",
    )
    await db.document_drafts.insert_one(draft.to_mongo())
    return draft


@router.post(
    "/documents/drafts/{draft_id}/pages",
    response_model=DocumentDraft,
    response_model_by_alias=False,
)
async def upload_draft_page(
    file: UploadFile = File(...),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    draft: dict = Depends(_require_draft),
) -> DocumentDraft:
    if draft["status"] != "capturing":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"draft is {draft['status']}, not capturing"
        )
    if file.content_type not in ("image/jpeg", "image/png"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unsupported file type {file.content_type!r}; expected image/jpeg or image/png",
        )

    content = await file.read()
    # Normalize before the size check, same reasoning as upload_document:
    # the size check and the stored S3 object should both reflect the
    # re-encoded bytes, not the raw upload.
    content = await run_in_threadpool(_normalize_image_orientation, content, file.content_type)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"file exceeds maximum upload size of {settings.max_upload_bytes} bytes",
        )

    page_id = new_id()
    extension = "jpg" if file.content_type == "image/jpeg" else "png"
    storage_path = f"{draft['household_id']}/drafts/{draft['_id']}/{page_id}.{extension}"

    try:
        await run_in_threadpool(s3.upload, storage_path, BytesIO(content), file.content_type)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not store file: {exc}"
        ) from exc

    page = DocumentDraftPage(id=page_id, storage_path=storage_path, mime_type=file.content_type)
    # $push is atomic at the document level — two racing page-uploads on the
    # same draft can't both read the same "current count" and collide, the
    # way a count-then-insert approach could. See the plan doc for why this
    # matters (concurrent-upload regression test covers it).
    updated = await db.document_drafts.find_one_and_update(
        {"_id": draft["_id"]},
        {
            "$push": {"pages": page.model_dump()},
            "$set": {"last_activity_at": datetime.now(timezone.utc)},
        },
        return_document=ReturnDocument.AFTER,
    )
    return DocumentDraft.model_validate(updated)


@router.get(
    "/documents/drafts/{draft_id}",
    response_model=DocumentDraft,
    response_model_by_alias=False,
)
async def get_document_draft(draft: dict = Depends(_require_draft)) -> DocumentDraft:
    return DocumentDraft.model_validate(draft)


@router.get("/documents/drafts/{draft_id}/pages/{page_id}/file")
async def download_draft_page(
    page_id: str,
    draft: dict = Depends(_require_draft),
) -> StreamingResponse:
    page = next((p for p in draft.get("pages", []) if p["id"] == page_id), None)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")
    try:
        body = await run_in_threadpool(s3.stream, page["storage_path"])
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not retrieve file: {exc}"
        ) from exc
    return StreamingResponse(body.iter_chunks(), media_type=page["mime_type"])


@router.delete(
    "/documents/drafts/{draft_id}/pages/{page_id}",
    response_model=DocumentDraft,
    response_model_by_alias=False,
)
async def delete_draft_page(
    page_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    draft: dict = Depends(_require_draft),
) -> DocumentDraft:
    page = next((p for p in draft.get("pages", []) if p["id"] == page_id), None)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")

    try:
        await run_in_threadpool(s3.delete, page["storage_path"])
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not delete file: {exc}"
        ) from exc

    # $pull splices the matched element out and closes the gap, so the
    # remaining pages keep their relative order with no renumbering step.
    updated = await db.document_drafts.find_one_and_update(
        {"_id": draft["_id"]},
        {
            "$pull": {"pages": {"id": page_id}},
            "$set": {"last_activity_at": datetime.now(timezone.utc)},
        },
        return_document=ReturnDocument.AFTER,
    )
    return DocumentDraft.model_validate(updated)


@router.patch(
    "/documents/drafts/{draft_id}/pages/reorder",
    response_model=DocumentDraft,
    response_model_by_alias=False,
)
async def reorder_draft_pages(
    body: DraftPageReorderMeta,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    draft: dict = Depends(_require_draft),
) -> DocumentDraft:
    current_pages = draft.get("pages", [])
    current_ids = {p["id"] for p in current_pages}
    # A mismatch (page added/deleted concurrently, e.g. from another tab)
    # means the client's view is stale — 409 rather than silently applying
    # an order that doesn't reflect the draft's real current pages.
    if len(body.page_ids) != len(current_pages) or set(body.page_ids) != current_ids:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "page_ids does not match the draft's current pages; refetch and retry",
        )

    pages_by_id = {p["id"]: p for p in current_pages}
    reordered = [pages_by_id[page_id] for page_id in body.page_ids]

    updated = await db.document_drafts.find_one_and_update(
        {"_id": draft["_id"]},
        {"$set": {"pages": reordered, "last_activity_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    return DocumentDraft.model_validate(updated)


@router.post(
    "/documents/drafts/{draft_id}/finalize",
    response_model=DocumentDraft,
    status_code=status.HTTP_202_ACCEPTED,
    response_model_by_alias=False,
)
async def finalize_document_draft_endpoint(
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    draft: dict = Depends(_require_draft),
) -> DocumentDraft:
    if not draft.get("pages"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "draft has no pages")

    # Guarded find_one_and_update on the current status: a no-match means a
    # finalize is already in flight or already succeeded, so this returns
    # 409 rather than double-enqueueing (a double-tap on "Create", or a
    # client retry racing the first request).
    updated = await db.document_drafts.find_one_and_update(
        {"_id": draft["_id"], "status": {"$in": ["capturing", "failed"]}},
        {"$set": {"status": "finalizing", "finalize_error": None}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "draft is already finalizing or finalized"
        )

    try:
        enqueue_finalize_document_draft(draft["_id"])
    except Exception as exc:
        # Unlike enqueue_document_processing/enqueue_document_deletion,
        # this enqueue is not fire-and-forget — there's no Document yet, so
        # a failed enqueue means the operation didn't happen at all. Roll
        # the draft back to `capturing` so the client can retry instead of
        # leaving it stuck in `finalizing` with no worker ever watching it.
        await db.document_drafts.update_one(
            {"_id": draft["_id"]}, {"$set": {"status": "capturing"}}
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not enqueue finalize: {exc}"
        ) from exc

    return DocumentDraft.model_validate(updated)


@router.delete("/documents/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_document_draft(
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    draft: dict = Depends(_require_draft),
) -> Response:
    try:
        for page in draft.get("pages", []):
            await run_in_threadpool(s3.delete, page["storage_path"])
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not delete file: {exc}"
        ) from exc

    await db.document_drafts.delete_one({"_id": draft["_id"]})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
