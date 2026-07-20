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
