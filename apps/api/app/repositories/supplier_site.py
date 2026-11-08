from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier_site import SupplierSite


class SupplierSiteRepository:
    """Repository handling all database operations for supplier physical/operational sites.

    CRITICAL ARCHITECTURAL RULE:
    Every query MUST explicitly filter by organization_id to enforce multi-tenant isolation.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(
        self,
        site_id: UUID,
        organization_id: UUID,
        supplier_id: UUID | None = None,
    ) -> SupplierSite | None:
        """Fetch a single supplier site by id strictly within the tenant boundary, optionally scoped to a supplier."""
        stmt = select(SupplierSite).where(
            SupplierSite.id == site_id,
            SupplierSite.organization_id == organization_id,
        )
        if supplier_id is not None:
            stmt = stmt.where(SupplierSite.supplier_id == supplier_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_code(
        self, supplier_id: UUID, site_code: str, organization_id: UUID
    ) -> SupplierSite | None:
        """Fetch a site by site code for a supplier strictly within the tenant boundary."""
        stmt = select(SupplierSite).where(
            SupplierSite.supplier_id == supplier_id,
            SupplierSite.site_code == site_code,
            SupplierSite.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_supplier(
        self,
        supplier_id: UUID,
        organization_id: UUID,
        is_active: bool | None = None,
    ) -> list[SupplierSite]:
        """Fetch all sites for a supplier within the tenant boundary."""
        stmt = select(SupplierSite).where(
            SupplierSite.supplier_id == supplier_id,
            SupplierSite.organization_id == organization_id,
        )
        if is_active is not None:
            stmt = stmt.where(SupplierSite.is_active == is_active)

        stmt = stmt.order_by(SupplierSite.site_name.asc(), SupplierSite.id.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, site: SupplierSite) -> SupplierSite:
        """Add a new supplier site and flush."""
        self.db.add(site)
        await self.db.flush()
        return site

    async def update(self, site: SupplierSite) -> SupplierSite:
        """Flush changes to an existing site."""
        await self.db.flush()
        return site
