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
    created_at: datetime
