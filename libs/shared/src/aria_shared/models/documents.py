from datetime import datetime
from typing import Literal

from pydantic import Field

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
