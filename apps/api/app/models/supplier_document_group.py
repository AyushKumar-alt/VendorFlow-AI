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
    from app.models.document_group_requirement_link import (
        DocumentGroupRequirementLink,
    )
    from app.models.document_type import DocumentType
    from app.models.organization import Organization
    from app.models.supplier import Supplier
    from app.models.supplier_document import SupplierDocument
    from app.models.supplier_site import SupplierSite


class SupplierDocumentGroup(Base):
    __tablename__ = "supplier_document_groups"

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
            name="fk_supplier_document_groups_organization_id",
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
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    coverage_scope: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="supplier",
        server_default=text("'supplier'"),
    )
    document_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issuing_authority: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
        ForeignKeyConstraint(
            ["supplier_id", "organization_id"],
            ["suppliers.id", "suppliers.organization_id"],
            name="fk_doc_groups_supplier",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_type_id", "organization_id"],
            ["document_types.id", "document_types.organization_id"],
            name="fk_doc_groups_doc_type",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["site_id", "supplier_id", "organization_id"],
            [
                "supplier_sites.id",
                "supplier_sites.supplier_id",
                "supplier_sites.organization_id",
            ],
            name="fk_doc_groups_site",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "coverage_scope IN ('supplier', 'site')",
            name="ck_supplier_doc_groups_scope",
        ),
        CheckConstraint(
            "(coverage_scope = 'site' AND site_id IS NOT NULL) OR (coverage_scope = 'supplier' AND site_id IS NULL)",
            name="ck_supplier_doc_groups_site_scope",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_supplier_doc_groups_id_org",
        ),
        UniqueConstraint(
            "id",
            "supplier_id",
            "organization_id",
            name="uq_supplier_doc_groups_id_supp_org",
        ),
        UniqueConstraint(
            "id",
            "supplier_id",
            "document_type_id",
            "organization_id",
            name="uq_supplier_doc_groups_id_lineage",
        ),
        Index(
            "uq_supplier_doc_groups_site_ref",
            "organization_id",
            "supplier_id",
            "document_type_id",
            "site_id",
            "document_reference",
            unique=True,
            postgresql_where=text(
                "site_id IS NOT NULL AND document_reference IS NOT NULL"
            ),
        ),
        Index(
            "uq_supplier_doc_groups_supplier_ref",
            "organization_id",
            "supplier_id",
            "document_type_id",
            "document_reference",
            unique=True,
            postgresql_where=text("site_id IS NULL AND document_reference IS NOT NULL"),
        ),
        Index(
            "idx_supplier_doc_groups_lookup",
            "organization_id",
            "supplier_id",
            "document_type_id",
        ),
        Index(
            "idx_supplier_doc_groups_site",
            "organization_id",
            "site_id",
            postgresql_where=text("site_id IS NOT NULL"),
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    supplier: Mapped["Supplier"] = relationship(
        "Supplier",
        back_populates="document_groups",
        foreign_keys=[supplier_id, organization_id],
        overlaps="organization",
    )
    document_type: Mapped["DocumentType"] = relationship(
        "DocumentType",
        foreign_keys=[document_type_id, organization_id],
        overlaps="organization,supplier,document_groups",
    )
    site: Mapped["SupplierSite | None"] = relationship(
        "SupplierSite",
        back_populates="document_groups",
        foreign_keys=[site_id, supplier_id, organization_id],
        overlaps="organization,supplier,document_groups,document_type",
    )
    documents: Mapped[list["SupplierDocument"]] = relationship(
        "SupplierDocument",
        back_populates="document_group",
        primaryjoin="and_(SupplierDocumentGroup.id==SupplierDocument.document_group_id, SupplierDocumentGroup.organization_id==SupplierDocument.organization_id)",
        overlaps="organization,supplier,document_type,documents",
    )
    requirement_links: Mapped[list["DocumentGroupRequirementLink"]] = relationship(
        "DocumentGroupRequirementLink",
        back_populates="document_group",
        primaryjoin="and_(SupplierDocumentGroup.id==DocumentGroupRequirementLink.document_group_id, SupplierDocumentGroup.organization_id==DocumentGroupRequirementLink.organization_id)",
        overlaps="organization,group_links,requirement",
    )
