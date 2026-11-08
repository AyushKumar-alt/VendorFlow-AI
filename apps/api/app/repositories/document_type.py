from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_type import DocumentType


class DocumentTypeRepository:
    """Repository handling database operations for document types.

    CRITICAL ARCHITECTURAL RULE:
    Every query MUST explicitly filter by organization_id to enforce multi-tenant isolation.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(
        self, document_type_id: UUID, organization_id: UUID
    ) -> DocumentType | None:
        """Fetch a single document type strictly within the tenant boundary."""
        stmt = select(DocumentType).where(
            DocumentType.id == document_type_id,
            DocumentType.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_code(
        self, code: str, organization_id: UUID
    ) -> DocumentType | None:
        """Fetch a document type by code within the tenant boundary."""
        stmt = select(DocumentType).where(
            DocumentType.code == code,
            DocumentType.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_document_types(
        self,
        organization_id: UUID,
        category: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[DocumentType], int]:
        """Fetch a paginated list of document types within the tenant boundary."""
        base_query = select(DocumentType).where(
            DocumentType.organization_id == organization_id
        )

        if category:
            base_query = base_query.where(DocumentType.category == category)

        if search and search.strip():
            term = f"%{search.strip()}%"
            base_query = base_query.where(
                or_(
                    DocumentType.name.ilike(term),
                    DocumentType.code.ilike(term),
                    DocumentType.description.ilike(term),
                )
            )

        # Count total matching records
        count_stmt = select(func.count()).select_from(base_query.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar_one()

        # Apply ordering and pagination
        offset = (page - 1) * page_size
        paginated_query = (
            base_query.order_by(DocumentType.name.asc()).offset(offset).limit(page_size)
        )
        result = await self.db.execute(paginated_query)
        items = list(result.scalars().all())

        return items, total

    async def create(self, document_type: DocumentType) -> DocumentType:
        """Add a document type to the current session."""
        self.db.add(document_type)
        await self.db.flush()
        return document_type
