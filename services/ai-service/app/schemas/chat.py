from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatResume(BaseModel):
    """Resumes a graph run paused at `execute_action_node`'s interrupt —
    the one narrow exception to the no-`conversation_id` contract: `thread_id`
    is reused across exactly the propose request and this one, not kept
    across the whole conversation.
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    decision: Literal["confirm", "reject"]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # No `min_length=1` here (unlike pre-M8) — a resume request carries no
    # real message history, since the graph's own state is already
    # checkpointed under `resume.thread_id`. `messages` must still be
    # non-empty whenever `resume` isn't set, enforced below. That
    # conditional constraint can't be expressed as a plain field
    # constraint, so it no longer shows up as `minItems` in the generated
    # OpenAPI schema the way it did pre-M8 — documented here instead so a
    # schema consumer (codegen, contract tests) can at least read it
    # (caught in code review).
    messages: list[ChatMessage] = Field(
        default=[],
        description=(
            "Must be non-empty unless `resume` is set — a resume request "
            "carries no message history, since the graph's own state is "
            "already checkpointed under `resume.thread_id`."
        ),
    )
    resume: ChatResume | None = None

    @model_validator(mode="after")
    def _check_messages_or_resume(self) -> "ChatRequest":
        if self.resume is None and not self.messages:
            raise ValueError("messages must be non-empty unless resume is set")
        return self


class Citation(BaseModel):
    """`source_type="document"` (the default, M5-era shape) keeps
    `document_id`/`filename`/`page_number`/`section_header`/`entity_ids`
    populated as before. `source_type="web"` (M10 — Research Assistant's
    `search_web`/`get_weather` tools) instead populates `url`/`title`/
    `snippet` and leaves the document-only fields `None` — one extended
    type rather than a second parallel citation shape, so the existing
    `citations` SSE frame and `build_system_prompt()` don't need a
    frame-shape change to carry both kinds.
    """

    model_config = ConfigDict(extra="forbid")

    source_type: Literal["document", "web"] = "document"
    document_id: str | None = None
    filename: str | None = None
    page_number: int | None = None
    section_header: str | None = None
    entity_ids: list[str] = []
    url: str | None = None
    title: str | None = None
    snippet: str | None = None

    @model_validator(mode="after")
    def _require_fields_for_source_type(self) -> "Citation":
        """Nothing else enforces the invariant the docstring above
        describes — without this, a document citation missing
        `document_id`/`filename`/`page_number` (or a web citation missing
        `url`/`title`) would pass validation silently and only surface as
        a broken download link / "p.None" downstream (caught in code
        review).
        """
        if self.source_type == "document":
            if self.document_id is None or self.filename is None or self.page_number is None:
                raise ValueError(
                    "document citations require document_id, filename, and page_number"
                )
        else:
            if self.url is None or self.title is None:
                raise ValueError("web citations require url and title")
        return self
