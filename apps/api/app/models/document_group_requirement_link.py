import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document_requirement import DocumentRequirement
    from app.models.organization import Organization
    from app.models.supplier_document_group import SupplierDocumentGroup


class DocumentGroupRequirementLink(Base):
    __tablename__ = "document_group_requirement_links"

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
            name="fk_group_req_links_organization_id",
        ),
        nullable=False,
    )
    document_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    link_origin: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="legacy_backfill",
        server_default=text("'legacy_backfill'"),
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_group_id", "organization_id"],
            [
                "supplier_document_groups.id",
                "supplier_document_groups.organization_id",
            ],
            name="fk_group_req_links_group",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["requirement_id", "organization_id"],
            [
                "document_requirements.id",
                "document_requirements.organization_id",
            ],
            name="fk_group_req_links_requirement",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "link_origin IN ('legacy_backfill', 'manual', 'policy_engine')",
            name="ck_group_req_links_origin",
        ),
        UniqueConstraint(
            "document_group_id",
            "requirement_id",
            name="uq_group_req_links_group_req",
        ),
        Index(
            "idx_group_req_links_req",
            "organization_id",
            "requirement_id",
        ),
        Index(
            "idx_group_req_links_group",
            "organization_id",
            "document_group_id",
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    document_group: Mapped["SupplierDocumentGroup"] = relationship(
        "SupplierDocumentGroup",
        back_populates="requirement_links",
        foreign_keys=[document_group_id, organization_id],
        overlaps="organization",
    )
    requirement: Mapped["DocumentRequirement"] = relationship(
        "DocumentRequirement",
        foreign_keys=[requirement_id, organization_id],
        overlaps="organization,document_group",
    )
