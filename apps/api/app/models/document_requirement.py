import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document_group_requirement_link import (
        DocumentGroupRequirementLink,
    )
    from app.models.document_type import DocumentType
    from app.models.organization import Organization


class DocumentRequirement(Base):
    __tablename__ = "document_requirements"

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
            name="fk_document_requirements_organization_id",
        ),
        nullable=False,
    )
    document_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supplier_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_mandatory: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    validity_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expiry_lead_notice_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
        server_default=text("30"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    validation_rules: Mapped[dict[str, Any]] = mapped_column(
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
            name="uq_doc_requirements_id_org",
        ),
        UniqueConstraint(
            "id",
            "document_type_id",
            "organization_id",
            name="uq_doc_requirements_id_type_org",
        ),
        ForeignKeyConstraint(
            ["document_type_id", "organization_id"],
            ["document_types.id", "document_types.organization_id"],
            name="fk_doc_requirements_doc_type",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "expiry_lead_notice_days >= 0",
            name="chk_doc_requirements_expiry_lead",
        ),
        CheckConstraint(
            "validity_period_days IS NULL OR validity_period_days > 0",
            name="chk_doc_requirements_validity",
        ),
        Index("idx_doc_requirements_org_active", "organization_id", "is_active"),
        Index(
            "idx_doc_requirements_lookup",
            "organization_id",
            "supplier_category",
            "supplier_tier",
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="document_requirements",
    )
    document_type: Mapped["DocumentType"] = relationship(
        "DocumentType",
        back_populates="requirements",
        foreign_keys=[document_type_id, organization_id],
        overlaps="organization,document_requirements",
    )
    group_links: Mapped[list["DocumentGroupRequirementLink"]] = relationship(
        "DocumentGroupRequirementLink",
        back_populates="requirement",
        foreign_keys="[DocumentGroupRequirementLink.requirement_id, DocumentGroupRequirementLink.organization_id]",
        overlaps="document_group,organization",
    )
