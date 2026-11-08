from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_review import DocumentReview
from app.models.supplier_document import SupplierDocument


class DocumentReviewRepository:
    """Repository handling all database operations for document review decisions and history.

    CRITICAL ARCHITECTURAL CONVENTIONS:
    1. Every query MUST explicitly filter by organization_id to enforce multi-tenant isolation.
    2. Review history queries MUST enforce deterministic ordering: 'created_at DESC, id DESC'.
    3. Repositories manage database persistence via add/flush only; transaction commit/rollback
       is strictly owned by domain services.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(
        self, review_id: UUID, organization_id: UUID
    ) -> DocumentReview | None:
        """Fetch a single review record by ID strictly within tenant boundary."""
        stmt = select(DocumentReview).where(
            DocumentReview.id == review_id,
            DocumentReview.organization_id == organization_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_document(
        self,
        document_id: UUID,
        organization_id: UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[DocumentReview], int]:
        """Fetch paginated review history for a document version with deterministic ordering."""
        base_filter = (
            DocumentReview.document_id == document_id,
            DocumentReview.organization_id == organization_id,
        )

        # 1. Total count for pagination metadata
        count_stmt = (
            select(func.count()).select_from(DocumentReview).where(*base_filter)
        )
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar_one()

        if total == 0:
            return [], 0

        # 2. Deterministic retrieval: created_at DESC, id DESC
        offset = max(0, (page - 1) * page_size)
        stmt = (
            select(DocumentReview)
            .where(*base_filter)
            .order_by(DocumentReview.created_at.desc(), DocumentReview.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())
        return items, total

    async def list_by_group(
        self,
        document_group_id: UUID,
        organization_id: UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[DocumentReview], int]:
        """Fetch paginated review history across all versions in a document group."""
        base_filter = (
            SupplierDocument.document_group_id == document_group_id,
            SupplierDocument.organization_id == organization_id,
            DocumentReview.organization_id == organization_id,
        )

        count_stmt = (
            select(func.count())
            .select_from(DocumentReview)
            .join(
                SupplierDocument,
                DocumentReview.document_id == SupplierDocument.id,
            )
            .where(*base_filter)
        )
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar_one()

        if total == 0:
            return [], 0

        offset = max(0, (page - 1) * page_size)
        stmt = (
            select(DocumentReview)
            .join(
                SupplierDocument,
                DocumentReview.document_id == SupplierDocument.id,
            )
            .where(*base_filter)
            .order_by(DocumentReview.created_at.desc(), DocumentReview.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())
        return items, total

    async def get_latest_for_document(
        self, document_id: UUID, organization_id: UUID
    ) -> DocumentReview | None:
        """Fetch the most recent review for a document version (created_at DESC, id DESC)."""
        stmt = (
            select(DocumentReview)
            .where(
                DocumentReview.document_id == document_id,
                DocumentReview.organization_id == organization_id,
            )
            .order_by(DocumentReview.created_at.desc(), DocumentReview.id.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, review: DocumentReview) -> DocumentReview:
        """Add a new review record and flush without committing."""
        self.db.add(review)
        await self.db.flush()
        return review
