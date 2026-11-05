from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.document_requirement import DocumentRequirement


class DocumentRequirementRepository:
    """Repository handling database operations for document requirements.

    CRITICAL ARCHITECTURAL RULE:
    Every query MUST explicitly filter by organization_id to enforce multi-tenant isolation.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(
        self, requirement_id: UUID, organization_id: UUID
    ) -> DocumentRequirement | None:
        """Fetch a single document requirement strictly within the tenant boundary."""
        stmt = (
            select(DocumentRequirement)
            .options(selectinload(DocumentRequirement.document_type))
            .where(
                DocumentRequirement.id == requirement_id,
                DocumentRequirement.organization_id == organization_id,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def find_existing(
        self,
        organization_id: UUID,
        document_type_id: UUID,
        supplier_category: str | None,
        supplier_tier: str | None,
    ) -> DocumentRequirement | None:
        """Find an existing requirement with matching document_type, category, and tier within the tenant."""
        stmt = select(DocumentRequirement).where(
            DocumentRequirement.organization_id == organization_id,
            DocumentRequirement.document_type_id == document_type_id,
        )

        if supplier_category is None:
            stmt = stmt.where(DocumentRequirement.supplier_category.is_(None))
        else:
            stmt = stmt.where(
                DocumentRequirement.supplier_category == supplier_category
            )

        if supplier_tier is None:
            stmt = stmt.where(DocumentRequirement.supplier_tier.is_(None))
        else:
            stmt = stmt.where(DocumentRequirement.supplier_tier == supplier_tier)

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_requirements(
        self,
        organization_id: UUID,
        document_type_id: UUID | None = None,
        supplier_category: str | None = None,
        supplier_tier: str | None = None,
        is_active: bool | None = None,
        is_mandatory: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[DocumentRequirement], int]:
        """Fetch a paginated list of document requirements within the tenant boundary."""
        base_query = select(DocumentRequirement).where(
            DocumentRequirement.organization_id == organization_id
        )

        if document_type_id is not None:
            base_query = base_query.where(
                DocumentRequirement.document_type_id == document_type_id
            )

        if supplier_category is not None:
            base_query = base_query.where(
                DocumentRequirement.supplier_category == supplier_category
            )

        if supplier_tier is not None:
            base_query = base_query.where(
                DocumentRequirement.supplier_tier == supplier_tier
            )

        if is_active is not None:
            base_query = base_query.where(DocumentRequirement.is_active == is_active)

        if is_mandatory is not None:
            base_query = base_query.where(
                DocumentRequirement.is_mandatory == is_mandatory
            )

        # Count total matching records
        count_stmt = select(func.count()).select_from(base_query.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar_one()

        # Apply ordering and pagination
        offset = (page - 1) * page_size
        paginated_query = (
            base_query.options(selectinload(DocumentRequirement.document_type))
            .order_by(
                DocumentRequirement.is_mandatory.desc(),
                DocumentRequirement.name.asc(),
            )
            .offset(offset)
            .limit(page_size)
        )
        result = await self.db.execute(paginated_query)
        items = list(result.scalars().all())

        return items, total

    async def get_applicable_for_supplier(
        self,
        organization_id: UUID,
        supplier_category: str,
        supplier_tier: str,
    ) -> list[DocumentRequirement]:
        """Fetch all active requirements applicable to a supplier's category and tier within the tenant.

        Excluded:
        - Inactive/disabled requirements (is_active == False).
        - Requirements belonging to another organization.
        - Requirements scoped to other categories or other tiers.
        """
        stmt = (
            select(DocumentRequirement)
            .options(selectinload(DocumentRequirement.document_type))
            .where(
                DocumentRequirement.organization_id == organization_id,
                DocumentRequirement.is_active.is_(True),
                or_(
                    DocumentRequirement.supplier_category.is_(None),
                    DocumentRequirement.supplier_category == supplier_category,
                ),
                or_(
                    DocumentRequirement.supplier_tier.is_(None),
                    DocumentRequirement.supplier_tier == supplier_tier,
                ),
            )
            .order_by(
                DocumentRequirement.is_mandatory.desc(),
                DocumentRequirement.name.asc(),
            )
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, requirement: DocumentRequirement) -> DocumentRequirement:
        """Add a document requirement to the current session."""
        self.db.add(requirement)
        await self.db.flush()
        return requirement
