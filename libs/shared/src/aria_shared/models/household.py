from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field

from aria_shared.types import MongoBaseModel, PyObjectId

Role = Literal["owner", "member"]


class Household(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    name: str
    # Optional, collected at signup — used as the default location for
    # chat's weather tool (M10) when the query doesn't name a place.
    city: str | None = None
    created_at: datetime
    updated_at: datetime


class User(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    household_id: PyObjectId
    name: str
    email: EmailStr
    password_hash: str
    role: Role = "member"
    # Per-user UI theme (a `ThemeId` from the frontend's `THEMES` list, e.g.
    # "slate", "indigo" — kept as a plain string here rather than a Literal
    # so the frontend's palette can grow without a backend migration).
    # `None` means "no preference set yet," not the same as "slate" — the
    # frontend falls back to its own default in that case.
    theme: str | None = None
    created_at: datetime
