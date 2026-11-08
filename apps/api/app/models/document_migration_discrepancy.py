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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.supplier_document import SupplierDocument


class SupplierDocumentMigrationDiscrepancy(Base):
    __tablename__ = "supplier_document_migration_discrepancies"

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
            name="fk_doc_discrepancies_organization_id",
        ),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    discrepancy_type: Mapped[str] = mapped_column(String(100), nullable=False)
    legacy_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    latest_review_decision: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            [
                "supplier_documents.id",
                "supplier_documents.organization_id",
            ],
            name="fk_doc_discrepancies_doc",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "discrepancy_type IN ('status_review_mismatch', 'unlinked_requirement', 'orphan_extracted', 'expired_effective', 'rootless_cycle_repaired', 'competing_approval')",
            name="ck_discrepancies_type",
        ),
        Index(
            "idx_doc_discrepancies_lookup",
            "organization_id",
            "discrepancy_type",
        ),
        Index(
            "idx_doc_discrepancies_doc_type",
            "document_id",
            "discrepancy_type",
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    document: Mapped["SupplierDocument"] = relationship(
        "SupplierDocument",
        foreign_keys=[document_id, organization_id],
        overlaps="organization",
    )
