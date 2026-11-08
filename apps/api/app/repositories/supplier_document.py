from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier_document import SupplierDocument


class SupplierDocumentRepository:
    """Repository handling all database operations for supplier documents and version lineage.

    CRITICAL ARCHITECTURAL RULE:
    Every query MUST explicitly filter by organization_id to enforce multi-tenant isolation.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(
        self,
        document_id: UUID,
        organization_id: UUID,
        supplier_id: UUID | None = None,
    ) -> SupplierDocument | None:
        """Fetch a single supplier document by id strictly within the tenant boundary, optionally scoped to a supplier."""
        stmt = select(SupplierDocument).where(
            SupplierDocument.id == document_id,
            SupplierDocument.organization_id == organization_id,
        )
        if supplier_id is not None:
            stmt = stmt.where(SupplierDocument.supplier_id == supplier_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_effective_version(
        self, document_group_id: UUID, organization_id: UUID
    ) -> SupplierDocument | None:
        """Fetch the currently effective document version for a group within the tenant boundary."""
        stmt = select(SupplierDocument).where(
            SupplierDocument.document_group_id == document_group_id,
            SupplierDocument.organization_id == organization_id,
            SupplierDocument.is_effective_version.is_(True),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_group_and_version(
        self, document_group_id: UUID, version: int, organization_id: UUID
    ) -> SupplierDocument | None:
        """Fetch a document by its group and explicit version number within the tenant boundary."""
        stmt = select(SupplierDocument).where(
            SupplierDocument.document_group_id == document_group_id,
            SupplierDocument.version == version,
            SupplierDocument.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_version_in_group(
        self, document_group_id: UUID, organization_id: UUID
    ) -> SupplierDocument | None:
        """Fetch the highest-numbered version document in a group within the tenant boundary."""
        stmt = (
            select(SupplierDocument)
            .where(
                SupplierDocument.document_group_id == document_group_id,
                SupplierDocument.organization_id == organization_id,
            )
            .order_by(SupplierDocument.version.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_group(
        self, document_group_id: UUID, organization_id: UUID
    ) -> list[SupplierDocument]:
        """Fetch all version documents belonging to a group, ordered by version asc."""
        stmt = (
            select(SupplierDocument)
            .where(
                SupplierDocument.document_group_id == document_group_id,
                SupplierDocument.organization_id == organization_id,
            )
            .order_by(SupplierDocument.version.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_child_versions(
        self, previous_version_id: UUID, organization_id: UUID
    ) -> list[SupplierDocument]:
        """Fetch all direct child versions that reference previous_version_id."""
        stmt = select(SupplierDocument).where(
            SupplierDocument.previous_version_id == previous_version_id,
            SupplierDocument.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, document: SupplierDocument) -> SupplierDocument:
        """Add a new supplier document and flush."""
        self.db.add(document)
        await self.db.flush()
        return document

    async def update(self, document: SupplierDocument) -> SupplierDocument:
        """Flush changes to an existing supplier document."""
        await self.db.flush()
        return document
