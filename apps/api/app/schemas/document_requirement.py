from datetime import datetime
from math import ceil
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.document_type import DocumentTypeResponse


class DocumentRequirementBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)]
    supplier_category: Annotated[str | None, Field(default=None, max_length=100)] = None
    supplier_tier: Annotated[str | None, Field(default=None, max_length=20)] = None
    is_mandatory: bool = True
    validity_period_days: Annotated[int | None, Field(default=None, gt=0)] = None
    expiry_lead_notice_days: Annotated[int, Field(default=30, ge=0)] = 30
    is_active: bool = True
    validation_rules: dict[str, Any] = Field(default_factory=dict)


class DocumentRequirementCreate(DocumentRequirementBase):
    document_type_id: UUID


class DocumentRequirementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(default=None, min_length=1, max_length=255)] = (
        None
    )
    supplier_category: Annotated[str | None, Field(default=None, max_length=100)] = None
    supplier_tier: Annotated[str | None, Field(default=None, max_length=20)] = None
    is_mandatory: bool | None = None
    validity_period_days: Annotated[int | None, Field(default=None, gt=0)] = None
    expiry_lead_notice_days: Annotated[int | None, Field(default=None, ge=0)] = None
    is_active: bool | None = None
    validation_rules: dict[str, Any] | None = None


class DocumentRequirementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    document_type_id: UUID
    name: str
    supplier_category: str | None
    supplier_tier: str | None
    is_mandatory: bool
    validity_period_days: int | None
    expiry_lead_notice_days: int
    is_active: bool
    validation_rules: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    document_type: DocumentTypeResponse | None = None


class DocumentRequirementListResponse(BaseModel):
    items: list[DocumentRequirementResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(
        cls,
        items: list[DocumentRequirementResponse],
        total: int,
        page: int,
        page_size: int,
    ) -> "DocumentRequirementListResponse":
        total_pages = ceil(total / page_size) if page_size > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )


class SupplierRequirementItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    requirement_id: UUID
    document_type_id: UUID
    name: str
    is_mandatory: bool
    validity_period_days: int | None
    expiry_lead_notice_days: int
    supplier_category: str | None = None
    supplier_tier: str | None = None
    validation_rules: dict[str, Any] = Field(default_factory=dict)
    document_type_code: str
    document_type_name: str
    document_type_category: str
    has_expiry: bool
    expected_fields: list[Any] = Field(default_factory=list)


class SupplierRequirementsEvaluationResponse(BaseModel):
    supplier_id: UUID
    supplier_name: str
    supplier_category: str
    supplier_tier: str
    total_applicable: int
    total_mandatory: int
    total_optional: int
    mandatory_requirements: list[SupplierRequirementItem]
    optional_requirements: list[SupplierRequirementItem]
