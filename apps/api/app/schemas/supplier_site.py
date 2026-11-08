from datetime import datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GSTIN_REGEX = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
COUNTRY_CODE_REGEX = r"^[A-Z]{2}$"
SITE_CODE_REGEX = r"^[A-Za-z0-9_-]+$"

NON_NULLABLE_UPDATE_FIELDS = {"site_name", "country_code", "is_active"}
CLEARABLE_NULLABLE_FIELDS = {
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
    "gstin",
}


class SupplierSiteBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_name: Annotated[str, Field(min_length=1, max_length=255)]
    address_line1: Annotated[str | None, Field(default=None, max_length=255)] = None
    address_line2: Annotated[str | None, Field(default=None, max_length=255)] = None
    city: Annotated[str | None, Field(default=None, max_length=100)] = None
    state: Annotated[str | None, Field(default=None, max_length=100)] = None
    postal_code: Annotated[str | None, Field(default=None, max_length=20)] = None
    country_code: Annotated[str, Field(default="IN", pattern=COUNTRY_CODE_REGEX)] = "IN"
    gstin: Annotated[str | None, Field(default=None, pattern=GSTIN_REGEX)] = None

    @field_validator("site_name", mode="after")
    @classmethod
    def validate_site_name_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field 'site_name' cannot be empty or whitespace-only.")
        return v


class SupplierSiteCreate(SupplierSiteBase):
    site_code: Annotated[
        str, Field(min_length=1, max_length=50, pattern=SITE_CODE_REGEX)
    ]


class SupplierSiteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_name: Annotated[
        str | None, Field(default=None, min_length=1, max_length=255)
    ] = None
    address_line1: Annotated[str | None, Field(default=None, max_length=255)] = None
    address_line2: Annotated[str | None, Field(default=None, max_length=255)] = None
    city: Annotated[str | None, Field(default=None, max_length=100)] = None
    state: Annotated[str | None, Field(default=None, max_length=100)] = None
    postal_code: Annotated[str | None, Field(default=None, max_length=20)] = None
    country_code: Annotated[
        str | None, Field(default=None, pattern=COUNTRY_CODE_REGEX)
    ] = None
    gstin: Annotated[str | None, Field(default=None, pattern=GSTIN_REGEX)] = None
    is_active: bool | None = None

    @field_validator("site_name", mode="after")
    @classmethod
    def validate_site_name_not_whitespace(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Field 'site_name' cannot be empty or whitespace-only.")
        return v

    @model_validator(mode="after")
    def validate_explicit_nulls(self) -> "SupplierSiteUpdate":
        for field in NON_NULLABLE_UPDATE_FIELDS:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(
                    f"Field '{field}' is non-nullable and cannot be set to null."
                )
        return self


class SupplierSiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    supplier_id: UUID
    site_code: str
    site_name: str
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country_code: str
    gstin: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SupplierSiteListResponse(BaseModel):
    items: list[SupplierSiteResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(
        cls,
        items: list[SupplierSiteResponse],
        total: int,
        page: int,
        page_size: int,
    ) -> "SupplierSiteListResponse":
        total_pages = ceil(total / page_size) if total > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
