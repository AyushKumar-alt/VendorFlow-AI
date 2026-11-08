from datetime import datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CoverageScopeLiteral = Literal["supplier", "site"]


class SupplierDocumentGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type_id: UUID
    coverage_scope: CoverageScopeLiteral = "supplier"
    site_id: UUID | None = None
    document_reference: Annotated[str | None, Field(default=None, max_length=255)] = (
        None
    )
    display_name: Annotated[str | None, Field(default=None, max_length=255)] = None
    issuing_authority: Annotated[str | None, Field(default=None, max_length=255)] = None
    requirement_ids: list[UUID] | None = None

    @field_validator(
        "display_name", "document_reference", "issuing_authority", mode="after"
    )
    @classmethod
    def validate_optional_text_not_whitespace(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Text fields cannot be empty or whitespace-only.")
        return v.strip() if v is not None else None

    @model_validator(mode="after")
    def validate_scope_and_site(self) -> "SupplierDocumentGroupCreate":
        if self.coverage_scope == "supplier" and self.site_id is not None:
            raise ValueError(
                "site_id must be null for supplier-scoped document groups."
            )
        if self.coverage_scope == "site" and self.site_id is None:
            raise ValueError("site_id is required for site-scoped document groups.")
        return self


class SupplierDocumentGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    supplier_id: UUID
    document_type_id: UUID
    site_id: UUID | None
    display_name: str
    coverage_scope: str
    document_reference: str | None
    issuing_authority: str | None
    created_at: datetime
    updated_at: datetime


class SupplierDocumentGroupListResponse(BaseModel):
    items: list[SupplierDocumentGroupResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(
        cls,
        items: list[SupplierDocumentGroupResponse],
        total: int,
        page: int,
        page_size: int,
    ) -> "SupplierDocumentGroupListResponse":
        total_pages = ceil(total / page_size) if total > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
