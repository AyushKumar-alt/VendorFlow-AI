"""add_action_required_status_and_updated_at_triggers

Revision ID: 9b7d3318e727
Revises: 4f5ae27a4e45
Create Date: 2026-11-05 13:35:29.529510

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b7d3318e727"
down_revision: str | Sequence[str] | None = "4f5ae27a4e45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES_WITH_UPDATED_AT = (
    "organizations",
    "users",
    "memberships",
    "suppliers",
    "document_types",
    "document_requirements",
    "supplier_documents",
)


def upgrade() -> None:
    """Upgrade schema: add action_required status and PostgreSQL updated_at triggers."""
    # 1. Update supplier_documents status check constraint to include 'action_required'
    op.execute(
        "ALTER TABLE supplier_documents "
        "DROP CONSTRAINT IF EXISTS ck_supplier_documents_chk_supplier_docs_status;"
    )
    op.execute(
        "ALTER TABLE supplier_documents "
        "ADD CONSTRAINT ck_supplier_documents_chk_supplier_docs_status "
        "CHECK (status IN ('uploaded', 'processing', 'extracted', 'verified', 'rejected', "
        "'action_required', 'expired', 'superseded'));"
    )

    # 2. Define reusable trigger function to set updated_at on record updates
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # 3. Create BEFORE UPDATE triggers for all 7 tables possessing updated_at
    for table in TABLES_WITH_UPDATED_AT:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_column();
            """
        )


def downgrade() -> None:
    """Downgrade schema: drop triggers, trigger function, and revert status constraint."""
    # 1. Drop BEFORE UPDATE triggers
    for table in reversed(TABLES_WITH_UPDATED_AT):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table};")

    # 2. Drop trigger function
    op.execute("DROP FUNCTION IF EXISTS set_updated_at_column();")

    # 3. Revert supplier_documents status check constraint
    op.execute(
        "ALTER TABLE supplier_documents "
        "DROP CONSTRAINT IF EXISTS ck_supplier_documents_chk_supplier_docs_status;"
    )
    op.execute(
        "ALTER TABLE supplier_documents "
        "ADD CONSTRAINT ck_supplier_documents_chk_supplier_docs_status "
        "CHECK (status IN ('uploaded', 'processing', 'extracted', 'verified', 'rejected', "
        "'expired', 'superseded'));"
    )
