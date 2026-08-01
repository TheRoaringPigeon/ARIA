import asyncio
import base64
import logging
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.concurrency import run_in_threadpool
from jinja2 import Environment, FileSystemLoader, select_autoescape
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field, ValidationError
from pypdf import PdfReader, PdfWriter
from weasyprint import HTML

from app import s3
from app.dependencies import (
    SessionContext,
    get_current_session,
    get_db_dep,
    require_entity_access,
    validate_shared_with,
)
from app.ids import new_id
from app.schemas.documents import ALLOWED_MIME_TYPES
from app.schemas.entities import EntityCreate, EntityUpdate
from aria_auth import Action, check_permission, has_shared_access
from aria_shared.models import Document, EntityBase, EntityDomain, LogEntry, Schedule
from aria_shared.timezones import to_household_date

router = APIRouter(prefix="/entities", tags=["entities"])
logger = logging.getLogger(__name__)

MAX_LIMIT = 200

# Derived from ALLOWED_MIME_TYPES (schemas/documents.py), the single source
# of truth for what a document upload may be — not redeclared by value, so a
# future change there (e.g. adding image/webp) doesn't silently desync from
# what the export attachment logic below knows how to handle.
_ATTACHABLE_PDF_MIME_TYPE = "application/pdf"
_ATTACHABLE_IMAGE_MIME_TYPES = ALLOWED_MIME_TYPES - {_ATTACHABLE_PDF_MIME_TYPE}
_ATTACHABLE_MIME_TYPES = ALLOWED_MIME_TYPES

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=select_autoescape(["html"]))

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _content_disposition(filename: str) -> str:
    """RFC 6266 attachment header — identical to documents.py's helper of
    the same name, duplicated here rather than imported since entities.py
    and documents.py don't otherwise share any code and this is the only
    other place that needs it."""
    sanitized = _CONTROL_CHARS.sub("", filename) or "file"
    ascii_fallback = sanitized.encode("ascii", "replace").decode("ascii").replace('"', "'")
    quoted = quote(sanitized, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted}'


async def _household_timezone(db: AsyncIOMotorDatabase, household_id: str) -> str | None:
    household = await db.households.find_one({"_id": household_id}, {"timezone": 1})
    return household.get("timezone") if household else None


def _format_attr_value(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


async def _fetch_document_bytes(document: Document) -> bytes | None:
    """Best-effort: a stray missing/corrupted S3 object shouldn't fail an
    otherwise-fine export, so a fetch failure here is logged and skipped
    rather than raised."""
    try:
        body = await run_in_threadpool(s3.stream, document.storage_path)
        return await run_in_threadpool(body.read)
    except Exception:
        logger.warning("export: could not fetch document %s for attachment", document.id, exc_info=True)
        return None


def _merge_pdfs(base_pdf: bytes, extra_pdfs: list[bytes]) -> bytes:
    """Appends each attached PDF's pages after the WeasyPrint-rendered base
    PDF. A single malformed attachment is skipped (logged), same "don't let
    one bad attachment sink the export" stance as `_fetch_document_bytes`."""
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(base_pdf)))
    for pdf_bytes in extra_pdfs:
        try:
            writer.append(PdfReader(BytesIO(pdf_bytes)))
        except Exception:
            logger.warning("export: could not merge an attached PDF", exc_info=True)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _search_filter(q: str) -> dict:
    """Case-insensitive substring match against `name`/`tags`/`location`,
    plus any value in the free-form `specs` dict.

    `re.escape` — `q` is user input passed straight into `$regex`; without
    escaping, a query like "(" or ".*" either errors Mongo or matches
    everything.

    No operator does "regex against any value of a dict" directly —
    `$objectToArray` turns `specs` into `[{k, v}, ...]` pairs, then
    `$filter` + `$size > 0` checks whether any value matches. `$ifNull`
    covers entities with no `specs` key at all. Deliberately `$regex`, not a
    Mongo text index: this backs a type-ahead search box where "rang"
    should match "Ranger" mid-word — `$text` only matches whole (optionally
    stemmed) tokens, so it wouldn't. No indexes exist anywhere in `core-api`
    yet (single-household data, not multi-tenant scale), so an unindexed
    scan here is an accepted, non-blocking tradeoff.
    """
    pattern = re.escape(q.strip())
    return {
        "$or": [
            {"name": {"$regex": pattern, "$options": "i"}},
            # Mongo's $regex on an array field matches if any element
            # matches — no $elemMatch needed.
            {"tags": {"$regex": pattern, "$options": "i"}},
            {"location": {"$regex": pattern, "$options": "i"}},
            {
                "$expr": {
                    "$gt": [
                        {
                            "$size": {
                                "$filter": {
                                    "input": {"$objectToArray": {"$ifNull": ["$specs", {}]}},
                                    "as": "kv",
                                    "cond": {
                                        "$regexMatch": {"input": "$$kv.v", "regex": pattern, "options": "i"}
                                    },
                                }
                            }
                        },
                        0,
                    ]
                }
            },
        ]
    }


# FastAPI's response_model_by_alias defaults to True, which would leak
# Mongo's `_id` wire format (aria_shared models alias id -> _id for
# storage) into every JSON response. Every route below passes this
# explicitly False so responses use the Python field name `id` instead.


def require_entity(action: Action):
    """Dependency factory: fetch `{entity_id}` (404 if missing or in
    another household) and check the caller's role against its domain (403
    if disallowed), returning the raw doc for the handler to use. One
    `Depends()` replaces the fetch/404/check_permission block that used to
    be repeated at the top of every mutating handler.

    Also 404s (not 403 — consistent with the "wrong household" case above,
    which already 404s rather than 403s to avoid confirming a record's
    existence to someone who can't see it) if the entity isn't shared with
    the caller. For `delete`, `check_permission` (owner-only, see
    `aria_auth.permissions.PERMISSIONS`) already runs first, so a member is
    rejected by that regardless of sharing — sharing governs view/edit,
    role governs delete.

    A trashed entity (`pending_delete_at` set) 404s here too, for every
    action except `undelete` — same "hidden everywhere but the trash view
    itself" invariant `get_entity`/`list_entities` already enforce, so a
    stale tab or client can't still update/archive/re-restore/re-delete an
    entity that's supposedly gone. `undelete` is the one action that must
    still be able to find it.
    """

    async def _require_entity(
        entity_id: str,
        session: SessionContext = Depends(get_current_session),
        db: AsyncIOMotorDatabase = Depends(get_db_dep),
    ) -> dict:
        doc = await db.entities.find_one({"_id": entity_id, "household_id": session.household_id})
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
        if action != "undelete" and doc.get("pending_delete_at") is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
        check_permission(session.role, doc["domain"], action)
        # .get(), not [] — an entity created before `shared_with` existed
        # has no such key stored at all; missing means "household", same
        # as the field's own Pydantic default.
        if not has_shared_access(session, doc.get("shared_with", "household"), doc["created_by"]):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
        return doc

    return _require_entity


async def _entity_create_body(body: EntityCreate) -> EntityCreate:
    return body


async def require_entity_create_permission(
    body: EntityCreate = Depends(_entity_create_body),
    session: SessionContext = Depends(get_current_session),
) -> None:
    check_permission(session.role, body.domain, "create")


@router.get("", response_model=list[EntityBase], response_model_by_alias=False)
async def list_entities(
    domain: EntityDomain | None = Query(default=None),
    include_archived: bool = Query(default=False),
    q: str | None = Query(default=None, min_length=1, max_length=200),
    tag: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=100, gt=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> list[EntityBase]:
    query: dict = {"household_id": session.household_id}
    if domain is not None:
        query["domain"] = domain
    if not include_archived:
        query["archived_at"] = None
    # Unconditional, unlike archived_at above — a trashed entity is a
    # terminal state pending permanent purge, not something any view opts
    # back into; it only ever surfaces through GET /entities/trash.
    query["pending_delete_at"] = None
    if tag:
        # Exact match (anchored), unlike `q`'s substring search — this backs
        # the tag-filter dropdown, which offers whole tag values to pick
        # from, not a type-ahead.
        query["tags"] = {"$regex": f"^{re.escape(tag)}$", "$options": "i"}
    if q:
        # Own $and key (not $or) so this composes with, rather than gets
        # overwritten by, the sharing $or assigned below — Mongo ANDs all
        # top-level filter keys, so "$and: [...], $or: [...]" means (search)
        # AND (sharing), not "the last one written wins."
        query["$and"] = [_search_filter(q)]
    if session.role != "owner":
        # Owner sees everything unfiltered (has_shared_access's own
        # owner-role branch, expressed as a query instead of a per-doc
        # check). Relies on MongoDB's standard scalar-or-array equality:
        # {"shared_with": session.user_id} matches a doc where the field is
        # an array *containing* that value — exactly the membership test
        # needed, no $in/$elemMatch required. The `$exists: False` clause
        # covers entities created before `shared_with` existed at all —
        # missing means "household", same as the field's own default;
        # without it, a pre-migration entity would silently vanish from
        # every non-owner's list (caught live against real migrated data).
        query["$or"] = [
            {"shared_with": "household"},
            {"shared_with": {"$exists": False}},
            {"shared_with": session.user_id},
            {"created_by": session.user_id},
        ]
    docs = (
        await db.entities.find(query)
        .skip(offset)
        .limit(limit)
        .to_list(length=limit)
    )
    return [EntityBase.model_validate(doc) for doc in docs]


class TagsPage(BaseModel):
    tags: list[str]
    has_more: bool


@router.get("/tags", response_model=TagsPage, response_model_by_alias=False)
async def list_entity_tags(
    q: str | None = Query(default=None, min_length=1, max_length=200),
    domain: EntityDomain | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, gt=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> TagsPage:
    """Distinct tag values across the household's entities, paginated and
    searchable — a household accumulates tags fast enough (a few hundred in
    normal use) that deriving filter options from a capped page of
    `GET /entities` results, as the frontend used to, silently hid tags
    outside that page. Declared ahead of `/{entity_id}` below so "tags"
    isn't swallowed as an entity id.
    """
    match: dict = {"household_id": session.household_id}
    if domain is not None:
        match["domain"] = domain
    if not include_archived:
        match["archived_at"] = None
    match["pending_delete_at"] = None
    if session.role != "owner":
        match["$or"] = [
            {"shared_with": "household"},
            {"shared_with": {"$exists": False}},
            {"shared_with": session.user_id},
            {"created_by": session.user_id},
        ]

    pipeline: list[dict] = [{"$match": match}, {"$unwind": "$tags"}]
    if q:
        pipeline.append({"$match": {"tags": {"$regex": re.escape(q.strip()), "$options": "i"}}})
    pipeline += [
        {"$group": {"_id": "$tags"}},
        {"$sort": {"_id": 1}},
        {"$skip": offset},
        # Fetch one extra to detect a next page without a separate count query.
        {"$limit": limit + 1},
    ]

    docs = await db.entities.aggregate(pipeline).to_list(length=limit + 1)
    tags = [doc["_id"] for doc in docs]
    return TagsPage(tags=tags[:limit], has_more=len(tags) > limit)


@router.get("/trash", response_model=list[EntityBase], response_model_by_alias=False)
async def list_trashed_entities(
    limit: int = Query(default=100, gt=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> list[EntityBase]:
    """Owner-only: entities currently in the trash grace period, most
    recently trashed first, so a "Recently Deleted" view can offer a
    restore action before `purge_expired_trash` (services/worker)
    permanently removes them. Declared ahead of `/{entity_id}` below, same
    reasoning as `/tags`.
    """
    check_permission(session.role, None, "undelete")
    docs = (
        await db.entities.find(
            {"household_id": session.household_id, "pending_delete_at": {"$ne": None}}
        )
        .sort("pending_delete_at", -1)
        .skip(offset)
        .limit(limit)
        .to_list(length=limit)
    )
    return [EntityBase.model_validate(doc) for doc in docs]


@router.get("/{entity_id}", response_model=EntityBase, response_model_by_alias=False)
async def get_entity(
    entity_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> EntityBase:
    doc = await db.entities.find_one({"_id": entity_id, "household_id": session.household_id})
    if doc is None or doc.get("pending_delete_at") is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
    if not has_shared_access(session, doc.get("shared_with", "household"), doc["created_by"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entity not found")
    return EntityBase.model_validate(doc)


@router.get("/{entity_id}/export.pdf")
async def export_entity_pdf(
    entity_id: str,
    include_documents: bool = Query(default=False),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> Response:
    """Bundles the entity's own fields, logs, schedules, and linked
    documents into a downloadable PDF via WeasyPrint, for warranty/resale-
    relevant record-keeping. Read-only, so gated by `require_entity_access`
    like the logs/schedules/documents list routes, not `require_entity()`
    (that factory is for mutating routes and needs a real `Action` to check
    against).

    Linked documents are always listed as metadata; their original files
    are only fetched and attached (images inline, PDFs merged in after
    rendering) when the caller opts in via `include_documents` — the
    frontend's ExportPdfModal is where that choice gets offered, since
    fetching every attachment is real extra work not worth doing by
    default."""
    await require_entity_access(db, session, entity_id)

    entity_doc = await db.entities.find_one({"_id": entity_id, "household_id": session.household_id})
    entity = EntityBase.model_validate(entity_doc)
    tz_name = await _household_timezone(db, session.household_id)

    log_docs = (
        await db.logs.find(
            {"entity_id": entity_id, "household_id": session.household_id, "pending_delete_at": None}
        )
        .sort("occurred_at", -1)
        .to_list(length=None)
    )
    logs = [LogEntry.model_validate(doc) for doc in log_docs]

    schedule_docs = await db.schedules.find(
        {"entity_id": entity_id, "household_id": session.household_id, "pending_delete_at": None}
    ).to_list(length=None)
    schedules = [Schedule.model_validate(doc) for doc in schedule_docs]

    document_docs = (
        await db.documents.find({"entity_ids": entity_id, "household_id": session.household_id})
        .sort("uploaded_at", -1)
        .to_list(length=None)
    )
    # Being able to see the entity doesn't automatically mean every document
    # attached to it is shared with you too — same check list_entity_documents
    # (documents.py) already applies.
    documents = [
        Document.model_validate(doc)
        for doc in document_docs
        if has_shared_access(session, doc.get("shared_with", "household"), doc["uploaded_by"])
    ]

    attribute_rows = [
        (key.replace("_", " ").title(), _format_attr_value(value))
        for key, value in entity.attributes.model_dump(exclude_none=True, exclude={"domain"}).items()
    ]

    attachment_images: list[dict] = []
    attachment_pdf_bytes: list[bytes] = []
    if include_documents:
        # Fetched concurrently — each is an independent S3 round trip, so
        # awaiting them one at a time would make export latency scale
        # linearly with attachment count for no reason.
        attachable_documents = [d for d in documents if d.mime_type in _ATTACHABLE_MIME_TYPES]
        contents = await asyncio.gather(*(_fetch_document_bytes(d) for d in attachable_documents))
        for document, content in zip(attachable_documents, contents):
            if content is None:
                continue
            if document.mime_type in _ATTACHABLE_IMAGE_MIME_TYPES:
                encoded = base64.b64encode(content).decode("ascii")
                attachment_images.append(
                    {
                        "filename": document.original_filename,
                        "data_uri": f"data:{document.mime_type};base64,{encoded}",
                    }
                )
            else:
                # Validated here, not just at merge time below — the note
                # text baked into the HTML render (next block) needs to know
                # the *true* attached count before that render happens, so a
                # malformed PDF can't be promised in the note and then
                # silently dropped by `_merge_pdfs`. pypdf parses the
                # trailer/xref eagerly but resolves the page tree lazily, so
                # constructing PdfReader alone doesn't surface a corrupt
                # page object — `len(...pages)` forces that walk now,
                # instead of leaving it for `_merge_pdfs` to discover later.
                try:
                    reader = PdfReader(BytesIO(content))
                    len(reader.pages)
                except Exception:
                    logger.warning(
                        "export: document %s is not a valid PDF, skipping attachment",
                        document.id,
                        exc_info=True,
                    )
                    continue
                attachment_pdf_bytes.append(content)

    attached_count = len(attachment_images) + len(attachment_pdf_bytes)
    if not documents:
        documents_note = ""
    elif include_documents and attached_count > 0:
        documents_note = "Original files are attached following this page."
        if attached_count < len(documents):
            documents_note += " Some files could not be attached and remain viewable in the app only."
    else:
        documents_note = "Full files remain viewable in the app; this export lists metadata only."

    context = {
        "entity": {
            "name": entity.name,
            "domain_label": entity.domain.replace("_", " ").title(),
            # Underscore-to-space only, not title-cased — matches
            # StatusBadge.tsx's own "needs attention" display convention.
            "status": entity.status.replace("_", " "),
            "location": entity.location,
            "tags": entity.tags,
            "created_at_local": to_household_date(entity.created_at, tz_name).isoformat(),
        },
        "attribute_rows": attribute_rows,
        "logs": [
            {
                "occurred_at": log.occurred_at.isoformat(),
                "title": log.title,
                "type": log.type,
                "cost": log.cost,
                "description": log.description,
            }
            for log in logs
        ],
        "schedules": [
            {
                "title": schedule.title,
                "interval_type": schedule.interval_type,
                "next_due_at": schedule.next_due_at.isoformat() if schedule.next_due_at else None,
                "active": schedule.active,
            }
            for schedule in schedules
        ],
        "documents": [
            {
                "original_filename": document.original_filename,
                "document_type": document.document_type,
                "uploaded_at_local": to_household_date(document.uploaded_at, tz_name).isoformat(),
                "size_label": _format_size(document.file_size_bytes),
            }
            for document in documents
        ],
        "documents_note": documents_note,
        "attachment_images": attachment_images,
        "generated_at": to_household_date(datetime.now(timezone.utc), tz_name).isoformat(),
    }

    html = _jinja_env.get_template("entity_export.html").render(**context)
    pdf_bytes = await run_in_threadpool(lambda: HTML(string=html).write_pdf())
    if attachment_pdf_bytes:
        pdf_bytes = await run_in_threadpool(_merge_pdfs, pdf_bytes, attachment_pdf_bytes)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(f"{entity.name}.pdf")},
    )


@router.post(
    "",
    response_model=EntityBase,
    status_code=status.HTTP_201_CREATED,
    response_model_by_alias=False,
    dependencies=[Depends(require_entity_create_permission)],
)
async def create_entity(
    body: EntityCreate = Depends(_entity_create_body),
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> EntityBase:
    await validate_shared_with(db, session.household_id, body.shared_with)

    now = datetime.now(timezone.utc)
    try:
        entity = EntityBase(
            id=new_id(),
            household_id=session.household_id,
            domain=body.domain,
            name=body.name,
            status=body.status,
            tags=body.tags,
            location=body.location,
            specs=body.specs,
            shared_with=body.shared_with,
            created_by=session.user_id,
            created_at=now,
            updated_at=now,
            archived_at=None,
            attributes=body.attributes,
        )
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await db.entities.insert_one(entity.to_mongo())
    return entity


@router.patch("/{entity_id}", response_model=EntityBase, response_model_by_alias=False)
async def update_entity(
    entity_id: str,
    body: EntityUpdate,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_entity("update")),
) -> EntityBase:
    current = EntityBase.model_validate(doc)

    if "shared_with" in body.model_fields_set:
        # Everyone with sharing access can edit a record's content, but
        # narrowing/widening *who else* can see it is reserved for whoever
        # created it (or the household owner) — otherwise any member with
        # edit access could unilaterally revoke every other member's
        # access, including the creator's.
        if session.role != "owner" and session.user_id != current.created_by:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "only the creator or household owner may change sharing"
            )
        await validate_shared_with(db, session.household_id, body.shared_with)

    merged_data = current.model_dump()
    merged_data.update(body.model_dump(exclude_unset=True))
    merged_data["updated_at"] = datetime.now(timezone.utc)

    try:
        merged = EntityBase.model_validate(merged_data)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await db.entities.replace_one({"_id": entity_id}, merged.to_mongo())
    return merged


@router.post("/{entity_id}/archive", response_model=EntityBase, response_model_by_alias=False)
async def archive_entity(
    entity_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_entity("archive")),
) -> EntityBase:
    now = datetime.now(timezone.utc)
    await db.entities.update_one({"_id": entity_id}, {"$set": {"archived_at": now, "updated_at": now}})
    doc["archived_at"] = now
    doc["updated_at"] = now
    return EntityBase.model_validate(doc)


@router.post("/{entity_id}/restore", response_model=EntityBase, response_model_by_alias=False)
async def restore_entity(
    entity_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_entity("restore")),
) -> EntityBase:
    now = datetime.now(timezone.utc)
    await db.entities.update_one({"_id": entity_id}, {"$set": {"archived_at": None, "updated_at": now}})
    doc["archived_at"] = None
    doc["updated_at"] = now
    return EntityBase.model_validate(doc)


class BulkEntityIds(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=MAX_LIMIT)


class BulkEntityResult(BaseModel):
    succeeded: list[str]
    not_found: list[str]
    forbidden: list[str]


async def _bulk_update_archived(
    body: BulkEntityIds,
    session: SessionContext,
    db: AsyncIOMotorDatabase,
    action: Action,
    archived_at: datetime | None,
) -> BulkEntityResult:
    """Shared batch implementation for bulk-archive/bulk-restore — one
    `find` + one `update_many` instead of looping N single-entity round
    trips, while still running the same three per-entity checks
    `require_entity()` runs for the single-item routes above (household
    scope, role/domain permission, sharing access) so a batch can't act on
    an entity the caller couldn't act on individually.
    """
    # pending_delete_at excluded unconditionally, same as require_entity()
    # above — bulk-archive/restore never covers undelete, so a trashed
    # entity should fall into not_found here exactly like a nonexistent one.
    docs = await db.entities.find(
        {"_id": {"$in": body.ids}, "household_id": session.household_id, "pending_delete_at": None}
    ).to_list(length=len(body.ids))
    docs_by_id = {doc["_id"]: doc for doc in docs}

    succeeded, not_found, forbidden = [], [], []
    for entity_id in body.ids:
        doc = docs_by_id.get(entity_id)
        if doc is None:
            not_found.append(entity_id)
            continue
        try:
            check_permission(session.role, doc["domain"], action)
        except HTTPException:
            forbidden.append(entity_id)
            continue
        if not has_shared_access(session, doc.get("shared_with", "household"), doc["created_by"]):
            # Same "404, not 403" treatment as require_entity() above — not
            # having sharing access looks identical to not existing.
            not_found.append(entity_id)
            continue
        succeeded.append(entity_id)

    if succeeded:
        now = datetime.now(timezone.utc)
        await db.entities.update_many(
            {"_id": {"$in": succeeded}},
            {"$set": {"archived_at": archived_at, "updated_at": now}},
        )

    return BulkEntityResult(succeeded=succeeded, not_found=not_found, forbidden=forbidden)


@router.post("/bulk-archive", response_model=BulkEntityResult)
async def bulk_archive_entities(
    body: BulkEntityIds,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> BulkEntityResult:
    return await _bulk_update_archived(body, session, db, "archive", datetime.now(timezone.utc))


@router.post("/bulk-restore", response_model=BulkEntityResult)
async def bulk_restore_entities(
    body: BulkEntityIds,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
) -> BulkEntityResult:
    return await _bulk_update_archived(body, session, db, "restore", None)


@router.post(
    "/{entity_id}/restore-from-trash", response_model=EntityBase, response_model_by_alias=False
)
async def restore_entity_from_trash(
    entity_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    doc: dict = Depends(require_entity("undelete")),
) -> EntityBase:
    now = datetime.now(timezone.utc)
    await db.entities.update_one(
        {"_id": entity_id}, {"$set": {"pending_delete_at": None, "updated_at": now}}
    )
    await db.logs.update_many(
        {"entity_id": entity_id, "household_id": session.household_id},
        {"$set": {"pending_delete_at": None}},
    )
    await db.schedules.update_many(
        {"entity_id": entity_id, "household_id": session.household_id},
        {"$set": {"pending_delete_at": None}},
    )
    doc["pending_delete_at"] = None
    doc["updated_at"] = now
    return EntityBase.model_validate(doc)


@router.delete("/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entity(
    entity_id: str,
    session: SessionContext = Depends(get_current_session),
    db: AsyncIOMotorDatabase = Depends(get_db_dep),
    _doc: dict = Depends(require_entity("delete")),
) -> Response:
    # Moves the entity, and every log/schedule cascade-linked to it, into a
    # grace-period trash state rather than deleting outright — restorable
    # via restore_entity_from_trash above until the worker's
    # purge_expired_trash task permanently removes it after
    # settings.entity_trash_grace_hours. Documents and pinned_entity_ids are
    # deliberately left untouched here: trashing is still reversible, same
    # reasoning archive already relies on ("archive does not unpin — an
    # archived entity is still validly pinnable"), so nothing that depends
    # on the entity existing should be cleaned up until the purge actually
    # happens.
    now = datetime.now(timezone.utc)
    await db.entities.update_one(
        {"_id": entity_id}, {"$set": {"pending_delete_at": now, "updated_at": now}}
    )
    await db.logs.update_many(
        {"entity_id": entity_id, "household_id": session.household_id},
        {"$set": {"pending_delete_at": now}},
    )
    await db.schedules.update_many(
        {"entity_id": entity_id, "household_id": session.household_id},
        {"$set": {"pending_delete_at": now}},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
