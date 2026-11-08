from datetime import datetime
from math import ceil
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DocumentTypeCategory = Literal[
    "statutory", "tax", "quality", "financial", "legal", "safety"
]

CODE_REGEX = r"^[a-z0-9_-]{2,50}$"


class DocumentTypeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str | None, Field(default=None)] = None
    category: DocumentTypeCategory = "statutory"
    has_expiry: bool = False
    expected_fields: list[Any] = Field(default_factory=list)


class DocumentTypeCreate(DocumentTypeBase):
    code: Annotated[str, Field(min_length=2, max_length=50, pattern=CODE_REGEX)]


class DocumentTypeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(default=None, min_length=1, max_length=255)] = (
        None
    )
    description: Annotated[str | None, Field(default=None)] = None
    category: DocumentTypeCategory | None = None
    has_expiry: bool | None = None
    expected_fields: list[Any] | None = None


class DocumentTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    code: str
    name: str
    description: str | None
    category: str
    has_expiry: bool
    is_system_preset: bool
    expected_fields: list[Any]
    created_at: datetime
    updated_at: datetime


class DocumentTypeListResponse(BaseModel):
    items: list[DocumentTypeResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(
        cls,
        items: list[DocumentTypeResponse],
        total: int,
        page: int,
        page_size: int,
    ) -> "DocumentTypeListResponse":
        total_pages = ceil(total / page_size) if page_size > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
