from datetime import date
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class RegistrationRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: str = Field(..., min_length=1, max_length=120)
    wedding_date: date
    venue: str = Field(..., min_length=1, max_length=255)
    guest_count: int = Field(..., ge=1, le=10000)
    email: EmailStr
    phone: str = Field(..., min_length=3, max_length=80)
    message: Optional[str] = Field(default=None, max_length=5000)


class StatusUpdate(BaseModel):
    status: str
