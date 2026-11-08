from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_group_requirement_link import (
    DocumentGroupRequirementLink,
)


class DocumentGroupRequirementLinkRepository:
    """Repository handling all database operations for document group requirement links.

    CRITICAL ARCHITECTURAL RULE:
    Every query MUST explicitly filter by organization_id to enforce multi-tenant isolation.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(
        self, link_id: UUID, organization_id: UUID
    ) -> DocumentGroupRequirementLink | None:
        """Fetch a single requirement link by id strictly within the tenant boundary."""
        stmt = select(DocumentGroupRequirementLink).where(
            DocumentGroupRequirementLink.id == link_id,
            DocumentGroupRequirementLink.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_group_and_requirement(
        self,
        document_group_id: UUID,
        requirement_id: UUID,
        organization_id: UUID,
    ) -> DocumentGroupRequirementLink | None:
        """Fetch a link for a specific group and requirement within the tenant boundary."""
        stmt = select(DocumentGroupRequirementLink).where(
            DocumentGroupRequirementLink.document_group_id == document_group_id,
            DocumentGroupRequirementLink.requirement_id == requirement_id,
            DocumentGroupRequirementLink.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_group(
        self, document_group_id: UUID, organization_id: UUID
    ) -> list[DocumentGroupRequirementLink]:
        """Fetch all requirement links for a document group within the tenant boundary."""
        stmt = (
            select(DocumentGroupRequirementLink)
            .where(
                DocumentGroupRequirementLink.document_group_id == document_group_id,
                DocumentGroupRequirementLink.organization_id == organization_id,
            )
            .order_by(DocumentGroupRequirementLink.linked_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self, link: DocumentGroupRequirementLink
    ) -> DocumentGroupRequirementLink:
        """Add a new requirement link and flush."""
        self.db.add(link)
        await self.db.flush()
        return link

    async def delete(self, link: DocumentGroupRequirementLink) -> None:
        """Delete an existing requirement link and flush."""
        await self.db.delete(link)
        await self.db.flush()
