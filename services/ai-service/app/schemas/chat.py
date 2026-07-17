from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1)


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    page_number: int
    section_header: str | None = None
    entity_ids: list[str] = []
