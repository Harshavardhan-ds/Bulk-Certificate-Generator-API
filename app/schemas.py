from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.config import MAX_RECIPIENTS_PER_JOB


class Recipient(BaseModel):
    """Validated per recipient inside the service, so one bad row doesn't reject the whole job."""

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("name cannot be blank")
        return v


class JobCreate(BaseModel):
    event_name: str = Field(min_length=1, max_length=200)
    title: str = Field(default="Certificate of Completion", min_length=1, max_length=200)
    issued_by: str = Field(min_length=1, max_length=200)
    issue_date: date | None = None
    # kept loose on purpose: each item is checked individually later
    recipients: list[Any] = Field(min_length=1, max_length=MAX_RECIPIENTS_PER_JOB)


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    certificate_number: str | None
    status: str
    error: str | None
    download_url: str | None = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_name: str
    title: str
    issued_by: str
    issue_date: date
    status: str
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    created_at: datetime
    completed_at: datetime | None
    download_all_url: str | None = None


class JobDetail(JobOut):
    failures: list[CertificateOut] = []


class CertificateList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]
