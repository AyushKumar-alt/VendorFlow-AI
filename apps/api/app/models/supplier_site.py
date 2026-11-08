import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    Boolean,
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
    from app.models.organization import Organization
    from app.models.supplier import Supplier
    from app.models.supplier_document_group import SupplierDocumentGroup


class SupplierSite(Base):
    __tablename__ = "supplier_sites"

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
            name="fk_supplier_sites_organization_id",
        ),
        nullable=False,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    site_code: Mapped[str] = mapped_column(String(50), nullable=False)
    site_name: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country_code: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        default="IN",
        server_default=text("'IN'"),
    )
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    is_active: Mapped[bool] = mapped_column(
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
        ForeignKeyConstraint(
            ["supplier_id", "organization_id"],
            ["suppliers.id", "suppliers.organization_id"],
            name="fk_supplier_sites_supplier",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "organization_id",
            "supplier_id",
            "site_code",
            name="uq_supplier_sites_org_code",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_supplier_sites_id_org",
        ),
        UniqueConstraint(
            "id",
            "supplier_id",
            "organization_id",
            name="uq_supplier_sites_id_supp_org",
        ),
        CheckConstraint(
            "gstin IS NULL OR gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'",
            name="ck_supplier_sites_gstin_format",
        ),
        CheckConstraint(
            "country_code ~ '^[A-Z]{2}$'",
            name="ck_supplier_sites_country_code",
        ),
        Index(
            "idx_supplier_sites_lookup",
            "organization_id",
            "supplier_id",
            "is_active",
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    supplier: Mapped["Supplier"] = relationship(
        "Supplier",
        back_populates="sites",
        foreign_keys=[supplier_id, organization_id],
        overlaps="organization",
    )
    document_groups: Mapped[list["SupplierDocumentGroup"]] = relationship(
        "SupplierDocumentGroup",
        back_populates="site",
        foreign_keys="[SupplierDocumentGroup.site_id, SupplierDocumentGroup.supplier_id, SupplierDocumentGroup.organization_id]",
        overlaps="organization,supplier,document_groups,document_type",
    )
