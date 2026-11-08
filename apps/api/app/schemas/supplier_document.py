from datetime import date, datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SupplierDocumentResponse(BaseModel):
    """Public representation of a supplier document version.

    SECURITY INVARIANT:
    Internal storage identifiers and filesystem paths (file_path) are strictly
    excluded from this response model.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    supplier_id: UUID
    document_type_id: UUID
    document_group_id: UUID
    version: int
    previous_version_id: UUID | None
    is_effective_version: bool
    processing_status: str
    legacy_status: str | None
    approval_provenance: str | None
    file_name: str
    file_size_bytes: int
    file_mime_type: str
    file_hash_sha256: str
    issued_date: date | None
    expiry_date: date | None
    created_at: datetime
    updated_at: datetime


class SupplierDocumentListResponse(BaseModel):
    items: list[SupplierDocumentResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(
        cls,
        items: list[SupplierDocumentResponse],
        total: int,
        page: int,
        page_size: int,
    ) -> "SupplierDocumentListResponse":
        total_pages = ceil(total / page_size) if total > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )


class EffectiveDocumentRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, Field(min_length=3, max_length=1000)]

    @field_validator("reason", mode="after")
    @classmethod
    def validate_reason_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Revocation reason cannot be empty or whitespace-only.")
        return v.strip()
