from datetime import datetime
from typing import Literal

from pydantic import Field

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

    uploaded_by: PyObjectId
    uploaded_at: datetime
