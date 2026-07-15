from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field

from aria_shared.types import MongoBaseModel, PyObjectId


class Household(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    name: str
    created_at: datetime
    updated_at: datetime


class User(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    household_id: PyObjectId
    name: str
    email: EmailStr
    role: Literal["owner", "member"] = "member"
    created_at: datetime
