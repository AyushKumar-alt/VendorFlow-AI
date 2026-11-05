import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.membership import Membership
    from app.models.organization import Organization
    from app.models.supplier_document import SupplierDocument


class DocumentReview(Base):
    __tablename__ = "document_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            ondelete="RESTRICT",
            name="fk_document_reviews_organization_id",
        ),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(50), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    rejection_reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["supplier_documents.id", "supplier_documents.organization_id"],
            name="fk_doc_reviews_doc",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["reviewer_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_doc_reviews_reviewer_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('approved', 'rejected', 'revision_requested')",
            name="chk_doc_reviews_decision",
        ),
        CheckConstraint(
            "rejection_reason_code IS NULL OR rejection_reason_code IN ('illegible', 'mismatched_gstin', 'expired', 'name_mismatch', 'incomplete_pages', 'invalid_document_type', 'other')",
            name="chk_doc_reviews_rejection_code",
        ),
        Index("idx_doc_reviews_doc", "organization_id", "document_id"),
        Index("idx_doc_reviews_created", "organization_id", "created_at"),
        Index("idx_doc_reviews_reviewer", "reviewer_id"),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    document: Mapped["SupplierDocument"] = relationship(
        "SupplierDocument",
        back_populates="reviews",
        foreign_keys=[document_id, organization_id],
        overlaps="organization",
    )
    reviewer_membership: Mapped["Membership"] = relationship(
        "Membership",
        foreign_keys=[reviewer_id, organization_id],
        overlaps="document,organization",
    )
