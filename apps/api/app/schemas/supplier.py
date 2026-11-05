from datetime import datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Valid categories, tiers, and statuses aligned with database CHECK constraints
SupplierTier = Literal["tier_1", "tier_2", "tier_3"]
SupplierStatus = Literal[
    "draft",
    "invited",
    "onboarding",
    "under_review",
    "approved",
    "rejected",
    "archived",
]
SupplierComplianceStatus = Literal[
    "compliant", "pending", "action_required", "non_compliant", "expired"
]

GSTIN_REGEX = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
PAN_REGEX = r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"


class SupplierBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: Annotated[str, Field(min_length=1, max_length=255)]
    trade_name: Annotated[str | None, Field(default=None, max_length=255)] = None
    vendor_code: Annotated[str | None, Field(default=None, max_length=50)] = None
    category: Annotated[str, Field(default="raw_materials", max_length=100)] = (
        "raw_materials"
    )
    tier: SupplierTier = "tier_2"
    gstin: Annotated[str | None, Field(default=None, pattern=GSTIN_REGEX)] = None
    pan: Annotated[str | None, Field(default=None, pattern=PAN_REGEX)] = None
    primary_contact_name: Annotated[str | None, Field(default=None, max_length=255)] = (
        None
    )
    primary_contact_email: Annotated[
        str | None, Field(default=None, max_length=255)
    ] = None
    primary_contact_phone: Annotated[str | None, Field(default=None, max_length=20)] = (
        None
    )
    city: Annotated[str | None, Field(default=None, max_length=100)] = None
    state: Annotated[str | None, Field(default=None, max_length=100)] = None
    pincode: Annotated[str | None, Field(default=None, max_length=10)] = None
    notes: str | None = None


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: Annotated[
        str | None, Field(default=None, min_length=1, max_length=255)
    ] = None
    trade_name: Annotated[str | None, Field(default=None, max_length=255)] = None
    vendor_code: Annotated[str | None, Field(default=None, max_length=50)] = None
    category: Annotated[str | None, Field(default=None, max_length=100)] = None
    tier: SupplierTier | None = None
    gstin: Annotated[str | None, Field(default=None, pattern=GSTIN_REGEX)] = None
    pan: Annotated[str | None, Field(default=None, pattern=PAN_REGEX)] = None
    primary_contact_name: Annotated[str | None, Field(default=None, max_length=255)] = (
        None
    )
    primary_contact_email: Annotated[
        str | None, Field(default=None, max_length=255)
    ] = None
    primary_contact_phone: Annotated[str | None, Field(default=None, max_length=20)] = (
        None
    )
    city: Annotated[str | None, Field(default=None, max_length=100)] = None
    state: Annotated[str | None, Field(default=None, max_length=100)] = None
    pincode: Annotated[str | None, Field(default=None, max_length=10)] = None
    notes: str | None = None


class SupplierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    vendor_code: str | None
    legal_name: str
    trade_name: str | None
    category: str
    tier: str
    gstin: str | None
    pan: str | None
    status: str
    compliance_status: str
    primary_contact_name: str | None
    primary_contact_email: str | None
    primary_contact_phone: str | None
    city: str | None
    state: str | None
    pincode: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class SupplierListResponse(BaseModel):
    items: list[SupplierResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(
        cls,
        items: list[SupplierResponse],
        total: int,
        page: int,
        page_size: int,
    ) -> "SupplierListResponse":
        total_pages = ceil(total / page_size) if total > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
