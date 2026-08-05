from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from aria_shared.models.entities import SharedWith
from aria_shared.types import MongoBaseModel, PyObjectId

DocumentType = Literal["manual", "receipt", "invoice", "photo", "diagram", "other"]
ProcessingStatus = Literal["pending", "ocr_complete", "chunked", "embedded", "failed"]


class Document(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    household_id: PyObjectId
    entity_ids: list[PyObjectId] = []
    log_ids: list[PyObjectId] = []
    document_type: DocumentType
    original_filename: str
    storage_path: str
    mime_type: str
    file_size_bytes: int
    page_count: int | None = None

    processing_status: ProcessingStatus = "pending"
    processing_error: str | None = None

    # Independent of any linked entity's own shared_with — a receipt
    # attached to a household-wide-shared entity can still be narrowed on
    # its own (data cost details are more sensitive than the entity itself).
    shared_with: SharedWith = "household"

    uploaded_by: PyObjectId
    uploaded_at: datetime


class DocumentDraftPage(BaseModel):
    id: PyObjectId
    storage_path: str
    # Not in the original plan sketch, but needed to set the right
    # Content-Type on both the S3 object and the page-file response —
    # DocumentDraftPage has no other field a draft's page-download endpoint
    # could read a mime type from, unlike Document.mime_type.
    mime_type: str


class DocumentDraft(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    household_id: PyObjectId
    entity_ids: list[PyObjectId]
    document_type: DocumentType
    shared_with: SharedWith
    created_by: PyObjectId
    created_at: datetime
    last_activity_at: datetime
    pages: list[DocumentDraftPage] = []
    # capturing -> finalizing -> finalized (or -> failed, retriable back to
    # finalizing). Finalize is asynchronous (PDF assembly runs in the
    # worker, not inline in the request) so this status is what a polling
    # client watches.
    status: Literal["capturing", "finalizing", "finalized", "failed"] = "capturing"
    resulting_document_id: PyObjectId | None = None
    finalize_error: str | None = None
