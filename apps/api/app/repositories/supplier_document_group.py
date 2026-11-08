from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier_document_group import SupplierDocumentGroup


class SupplierDocumentGroupRepository:
    """Repository handling all database operations for supplier document groups.

    CRITICAL ARCHITECTURAL RULE:
    Every query MUST explicitly filter by organization_id to enforce multi-tenant isolation.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(
        self,
        group_id: UUID,
        organization_id: UUID,
        supplier_id: UUID | None = None,
    ) -> SupplierDocumentGroup | None:
        """Fetch a single document group by id strictly within the tenant boundary, optionally scoped to a supplier."""
        stmt = select(SupplierDocumentGroup).where(
            SupplierDocumentGroup.id == group_id,
            SupplierDocumentGroup.organization_id == organization_id,
        )
        if supplier_id is not None:
            stmt = stmt.where(SupplierDocumentGroup.supplier_id == supplier_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_for_update(
        self,
        group_id: UUID,
        organization_id: UUID,
        supplier_id: UUID | None = None,
    ) -> SupplierDocumentGroup | None:
        """Fetch a single document group with an exclusive row lock within the tenant boundary, optionally scoped to a supplier."""
        stmt = select(SupplierDocumentGroup).where(
            SupplierDocumentGroup.id == group_id,
            SupplierDocumentGroup.organization_id == organization_id,
        )
        if supplier_id is not None:
            stmt = stmt.where(SupplierDocumentGroup.supplier_id == supplier_id)
        stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_site_ref(
        self,
        supplier_id: UUID,
        document_type_id: UUID,
        site_id: UUID,
        document_reference: str,
        organization_id: UUID,
    ) -> SupplierDocumentGroup | None:
        """Fetch a site-scoped group by document reference within the tenant boundary."""
        stmt = select(SupplierDocumentGroup).where(
            SupplierDocumentGroup.organization_id == organization_id,
            SupplierDocumentGroup.supplier_id == supplier_id,
            SupplierDocumentGroup.document_type_id == document_type_id,
            SupplierDocumentGroup.site_id == site_id,
            SupplierDocumentGroup.document_reference == document_reference,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_supplier_ref(
        self,
        supplier_id: UUID,
        document_type_id: UUID,
        document_reference: str,
        organization_id: UUID,
    ) -> SupplierDocumentGroup | None:
        """Fetch a supplier-scoped group by document reference within the tenant boundary."""
        stmt = select(SupplierDocumentGroup).where(
            SupplierDocumentGroup.organization_id == organization_id,
            SupplierDocumentGroup.supplier_id == supplier_id,
            SupplierDocumentGroup.document_type_id == document_type_id,
            SupplierDocumentGroup.site_id.is_(None),
            SupplierDocumentGroup.document_reference == document_reference,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_supplier(
        self,
        supplier_id: UUID,
        organization_id: UUID,
        document_type_id: UUID | None = None,
        site_id: UUID | None = None,
    ) -> list[SupplierDocumentGroup]:
        """Fetch document groups for a supplier within the tenant boundary."""
        stmt = select(SupplierDocumentGroup).where(
            SupplierDocumentGroup.supplier_id == supplier_id,
            SupplierDocumentGroup.organization_id == organization_id,
        )
        if document_type_id is not None:
            stmt = stmt.where(
                SupplierDocumentGroup.document_type_id == document_type_id
            )
        if site_id is not None:
            stmt = stmt.where(SupplierDocumentGroup.site_id == site_id)

        stmt = stmt.order_by(
            SupplierDocumentGroup.created_at.desc(),
            SupplierDocumentGroup.id.desc(),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_supplier_paginated(
        self,
        supplier_id: UUID,
        organization_id: UUID,
        document_type_id: UUID | None = None,
        site_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SupplierDocumentGroup], int]:
        """Fetch paginated document groups for a supplier within the tenant boundary."""
        base_conditions = [
            SupplierDocumentGroup.supplier_id == supplier_id,
            SupplierDocumentGroup.organization_id == organization_id,
        ]
        if document_type_id is not None:
            base_conditions.append(
                SupplierDocumentGroup.document_type_id == document_type_id
            )
        if site_id is not None:
            base_conditions.append(SupplierDocumentGroup.site_id == site_id)

        count_stmt = (
            select(func.count())
            .select_from(SupplierDocumentGroup)
            .where(*base_conditions)
        )
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar_one()

        if total == 0:
            return [], 0

        offset = max(0, (page - 1) * page_size)
        stmt = (
            select(SupplierDocumentGroup)
            .where(*base_conditions)
            .order_by(
                SupplierDocumentGroup.created_at.desc(),
                SupplierDocumentGroup.id.desc(),
            )
            .offset(offset)
            .limit(page_size)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total

    async def create(self, group: SupplierDocumentGroup) -> SupplierDocumentGroup:
        """Add a new document group and flush."""
        self.db.add(group)
        await self.db.flush()
        return group

    async def update(self, group: SupplierDocumentGroup) -> SupplierDocumentGroup:
        """Flush changes to an existing document group."""
        await self.db.flush()
        return group
