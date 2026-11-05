import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.supplier_document import SupplierDocument


class DocumentExtraction(Base):
    __tablename__ = "document_extractions"

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
            name="fk_document_extractions_organization_id",
        ),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    extraction_engine: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="local_ocr",
        server_default=text("'local_ocr'"),
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    field_confidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    validation_flags: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    execution_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["supplier_documents.id", "supplier_documents.organization_id"],
            name="fk_doc_extractions_doc",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="chk_doc_extractions_status",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0.000 AND confidence_score <= 1.000)",
            name="chk_doc_extractions_confidence",
        ),
        Index("idx_doc_extractions_doc", "organization_id", "document_id"),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    document: Mapped["SupplierDocument"] = relationship(
        "SupplierDocument",
        back_populates="extractions",
        foreign_keys=[document_id, organization_id],
        overlaps="organization",
    )
