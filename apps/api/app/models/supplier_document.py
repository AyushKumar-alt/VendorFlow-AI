import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document_extraction import DocumentExtraction
    from app.models.document_requirement import DocumentRequirement
    from app.models.document_review import DocumentReview
    from app.models.document_type import DocumentType
    from app.models.organization import Organization
    from app.models.supplier import Supplier


class SupplierDocument(Base):
    __tablename__ = "supplier_documents"

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
            name="fk_supplier_documents_organization_id",
        ),
        nullable=False,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    document_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="uploaded",
        server_default=text("'uploaded'"),
    )
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active_version: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_supplier_documents_id_org",
        ),
        UniqueConstraint(
            "id",
            "supplier_id",
            "document_type_id",
            "organization_id",
            name="uq_supplier_documents_id_lineage",
        ),
        UniqueConstraint(
            "supplier_id",
            "document_type_id",
            "version",
            name="uq_supplier_documents_version",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "organization_id"],
            ["suppliers.id", "suppliers.organization_id"],
            name="fk_supplier_documents_supplier",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_type_id", "organization_id"],
            ["document_types.id", "document_types.organization_id"],
            name="fk_supplier_documents_doc_type",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "document_type_id", "organization_id"],
            [
                "document_requirements.id",
                "document_requirements.document_type_id",
                "document_requirements.organization_id",
            ],
            name="fk_supplier_documents_requirement",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "previous_version_id",
                "supplier_id",
                "document_type_id",
                "organization_id",
            ],
            [
                "supplier_documents.id",
                "supplier_documents.supplier_id",
                "supplier_documents.document_type_id",
                "supplier_documents.organization_id",
            ],
            name="fk_supplier_documents_previous_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "version >= 1",
            name="chk_supplier_docs_version",
        ),
        CheckConstraint(
            "file_size_bytes > 0 AND file_size_bytes <= 26214400",
            name="chk_supplier_docs_file_size",
        ),
        CheckConstraint(
            "file_hash_sha256 ~ '^[a-f0-9]{64}$'",
            name="chk_supplier_docs_hash_format",
        ),
        CheckConstraint(
            "file_mime_type IN ('application/pdf', 'image/jpeg', 'image/png')",
            name="chk_supplier_docs_mime_type",
        ),
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'extracted', 'verified', 'rejected', 'action_required', 'expired', 'superseded')",
            name="chk_supplier_docs_status",
        ),
        Index(
            "uq_supplier_docs_active_version",
            "supplier_id",
            "document_type_id",
            unique=True,
            postgresql_where=text("is_active_version = TRUE"),
        ),
        Index(
            "idx_supplier_docs_expiry",
            "organization_id",
            "expiry_date",
            postgresql_where=text(
                "is_active_version = TRUE AND expiry_date IS NOT NULL"
            ),
        ),
        Index(
            "idx_supplier_docs_supplier",
            "organization_id",
            "supplier_id",
            "is_active_version",
        ),
        Index(
            "idx_supplier_docs_org_hash",
            "organization_id",
            "file_hash_sha256",
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    supplier: Mapped["Supplier"] = relationship(
        "Supplier",
        back_populates="documents",
        foreign_keys=[supplier_id, organization_id],
        overlaps="organization",
    )
    document_type: Mapped["DocumentType"] = relationship(
        "DocumentType",
        foreign_keys=[document_type_id, organization_id],
        overlaps="documents,organization,supplier",
    )
    requirement: Mapped["DocumentRequirement | None"] = relationship(
        "DocumentRequirement",
        foreign_keys=[requirement_id, document_type_id, organization_id],
        overlaps="document_type,documents,organization,supplier",
    )
    extractions: Mapped[list["DocumentExtraction"]] = relationship(
        "DocumentExtraction",
        back_populates="document",
        primaryjoin="and_(SupplierDocument.id==DocumentExtraction.document_id, SupplierDocument.organization_id==DocumentExtraction.organization_id)",
        overlaps="organization",
    )
    reviews: Mapped[list["DocumentReview"]] = relationship(
        "DocumentReview",
        back_populates="document",
        primaryjoin="and_(SupplierDocument.id==DocumentReview.document_id, SupplierDocument.organization_id==DocumentReview.organization_id)",
        overlaps="organization,reviewer_membership",
    )
    previous_version: Mapped["SupplierDocument | None"] = relationship(
        "SupplierDocument",
        remote_side=[id, supplier_id, document_type_id, organization_id],
        foreign_keys=[
            previous_version_id,
            supplier_id,
            document_type_id,
            organization_id,
        ],
        viewonly=True,
    )
