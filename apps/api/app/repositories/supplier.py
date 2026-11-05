from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier import Supplier


class SupplierRepository:
    """Repository handling all database operations for suppliers.

    CRITICAL ARCHITECTURAL RULE:
    Every query MUST explicitly filter by organization_id to enforce multi-tenant isolation.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(
        self, supplier_id: UUID, organization_id: UUID
    ) -> Supplier | None:
        """Fetch a single supplier by id strictly within the tenant boundary."""
        stmt = select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_vendor_code(
        self, vendor_code: str, organization_id: UUID
    ) -> Supplier | None:
        """Fetch a supplier by vendor code within the tenant boundary."""
        stmt = select(Supplier).where(
            Supplier.vendor_code == vendor_code,
            Supplier.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_gstin(self, gstin: str, organization_id: UUID) -> Supplier | None:
        """Fetch a supplier by GSTIN within the tenant boundary."""
        stmt = select(Supplier).where(
            Supplier.gstin == gstin,
            Supplier.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_suppliers(
        self,
        organization_id: UUID,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Supplier], int]:
        """Fetch a paginated list of suppliers within the tenant boundary with optional filtering."""
        base_query = select(Supplier).where(Supplier.organization_id == organization_id)

        if status:
            base_query = base_query.where(Supplier.status == status)

        if search and search.strip():
            term = f"%{search.strip()}%"
            base_query = base_query.where(
                or_(
                    Supplier.legal_name.ilike(term),
                    Supplier.trade_name.ilike(term),
                    Supplier.vendor_code.ilike(term),
                    Supplier.gstin.ilike(term),
                    Supplier.pan.ilike(term),
                )
            )

        # Count total matching rows
        count_stmt = select(func.count()).select_from(base_query.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar_one()

        # Query paginated rows ordered by creation timestamp
        offset = (page - 1) * page_size
        paginated_stmt = (
            base_query.order_by(Supplier.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self.db.execute(paginated_stmt)
        items = list(result.scalars().all())

        return items, total

    async def create(self, supplier: Supplier) -> Supplier:
        """Add a new supplier to the session and flush to generate ID and defaults."""
        self.db.add(supplier)
        await self.db.flush()
        return supplier
