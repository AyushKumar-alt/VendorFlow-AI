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
    from app.models.membership import Membership
    from app.models.organization import Organization


class AuditEvent(Base):
    __tablename__ = "audit_events"

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
            name="fk_audit_events_organization_id",
        ),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    actor_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="user",
        server_default=text("'user'"),
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    previous_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
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
            ["actor_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_audit_events_actor_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "actor_type IN ('user', 'supplier', 'system')",
            name="chk_audit_events_actor_type",
        ),
        CheckConstraint(
            "((actor_type = 'user' AND actor_id IS NOT NULL) OR (actor_type IN ('supplier', 'system') AND actor_id IS NULL))",
            name="chk_audit_events_actor_attribution",
        ),
        Index("idx_audit_events_timeline", "organization_id", "created_at"),
        Index(
            "idx_audit_events_entity",
            "organization_id",
            "entity_type",
            "entity_id",
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    actor_membership: Mapped["Membership | None"] = relationship(
        "Membership",
        foreign_keys=[actor_id, organization_id],
        viewonly=True,
    )
