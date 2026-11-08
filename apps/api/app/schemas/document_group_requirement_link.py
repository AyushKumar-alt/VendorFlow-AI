from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

LinkOriginLiteral = Literal["manual"]


class DocumentGroupRequirementLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: UUID
    link_origin: LinkOriginLiteral = "manual"


class DocumentGroupRequirementLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    document_group_id: UUID
    requirement_id: UUID
    link_origin: str
    linked_at: datetime


class DocumentGroupRequirementLinkListResponse(BaseModel):
    items: list[DocumentGroupRequirementLinkResponse]
    total: int
