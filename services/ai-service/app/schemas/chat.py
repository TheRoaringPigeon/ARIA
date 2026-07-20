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
    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    page_number: int
    section_header: str | None = None
    entity_ids: list[str] = []
