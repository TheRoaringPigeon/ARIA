from pydantic import BaseModel, ConfigDict, field_validator

from aria_shared.models.documents import DocumentType

# The OCR stage only knows how to handle PDF and common image formats, so
# uploads outside this set are rejected up front (400) rather than accepted
# and failed asynchronously in the pipeline.
ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}


class DocumentUploadMeta(BaseModel):
    """Multipart form fields don't map cleanly to a single Pydantic body
    model in FastAPI — the router declares `File`/`Form` params directly
    and assembles this DTO from them for validation.
    """

    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType
    entity_ids: list[str]
    # A plain list, not `SharedWith`'s `Literal["household"] | list[str]` —
    # multipart form fields don't carry that union cleanly. An empty list
    # (the default, since a `Form()` field can't default to the string
    # `"household"` and a list at the same time) means "shared with the
    # whole household"; the router converts `[] -> "household"` before
    # constructing the `Document`, same shape `SharedWith` expects.
    shared_with: list[str] = []

    @field_validator("entity_ids")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("entity_ids must include at least one entity")
        return value


class DocumentDraftCreateMeta(BaseModel):
    """Body of POST /documents/drafts — JSON, not multipart (no file yet;
    pages are uploaded one at a time after the draft exists)."""

    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType
    entity_ids: list[str]
    shared_with: str | list[str] = "household"
    # Becomes the resulting Document's original_filename (see
    # finalize_document_draft.py); None/blank falls back to the default
    # "mobile-scan.pdf" name.
    name: str | None = None

    @field_validator("entity_ids")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("entity_ids must include at least one entity")
        return value

    @field_validator("name")
    @classmethod
    def _blank_name_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > 200:
            raise ValueError("name must be 200 characters or fewer")
        return stripped


class DraftPageReorderMeta(BaseModel):
    """Body of PATCH /documents/drafts/{draft_id}/pages/reorder — the full
    list of the draft's page ids in the desired order."""

    model_config = ConfigDict(extra="forbid")

    page_ids: list[str]


class DocumentRenameMeta(BaseModel):
    """Body of PATCH /documents/{document_id} — renames a document's
    original_filename (the display name and the name it downloads as).
    Does not touch storage_path; the underlying S3 object key is unaffected."""

    model_config = ConfigDict(extra="forbid")

    original_filename: str

    @field_validator("original_filename")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("original_filename must not be blank")
        if len(stripped) > 200:
            raise ValueError("original_filename must be 200 characters or fewer")
        return stripped
