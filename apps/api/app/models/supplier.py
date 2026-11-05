import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.supplier_document import SupplierDocument


class Supplier(Base):
    __tablename__ = "suppliers"

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
            name="fk_suppliers_organization_id",
        ),
        nullable=False,
    )
    vendor_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="raw_materials",
        server_default=text("'raw_materials'"),
    )
    tier: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="tier_2",
        server_default=text("'tier_2'"),
    )
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="draft",
        server_default=text("'draft'"),
    )
    compliance_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    primary_contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_contact_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    primary_contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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
            name="uq_suppliers_id_org",
        ),
        UniqueConstraint(
            "organization_id",
            "vendor_code",
            name="uq_suppliers_org_vendor_code",
        ),
        UniqueConstraint(
            "organization_id",
            "gstin",
            name="uq_suppliers_org_gstin",
        ),
        CheckConstraint(
            "status IN ('draft', 'invited', 'onboarding', 'under_review', 'approved', 'rejected', 'archived')",
            name="chk_suppliers_status",
        ),
        CheckConstraint(
            "compliance_status IN ('compliant', 'pending', 'action_required', 'non_compliant', 'expired')",
            name="chk_suppliers_compliance",
        ),
        CheckConstraint(
            "tier IN ('tier_1', 'tier_2', 'tier_3')",
            name="chk_suppliers_tier",
        ),
        CheckConstraint(
            "gstin IS NULL OR gstin ~ '^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'",
            name="chk_suppliers_gstin_format",
        ),
        CheckConstraint(
            "pan IS NULL OR pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'",
            name="chk_suppliers_pan_format",
        ),
        Index("idx_suppliers_org_status", "organization_id", "status"),
        Index(
            "idx_suppliers_org_compliance",
            "organization_id",
            "compliance_status",
        ),
        Index("idx_suppliers_org_legal_name", "organization_id", "legal_name"),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="suppliers",
    )
    documents: Mapped[list["SupplierDocument"]] = relationship(
        "SupplierDocument",
        back_populates="supplier",
        primaryjoin="and_(Supplier.id==SupplierDocument.supplier_id, Supplier.organization_id==SupplierDocument.organization_id)",
        overlaps="organization",
    )
