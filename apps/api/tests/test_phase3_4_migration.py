"""Comprehensive isolated integration tests for Phase 3.4 database migration.

CRITICAL SAFETY RULE:
- All tests execute strictly against 'vendorflow_test'.
- The development database 'vendorflow' is never touched.
- Preflight failure conditions, data backfills, triggers, and downgrade checks are exhaustively verified.
"""

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config

from alembic import command
from app.core.config import settings

BASE_REVISION = "9b7d3318e727"
HEAD_REVISION = "f129c5e378bc"


def _run_alembic(action: str, target: str) -> None:
    """Execute Alembic upgrade/downgrade strictly targeting approved verification database."""
    test_url = settings.get_test_database_url()
    approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
    if test_url.database != approved_db:
        raise RuntimeError(
            f"Safety guard failure: Target '{test_url.database}' is not '{approved_db}'."
        )
    if test_url.database == "vendorflow":
        raise RuntimeError(
            f"CRITICAL SAFETY VIOLATION: Refusing to run migrations against production database '{test_url.database}'!"
        )

    os.environ["ALEMBIC_DB"] = "test"
    api_dir = Path(__file__).resolve().parents[1]
    alembic_ini = api_dir / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(api_dir / "alembic"))

    if action == "upgrade":
        command.upgrade(cfg, target)
    elif action == "downgrade":
        command.downgrade(cfg, target)
    else:
        raise ValueError(f"Unknown action: {action}")


async def _reset_test_db_to_base() -> None:
    """Safely clear test data so downgrade preflights pass and restore BASE_REVISION in child-before-parent order."""
    test_url = settings.get_test_database_url()
    approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
    if test_url.database != approved_db:
        raise RuntimeError(
            f"Safety guard failure: Target '{test_url.database}' is not '{approved_db}'."
        )
    if test_url.database == "vendorflow":
        raise RuntimeError(
            f"CRITICAL SAFETY VIOLATION: Refusing to connect to production database '{test_url.database}'!"
        )

    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=approved_db,
    )
    try:
        # 1. Delete dependent leaf records (reviews, extractions, audit events)
        await conn.execute("DELETE FROM audit_events;")
        await conn.execute("DELETE FROM document_reviews;")
        await conn.execute("DELETE FROM document_extractions;")

        # 2. Check if Phase 3.4 tables exist and reconcile preflight requirements
        table_exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'supplier_document_groups';"
        )
        if table_exists:
            await conn.execute("DELETE FROM document_group_requirement_links;")
            await conn.execute(
                """
                INSERT INTO document_group_requirement_links (id, organization_id, document_group_id, requirement_id, link_origin)
                SELECT gen_random_uuid(), d.organization_id, d.document_group_id, d.legacy_requirement_id, 'legacy_backfill'
                FROM supplier_documents d
                JOIN document_requirements r ON d.legacy_requirement_id = r.id AND d.organization_id = r.organization_id
                WHERE d.legacy_requirement_id IS NOT NULL
                GROUP BY d.organization_id, d.document_group_id, d.legacy_requirement_id;
                """
            )
            await conn.execute(
                "UPDATE supplier_document_groups SET site_id = NULL, coverage_scope = 'supplier', document_reference = NULL, issuing_authority = NULL;"
            )
            await conn.execute("DELETE FROM supplier_sites;")
            await conn.execute(
                "UPDATE supplier_document_groups g SET display_name = dt.name FROM document_types dt WHERE g.document_type_id = dt.id AND g.organization_id = dt.organization_id;"
            )
            await conn.execute(
                "UPDATE supplier_documents SET processing_status = 'extracted' WHERE processing_status = 'failed';"
            )
            await conn.execute(
                "UPDATE supplier_documents SET legacy_status = 'uploaded' WHERE legacy_status IS NULL;"
            )
            await conn.execute(
                "UPDATE supplier_documents SET is_effective_version = FALSE;"
            )
            await conn.execute(
                "UPDATE document_requirements SET applicability_scope = 'supplier', precedence_order = 100 WHERE applicability_scope != 'supplier' OR precedence_order != 100;"
            )
    finally:
        await conn.close()

    # 3. Safely downgrade to BASE_REVISION
    await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)

    # 4. Clean up remaining base records in strict child-before-parent order
    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=approved_db,
    )
    try:
        await conn.execute("DELETE FROM audit_events;")
        await conn.execute("DELETE FROM document_reviews;")
        await conn.execute("DELETE FROM document_extractions;")
        await conn.execute("UPDATE supplier_documents SET previous_version_id = NULL;")
        await conn.execute("DELETE FROM supplier_documents;")
        await conn.execute("DELETE FROM document_requirements;")
        await conn.execute("DELETE FROM document_types;")
        await conn.execute("DELETE FROM memberships;")
        await conn.execute("DELETE FROM suppliers;")
        await conn.execute("DELETE FROM users;")
        await conn.execute("DELETE FROM organizations;")
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
async def ensure_clean_test_db():
    """Ensure approved verification database starts and ends at BASE_REVISION for each test."""
    test_url = settings.get_test_database_url()
    approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
    assert test_url.database == approved_db
    assert test_url.database != "vendorflow"

    # Make sure we start at base revision
    await _reset_test_db_to_base()
    yield
    # Make sure we finish at base revision so existing backend tests continue to pass
    await _reset_test_db_to_base()


@pytest.fixture
async def pg_conn():
    """Direct asyncpg connection to approved verification database."""
    test_url = settings.get_test_database_url()
    approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
    assert test_url.database == approved_db
    assert test_url.database != "vendorflow"

    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=approved_db,
    )
    try:
        yield conn
    finally:
        await conn.close()


async def _cleanup_base_tables(conn: asyncpg.Connection) -> None:
    """Delete records from base tables in strict child-before-parent foreign-key order without CASCADE."""
    await conn.execute("DELETE FROM audit_events;")
    await conn.execute("DELETE FROM document_reviews;")
    await conn.execute("DELETE FROM document_extractions;")
    await conn.execute("UPDATE supplier_documents SET previous_version_id = NULL;")
    await conn.execute("DELETE FROM supplier_documents;")
    await conn.execute("DELETE FROM document_requirements;")
    await conn.execute("DELETE FROM document_types;")
    await conn.execute("DELETE FROM memberships;")
    await conn.execute("DELETE FROM suppliers;")
    await conn.execute("DELETE FROM users;")
    await conn.execute("DELETE FROM organizations;")


async def _seed_base_entities(conn: asyncpg.Connection):
    """Seed tenant, supplier, document type and requirement in legacy schema."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    supplier_id = uuid.uuid4()
    doc_type_id = uuid.uuid4()
    req_id = uuid.uuid4()

    await conn.execute(
        """
        INSERT INTO organizations (id, name, slug)
        VALUES ($1, 'Acme Corp', $2);
        """,
        org_id,
        f"acme-{uuid.uuid4().hex[:8]}",
    )

    await conn.execute(
        """
        INSERT INTO users (id, email, full_name, hashed_password)
        VALUES ($1, $2, 'Test Reviewer', 'hash');
        """,
        user_id,
        f"user-{uuid.uuid4().hex[:8]}@example.com",
    )

    await conn.execute(
        """
        INSERT INTO memberships (id, organization_id, user_id, role)
        VALUES ($1, $2, $3, 'reviewer');
        """,
        uuid.uuid4(),
        org_id,
        user_id,
    )

    await conn.execute(
        """
        INSERT INTO suppliers (id, organization_id, legal_name, vendor_code, category, tier)
        VALUES ($1, $2, 'Supplier Beta', $3, 'raw_materials', 'tier_2');
        """,
        supplier_id,
        org_id,
        f"SUP-{uuid.uuid4().hex[:6]}",
    )

    await conn.execute(
        """
        INSERT INTO document_types (id, organization_id, name, code, category)
        VALUES ($1, $2, 'GST Certificate', $3, 'tax');
        """,
        doc_type_id,
        org_id,
        f"GST_{uuid.uuid4().hex[:6]}",
    )

    await conn.execute(
        """
        INSERT INTO document_requirements (id, organization_id, document_type_id, name, is_mandatory)
        VALUES ($1, $2, $3, 'GSTIN Mandatory Requirement', true);
        """,
        req_id,
        org_id,
        doc_type_id,
    )

    return {
        "org_id": org_id,
        "user_id": user_id,
        "supplier_id": supplier_id,
        "doc_type_id": doc_type_id,
        "req_id": req_id,
    }


# =============================================================================
# 1. Lineage Preflight Tests (Dangling, Self-Ref, Fork, Cycles, Lassos, Mismatches)
# =============================================================================


async def test_preflight_detects_dangling_predecessor(
    pg_conn: asyncpg.Connection,
):
    """Test dangling predecessor check independently of the existing FK."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)

    # Deliberately drop the legacy previous_version foreign key constraint to insert dangling ref
    await pg_conn.execute(
        "ALTER TABLE supplier_documents DROP CONSTRAINT IF EXISTS fk_supplier_documents_previous_version;"
    )

    doc_id = uuid.uuid4()
    dangling_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256
        ) VALUES (
            $1, $2, $3, $4, $5,
            $6, 1, 'uploaded', 'storage/test.pdf', 'test.pdf', 1024,
            'application/pdf', '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
        );
        """,
        doc_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        seeds["req_id"],
        dangling_id,
    )

    # Upgrade should abort in preflight
    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)
    assert "dangling previous_version_id" in str(exc_info.value)

    # Cleanup dangling document & restore FK
    await _cleanup_base_tables(pg_conn)
    await pg_conn.execute(
        """
        ALTER TABLE supplier_documents
            ADD CONSTRAINT fk_supplier_documents_previous_version
            FOREIGN KEY (previous_version_id, supplier_id, document_type_id, organization_id)
            REFERENCES supplier_documents(id, supplier_id, document_type_id, organization_id)
            ON DELETE RESTRICT;
        """
    )


async def test_preflight_detects_self_reference(pg_conn: asyncpg.Connection):
    """Test direct self-reference (previous_version_id = id)."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)

    # Drop FK to insert self-reference in one go
    await pg_conn.execute(
        "ALTER TABLE supplier_documents DROP CONSTRAINT IF EXISTS fk_supplier_documents_previous_version;"
    )

    doc_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256
        ) VALUES (
            $1, $2, $3, $4, $5,
            $1, 1, 'uploaded', 'storage/test.pdf', 'test.pdf', 1024,
            'application/pdf', '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
        );
        """,
        doc_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        seeds["req_id"],
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)
    assert "self-referencing previous_version_id" in str(exc_info.value)

    await _cleanup_base_tables(pg_conn)
    await pg_conn.execute(
        """
        ALTER TABLE supplier_documents
            ADD CONSTRAINT fk_supplier_documents_previous_version
            FOREIGN KEY (previous_version_id, supplier_id, document_type_id, organization_id)
            REFERENCES supplier_documents(id, supplier_id, document_type_id, organization_id)
            ON DELETE RESTRICT;
        """
    )


async def test_preflight_detects_cross_supplier_mismatch(
    pg_conn: asyncpg.Connection,
):
    """Test cross-supplier predecessor mismatch (same org, same doc_type, different supplier)."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)
    org_id = seeds["org_id"]
    sup1_id = seeds["supplier_id"]
    dt_id = seeds["doc_type_id"]

    # Drop composite FK
    await pg_conn.execute(
        "ALTER TABLE supplier_documents DROP CONSTRAINT IF EXISTS fk_supplier_documents_previous_version;"
    )

    # Insert root doc under supplier 1
    doc1_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256
        ) VALUES ($1, $2, $3, $4, NULL, NULL, 1, 'uploaded', 's/d1.pdf', 'd1.pdf', 100, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000001');
        """,
        doc1_id,
        org_id,
        sup1_id,
        dt_id,
    )

    # Insert supplier 2
    sup2_id = uuid.uuid4()
    await pg_conn.execute(
        "INSERT INTO suppliers (id, organization_id, legal_name, vendor_code) VALUES ($1, $2, 'Supplier 2', $3);",
        sup2_id,
        org_id,
        f"SUP2-{uuid.uuid4().hex[:6]}",
    )

    # Insert child doc under supplier 2 referencing doc1 from supplier 1
    doc2_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256
        ) VALUES ($1, $2, $3, $4, NULL, $5, 2, 'uploaded', 's/d2.pdf', 'd2.pdf', 100, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000002');
        """,
        doc2_id,
        org_id,
        sup2_id,
        dt_id,
        doc1_id,
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)
    assert "predecessor organization/supplier/document_type mismatch" in str(
        exc_info.value
    )

    await _cleanup_base_tables(pg_conn)
    await pg_conn.execute(
        """
        ALTER TABLE supplier_documents
            ADD CONSTRAINT fk_supplier_documents_previous_version
            FOREIGN KEY (previous_version_id, supplier_id, document_type_id, organization_id)
            REFERENCES supplier_documents(id, supplier_id, document_type_id, organization_id)
            ON DELETE RESTRICT;
        """
    )


async def test_preflight_detects_cross_document_type_mismatch(
    pg_conn: asyncpg.Connection,
):
    """Test cross-document-type predecessor mismatch (same org, same supplier, different doc_type)."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)
    org_id = seeds["org_id"]
    sup_id = seeds["supplier_id"]
    dt1_id = seeds["doc_type_id"]

    await pg_conn.execute(
        "ALTER TABLE supplier_documents DROP CONSTRAINT IF EXISTS fk_supplier_documents_previous_version;"
    )

    # Insert root doc of type 1
    doc1_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256
        ) VALUES ($1, $2, $3, $4, NULL, NULL, 1, 'uploaded', 's/d1.pdf', 'd1.pdf', 100, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000001');
        """,
        doc1_id,
        org_id,
        sup_id,
        dt1_id,
    )

    # Insert document type 2
    dt2_id = uuid.uuid4()
    await pg_conn.execute(
        "INSERT INTO document_types (id, organization_id, name, code, category) VALUES ($1, $2, 'PAN Card', $3, 'tax');",
        dt2_id,
        org_id,
        f"PAN_{uuid.uuid4().hex[:6]}",
    )

    # Insert child doc of type 2 referencing doc1 of type 1
    doc2_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256
        ) VALUES ($1, $2, $3, $4, NULL, $5, 2, 'uploaded', 's/d2.pdf', 'd2.pdf', 100, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000002');
        """,
        doc2_id,
        org_id,
        sup_id,
        dt2_id,
        doc1_id,
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)
    assert "predecessor organization/supplier/document_type mismatch" in str(
        exc_info.value
    )

    await _cleanup_base_tables(pg_conn)
    await pg_conn.execute(
        """
        ALTER TABLE supplier_documents
            ADD CONSTRAINT fk_supplier_documents_previous_version
            FOREIGN KEY (previous_version_id, supplier_id, document_type_id, organization_id)
            REFERENCES supplier_documents(id, supplier_id, document_type_id, organization_id)
            ON DELETE RESTRICT;
        """
    )


async def test_preflight_detects_branching_fork(pg_conn: asyncpg.Connection):
    """Test branching fork detection (multiple children for same predecessor)."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)

    # Root version 1
    doc1_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256
        ) VALUES (
            $1, $2, $3, $4, $5,
            NULL, 1, 'uploaded', 'storage/test1.pdf', 'test1.pdf', 1024,
            'application/pdf', '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
        );
        """,
        doc1_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        seeds["req_id"],
    )

    # Two distinct child versions both pointing to doc1 (branching)
    doc2_id = uuid.uuid4()
    doc3_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5,
            $6, 2, 'uploaded', 'storage/test2.pdf', 'test2.pdf', 1024,
            'application/pdf', '1123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef', false
        ), (
            $7, $2, $3, $4, $5,
            $6, 3, 'uploaded', 'storage/test3.pdf', 'test3.pdf', 1024,
            'application/pdf', '2123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef', false
        );
        """,
        doc2_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        seeds["req_id"],
        doc1_id,
        doc3_id,
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)
    assert "branching detected" in str(exc_info.value)

    await _cleanup_base_tables(pg_conn)


async def test_preflight_detects_rootless_cycle(pg_conn: asyncpg.Connection):
    """Test cycle detection when no root exists (e.g. A -> B -> A)."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)

    await pg_conn.execute(
        "ALTER TABLE supplier_documents DROP CONSTRAINT IF EXISTS fk_supplier_documents_previous_version;"
    )

    doc_a = uuid.uuid4()
    doc_b = uuid.uuid4()

    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5,
            $6, 1, 'uploaded', 'storage/testA.pdf', 'testA.pdf', 1024,
            'application/pdf', '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef', false
        ), (
            $6, $2, $3, $4, $5,
            $1, 2, 'uploaded', 'storage/testB.pdf', 'testB.pdf', 1024,
            'application/pdf', '1123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef', true
        );
        """,
        doc_a,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        seeds["req_id"],
        doc_b,
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)
    assert "Lineage cycle detected" in str(
        exc_info.value
    ) or "does not match reachable" in str(exc_info.value)

    await _cleanup_base_tables(pg_conn)
    await pg_conn.execute(
        """
        ALTER TABLE supplier_documents
            ADD CONSTRAINT fk_supplier_documents_previous_version
            FOREIGN KEY (previous_version_id, supplier_id, document_type_id, organization_id)
            REFERENCES supplier_documents(id, supplier_id, document_type_id, organization_id)
            ON DELETE RESTRICT;
        """
    )


async def test_preflight_detects_cycle_with_tail(pg_conn: asyncpg.Connection):
    """Test cycle detection in a lasso graph: tail A -> B -> C -> D -> B."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)

    await pg_conn.execute(
        "ALTER TABLE supplier_documents DROP CONSTRAINT IF EXISTS fk_supplier_documents_previous_version;"
    )

    doc_a = uuid.uuid4()
    doc_b = uuid.uuid4()
    doc_c = uuid.uuid4()
    doc_d = uuid.uuid4()

    # Doc A has NULL previous_version_id (tail root)
    # Doc B -> Doc A
    # Doc C -> Doc B
    # Doc D -> Doc C
    # But wait, to form a cycle with tail: Doc B has previous_version_id = Doc D (creating cycle B->C->D->B)
    # and Doc E points to Doc B.
    # More simply: Doc A (root) -> Doc B -> Doc C -> Doc B (Doc C points back to B, lasso tail at A)
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES
        ($1, $5, $6, $7, NULL, NULL, 1, 'uploaded', 's/a.pdf', 'a.pdf', 100, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000001', false),
        ($2, $5, $6, $7, NULL, $1, 2, 'uploaded', 's/b.pdf', 'b.pdf', 100, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000002', false),
        ($3, $5, $6, $7, NULL, $2, 3, 'uploaded', 's/c.pdf', 'c.pdf', 100, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000003', false),
        ($4, $5, $6, $7, NULL, $3, 4, 'uploaded', 's/d.pdf', 'd.pdf', 100, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000004', true);
        """,
        doc_a,
        doc_b,
        doc_c,
        doc_d,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
    )
    # Now close the loop by pointing doc_b back to doc_d (creating tail A, loop B->C->D->B)
    await pg_conn.execute(
        "UPDATE supplier_documents SET previous_version_id = $1 WHERE id = $2;",
        doc_d,
        doc_b,
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)
    assert "Lineage cycle detected" in str(
        exc_info.value
    ) or "does not match reachable" in str(exc_info.value)

    await _cleanup_base_tables(pg_conn)
    await pg_conn.execute(
        """
        ALTER TABLE supplier_documents
            ADD CONSTRAINT fk_supplier_documents_previous_version
            FOREIGN KEY (previous_version_id, supplier_id, document_type_id, organization_id)
            REFERENCES supplier_documents(id, supplier_id, document_type_id, organization_id)
            ON DELETE RESTRICT;
        """
    )


# =============================================================================
# 2. Effective-Version Ranking & Provenance Tests (Scenarios 1 through 5)
# =============================================================================


async def test_effective_version_scenarios_and_unique_index(
    pg_conn: asyncpg.Connection,
):
    """Exhaustively test all 5 effective-version ranking scenarios and unique index."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)
    org_id = seeds["org_id"]
    sup_id = seeds["supplier_id"]
    user_id = seeds["user_id"]

    # Create 5 distinct document types (one per test group)
    doc_types = {}
    for i in range(1, 6):
        dt_id = uuid.uuid4()
        await pg_conn.execute(
            "INSERT INTO document_types (id, organization_id, name, code, category) VALUES ($1, $2, $3, $4, 'tax');",
            dt_id,
            org_id,
            f"DocType {i}",
            f"DT_{i}_{uuid.uuid4().hex[:6]}",
        )
        doc_types[i] = dt_id

    # -------------------------------------------------------------------------
    # Group 1 (Scenario 1): Legacy-verified V1 followed by review-approved V2
    # -------------------------------------------------------------------------
    g1_v1 = uuid.uuid4()
    g1_v2 = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, previous_version_id, version,
            status, file_path, file_name, file_size_bytes, file_mime_type, file_hash_sha256, is_active_version
        ) VALUES
        ($1, $3, $4, $5, NULL, 1, 'verified', 's/1.pdf', '1.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000011', false),
        ($2, $3, $4, $5, $1, 2, 'verified', 's/2.pdf', '2.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000012', true);
        """,
        g1_v1,
        g1_v2,
        org_id,
        sup_id,
        doc_types[1],
    )
    # V2 has review decision 'approved'
    await pg_conn.execute(
        "INSERT INTO document_reviews (id, organization_id, document_id, reviewer_id, decision, verified_data) VALUES ($1, $2, $3, $4, 'approved', '{}'::jsonb);",
        uuid.uuid4(),
        org_id,
        g1_v2,
        user_id,
    )

    # -------------------------------------------------------------------------
    # Group 2 (Scenario 2): Review-approved V1 followed by legacy-verified V2
    # -------------------------------------------------------------------------
    g2_v1 = uuid.uuid4()
    g2_v2 = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, previous_version_id, version,
            status, file_path, file_name, file_size_bytes, file_mime_type, file_hash_sha256, is_active_version
        ) VALUES
        ($1, $3, $4, $5, NULL, 1, 'verified', 's/1.pdf', '1.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000021', false),
        ($2, $3, $4, $5, $1, 2, 'verified', 's/2.pdf', '2.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000022', true);
        """,
        g2_v1,
        g2_v2,
        org_id,
        sup_id,
        doc_types[2],
    )
    # V1 has review decision 'approved'
    await pg_conn.execute(
        "INSERT INTO document_reviews (id, organization_id, document_id, reviewer_id, decision, verified_data) VALUES ($1, $2, $3, $4, 'approved', '{}'::jsonb);",
        uuid.uuid4(),
        org_id,
        g2_v1,
        user_id,
    )

    # -------------------------------------------------------------------------
    # Group 3 (Scenario 3): Approved V1 followed by rejected V2
    # -------------------------------------------------------------------------
    g3_v1 = uuid.uuid4()
    g3_v2 = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, previous_version_id, version,
            status, file_path, file_name, file_size_bytes, file_mime_type, file_hash_sha256, is_active_version
        ) VALUES
        ($1, $3, $4, $5, NULL, 1, 'verified', 's/1.pdf', '1.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000031', true),
        ($2, $3, $4, $5, $1, 2, 'rejected', 's/2.pdf', '2.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000032', false);
        """,
        g3_v1,
        g3_v2,
        org_id,
        sup_id,
        doc_types[3],
    )
    await pg_conn.execute(
        "INSERT INTO document_reviews (id, organization_id, document_id, reviewer_id, decision, verified_data) VALUES ($1, $2, $3, $4, 'approved', '{}'::jsonb);",
        uuid.uuid4(),
        org_id,
        g3_v1,
        user_id,
    )
    await pg_conn.execute(
        "INSERT INTO document_reviews (id, organization_id, document_id, reviewer_id, decision, verified_data) VALUES ($1, $2, $3, $4, 'rejected', '{}'::jsonb);",
        uuid.uuid4(),
        org_id,
        g3_v2,
        user_id,
    )

    # -------------------------------------------------------------------------
    # Group 4 (Scenario 4): Multiple approved versions with mixed provenance (V1 legacy, V2 review, V3 review, V4 rejected)
    # -------------------------------------------------------------------------
    g4_v1 = uuid.uuid4()
    g4_v2 = uuid.uuid4()
    g4_v3 = uuid.uuid4()
    g4_v4 = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, previous_version_id, version,
            status, file_path, file_name, file_size_bytes, file_mime_type, file_hash_sha256, is_active_version
        ) VALUES
        ($1, $5, $6, $7, NULL, 1, 'verified', 's/1.pdf', '1.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000041', false),
        ($2, $5, $6, $7, $1, 2, 'verified', 's/2.pdf', '2.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000042', false),
        ($3, $5, $6, $7, $2, 3, 'verified', 's/3.pdf', '3.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000043', true),
        ($4, $5, $6, $7, $3, 4, 'rejected', 's/4.pdf', '4.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000044', false);
        """,
        g4_v1,
        g4_v2,
        g4_v3,
        g4_v4,
        org_id,
        sup_id,
        doc_types[4],
    )
    # V2 and V3 have review 'approved', V4 has review 'rejected'
    await pg_conn.execute(
        "INSERT INTO document_reviews (id, organization_id, document_id, reviewer_id, decision, verified_data) VALUES ($1, $2, $3, $4, 'approved', '{}'::jsonb);",
        uuid.uuid4(),
        org_id,
        g4_v2,
        user_id,
    )
    await pg_conn.execute(
        "INSERT INTO document_reviews (id, organization_id, document_id, reviewer_id, decision, verified_data) VALUES ($1, $2, $3, $4, 'approved', '{}'::jsonb);",
        uuid.uuid4(),
        org_id,
        g4_v3,
        user_id,
    )
    await pg_conn.execute(
        "INSERT INTO document_reviews (id, organization_id, document_id, reviewer_id, decision, verified_data) VALUES ($1, $2, $3, $4, 'rejected', '{}'::jsonb);",
        uuid.uuid4(),
        org_id,
        g4_v4,
        user_id,
    )

    # -------------------------------------------------------------------------
    # Group 5 (Scenario 5): No approved version in group (V1 uploaded, V2 rejected)
    # -------------------------------------------------------------------------
    g5_v1 = uuid.uuid4()
    g5_v2 = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, previous_version_id, version,
            status, file_path, file_name, file_size_bytes, file_mime_type, file_hash_sha256, is_active_version
        ) VALUES
        ($1, $3, $4, $5, NULL, 1, 'uploaded', 's/1.pdf', '1.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000051', false),
        ($2, $3, $4, $5, $1, 2, 'rejected', 's/2.pdf', '2.pdf', 10, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000052', false);
        """,
        g5_v1,
        g5_v2,
        org_id,
        sup_id,
        doc_types[5],
    )
    await pg_conn.execute(
        "INSERT INTO document_reviews (id, organization_id, document_id, reviewer_id, decision, verified_data) VALUES ($1, $2, $3, $4, 'rejected', '{}'::jsonb);",
        uuid.uuid4(),
        org_id,
        g5_v2,
        user_id,
    )

    # Execute forward migration
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)

    # Check Scenario 1: V2 effective, V1 not effective
    r1_v1 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g1_v1
    )
    r1_v2 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g1_v2
    )
    assert r1_v1["approval_provenance"] == "legacy_verified"
    assert r1_v2["approval_provenance"] == "review"
    assert r1_v1["is_effective_version"] is False
    assert r1_v2["is_effective_version"] is True

    # Check Scenario 2: V2 effective, V1 not effective
    r2_v1 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g2_v1
    )
    r2_v2 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g2_v2
    )
    assert r2_v1["approval_provenance"] == "review"
    assert r2_v2["approval_provenance"] == "legacy_verified"
    assert r2_v1["is_effective_version"] is False
    assert r2_v2["is_effective_version"] is True

    # Check Scenario 3: V1 effective, V2 not effective
    r3_v1 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g3_v1
    )
    r3_v2 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g3_v2
    )
    assert r3_v1["approval_provenance"] == "review"
    assert r3_v2["approval_provenance"] is None
    assert r3_v1["is_effective_version"] is True
    assert r3_v2["is_effective_version"] is False

    # Check Scenario 4: V3 effective, V1, V2, V4 not effective
    r4_v1 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g4_v1
    )
    r4_v2 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g4_v2
    )
    r4_v3 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g4_v3
    )
    r4_v4 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g4_v4
    )
    assert r4_v1["approval_provenance"] == "legacy_verified"
    assert r4_v2["approval_provenance"] == "review"
    assert r4_v3["approval_provenance"] == "review"
    assert r4_v4["approval_provenance"] is None
    assert r4_v1["is_effective_version"] is False
    assert r4_v2["is_effective_version"] is False
    assert r4_v3["is_effective_version"] is True
    assert r4_v4["is_effective_version"] is False

    # Check Scenario 5: Exactly zero effective versions in group 5
    r5_v1 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g5_v1
    )
    r5_v2 = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", g5_v2
    )
    assert r5_v1["approval_provenance"] is None
    assert r5_v2["approval_provenance"] is None
    assert r5_v1["is_effective_version"] is False
    assert r5_v2["is_effective_version"] is False

    # Verify unique partial index uq_supplier_docs_effective_group is satisfied across all groups
    multi_effective = await pg_conn.fetch(
        """
        SELECT document_group_id, COUNT(*) AS eff_count
        FROM supplier_documents
        WHERE is_effective_version = true
        GROUP BY document_group_id
        HAVING COUNT(*) > 1;
        """
    )
    assert len(multi_effective) == 0


async def test_legacy_verified_revoked_by_review_is_not_effective(
    pg_conn: asyncpg.Connection,
):
    """A legacy-verified document that received a subsequent rejected review must not be effective."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)

    doc_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5, NULL, 1, 'verified', 's/revoked.pdf', 'revoked.pdf', 100, 'application/pdf',
            '0000000000000000000000000000000000000000000000000000000000000055', false
        );
        """,
        doc_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        seeds["req_id"],
    )
    # A subsequent review rejected it
    await pg_conn.execute(
        """
        INSERT INTO document_reviews (id, organization_id, document_id, reviewer_id, decision, verified_data)
        VALUES ($1, $2, $3, $4, 'rejected', '{}'::jsonb);
        """,
        uuid.uuid4(),
        seeds["org_id"],
        doc_id,
        seeds["user_id"],
    )

    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)

    doc = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", doc_id
    )
    assert doc["is_effective_version"] is False
    assert doc["approval_provenance"] == "legacy_verified"


# =============================================================================
# 3. Requirement Applicability Scope & Processing Status Consistency
# =============================================================================


async def test_requirement_applicability_scope_values(
    pg_conn: asyncpg.Connection,
):
    """Test document_requirements supports 'supplier', 'site', and 'either'."""
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)
    org_id = uuid.uuid4()
    dt_id = uuid.uuid4()
    await pg_conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Scope Org', $2);",
        org_id,
        f"scope-{uuid.uuid4().hex[:8]}",
    )
    await pg_conn.execute(
        "INSERT INTO document_types (id, organization_id, name, code, category) VALUES ($1, $2, 'Fire NOC', $3, 'safety');",
        dt_id,
        org_id,
        f"FNOC_{uuid.uuid4().hex[:6]}",
    )

    # Valid scopes: 'supplier', 'site', 'either'
    for valid_scope in ("supplier", "site", "either"):
        await pg_conn.execute(
            """
            INSERT INTO document_requirements (id, organization_id, document_type_id, name, applicability_scope)
            VALUES ($1, $2, $3, $4, $5);
            """,
            uuid.uuid4(),
            org_id,
            dt_id,
            f"Req {valid_scope}",
            valid_scope,
        )

    # Invalid scope should be rejected by check constraint
    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await pg_conn.execute(
            """
            INSERT INTO document_requirements (id, organization_id, document_type_id, name, applicability_scope)
            VALUES ($1, $2, $3, 'Invalid Req', 'enterprise');
            """,
            uuid.uuid4(),
            org_id,
            dt_id,
        )
    assert "ck_doc_requirements_applicability_scope" in str(exc_info.value)

    await pg_conn.execute(
        "DELETE FROM document_requirements WHERE organization_id = $1;", org_id
    )
    await pg_conn.execute(
        "DELETE FROM document_types WHERE organization_id = $1;", org_id
    )
    await pg_conn.execute("DELETE FROM organizations WHERE id = $1;", org_id)


async def test_processing_status_consistency(pg_conn: asyncpg.Connection):
    """Test supplier_documents processing_status allows uploaded, processing, extracted, failed, and forbids completed."""
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)
    seeds = await _seed_base_entities(pg_conn)
    org_id = seeds["org_id"]
    sup_id = seeds["supplier_id"]
    dt_id = seeds["doc_type_id"]

    # Create a group
    group_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_document_groups (id, organization_id, supplier_id, document_type_id, display_name)
        VALUES ($1, $2, $3, $4, 'Test Group');
        """,
        group_id,
        org_id,
        sup_id,
        dt_id,
    )

    # Valid processing statuses: 'uploaded', 'processing', 'extracted', 'failed'
    v = 1
    for valid_status in ("uploaded", "processing", "extracted", "failed"):
        await pg_conn.execute(
            """
            INSERT INTO supplier_documents (
                id, organization_id, supplier_id, document_type_id, document_group_id,
                version, processing_status, file_path, file_name, file_size_bytes,
                file_mime_type, file_hash_sha256
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, 's/f.pdf', 'f.pdf', 100, 'application/pdf', $8
            );
            """,
            uuid.uuid4(),
            org_id,
            sup_id,
            dt_id,
            group_id,
            v,
            valid_status,
            f"{v:064d}",
        )
        v += 1

    # 'completed' is redundant and must be rejected by check constraint
    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await pg_conn.execute(
            """
            INSERT INTO supplier_documents (
                id, organization_id, supplier_id, document_type_id, document_group_id,
                version, processing_status, file_path, file_name, file_size_bytes,
                file_mime_type, file_hash_sha256
            ) VALUES (
                $1, $2, $3, $4, $5,
                99, 'completed', 's/f.pdf', 'f.pdf', 100, 'application/pdf', '9999999999999999999999999999999999999999999999999999999999999999'
            );
            """,
            uuid.uuid4(),
            org_id,
            sup_id,
            dt_id,
            group_id,
        )
    assert "ck_supplier_docs_processing_status" in str(exc_info.value)

    await pg_conn.execute(
        "DELETE FROM supplier_documents WHERE document_group_id = $1;", group_id
    )
    await pg_conn.execute(
        "DELETE FROM supplier_document_groups WHERE id = $1;", group_id
    )


# =============================================================================
# 4. Database Triggers & Discrepancies Tests
# =============================================================================


async def test_triggers_and_discrepancy_audit_retention(
    pg_conn: asyncpg.Connection,
):
    """Test append-only discrepancies, multiple discrepancy audit retention, and immutability."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)

    doc_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5, NULL, 1, 'verified', 's/doc.pdf', 'doc.pdf', 100, 'application/pdf',
            '0000000000000000000000000000000000000000000000000000000000000010', true
        );
        """,
        doc_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        seeds["req_id"],
    )

    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)

    doc = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", doc_id
    )
    assert doc is not None

    # Lineage immutability trigger
    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await pg_conn.execute(
            "UPDATE supplier_documents SET version = 999 WHERE id = $1;",
            doc["id"],
        )
    assert "version on supplier_documents is immutable" in str(exc_info.value)

    # Multiple discrepancies for same document (audit log retention without suppression)
    disc1_id = uuid.uuid4()
    disc2_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_document_migration_discrepancies (id, organization_id, document_id, discrepancy_type, legacy_status)
        VALUES ($1, $2, $3, 'status_review_mismatch', 'verified');
        """,
        disc1_id,
        seeds["org_id"],
        doc_id,
    )
    await pg_conn.execute(
        """
        INSERT INTO supplier_document_migration_discrepancies (id, organization_id, document_id, discrepancy_type, legacy_status)
        VALUES ($1, $2, $3, 'status_review_mismatch', 'verified');
        """,
        disc2_id,
        seeds["org_id"],
        doc_id,
    )
    discs = await pg_conn.fetch(
        "SELECT * FROM supplier_document_migration_discrepancies WHERE document_id = $1 AND discrepancy_type = 'status_review_mismatch';",
        doc_id,
    )
    assert len(discs) >= 2

    # Discrepancies append-only triggers
    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await pg_conn.execute(
            "UPDATE supplier_document_migration_discrepancies SET discrepancy_type = 'custom' WHERE id = $1;",
            disc1_id,
        )
    assert "is append-only and cannot be updated or deleted" in str(exc_info.value)

    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await pg_conn.execute(
            "DELETE FROM supplier_document_migration_discrepancies WHERE id = $1;",
            disc1_id,
        )
    assert "is append-only and cannot be updated or deleted" in str(exc_info.value)

    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await pg_conn.execute("TRUNCATE supplier_document_migration_discrepancies;")
    assert "cannot be truncated" in str(exc_info.value)


# =============================================================================
# 5. Exhaustive Downgrade Preflight Assertions & Reversal Tests
# =============================================================================


async def _setup_clean_migrated_db(pg_conn: asyncpg.Connection):
    """Seed base entities, 1 valid document, and upgrade to HEAD_REVISION."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)
    doc_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5, NULL, 1, 'verified', 's/doc.pdf', 'doc.pdf', 100, 'application/pdf',
            '0000000000000000000000000000000000000000000000000000000000000099', true
        );
        """,
        doc_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        seeds["req_id"],
    )
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)
    return seeds, doc_id


async def test_downgrade_preflight_blocks_on_non_empty_sites(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort before destructive DDL if supplier_sites is not empty."""
    seeds, _ = await _setup_clean_migrated_db(pg_conn)
    site_id = uuid.uuid4()
    await pg_conn.execute(
        "INSERT INTO supplier_sites (id, organization_id, supplier_id, site_code, site_name) VALUES ($1, $2, $3, 'PLANT-1', 'Main Plant');",
        site_id,
        seeds["org_id"],
        seeds["supplier_id"],
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert "supplier_sites table is not empty" in str(exc_info.value)

    # Verify schema remained intact
    col = await pg_conn.fetchrow(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'supplier_documents' AND column_name = 'document_group_id';"
    )
    assert col is not None

    await pg_conn.execute("DELETE FROM supplier_sites WHERE id = $1;", site_id)


async def test_downgrade_preflight_blocks_on_post_migration_documents(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort if documents created post-migration exist (legacy_status IS NULL)."""
    seeds, _ = await _setup_clean_migrated_db(pg_conn)
    group = await pg_conn.fetchrow("SELECT * FROM supplier_document_groups LIMIT 1;")
    new_doc_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, document_group_id,
            version, processing_status, legacy_status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256
        ) VALUES ($1, $2, $3, $4, $5, 99, 'extracted', NULL, 's/new.pdf', 'new.pdf', 100, 'application/pdf', '9999999999999999999999999999999999999999999999999999999999999999');
        """,
        new_doc_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        group["id"],
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert "post-migration documents exist (legacy_status IS NULL)" in str(
        exc_info.value
    )

    await pg_conn.execute("DELETE FROM supplier_documents WHERE id = $1;", new_doc_id)


async def test_downgrade_preflight_blocks_on_failed_processing_status(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort if documents have processing_status = 'failed'."""
    _seeds, doc_id = await _setup_clean_migrated_db(pg_conn)
    await pg_conn.execute(
        "UPDATE supplier_documents SET processing_status = 'failed' WHERE id = $1;",
        doc_id,
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert "documents have processing_status = 'failed'" in str(exc_info.value)

    await pg_conn.execute(
        "UPDATE supplier_documents SET processing_status = 'extracted' WHERE id = $1;",
        doc_id,
    )


async def test_downgrade_preflight_blocks_on_custom_requirement_attributes(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort if requirements have applicability_scope != 'supplier' or precedence_order != 100."""
    seeds, _ = await _setup_clean_migrated_db(pg_conn)
    await pg_conn.execute(
        "UPDATE document_requirements SET applicability_scope = 'either' WHERE id = $1;",
        seeds["req_id"],
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert "non-default applicability_scope or precedence_order" in str(exc_info.value)

    await pg_conn.execute(
        "UPDATE document_requirements SET applicability_scope = 'supplier' WHERE id = $1;",
        seeds["req_id"],
    )


async def test_downgrade_succeeds_on_valid_multi_requirement_lineage(
    pg_conn: asyncpg.Connection,
):
    """Downgrade succeeds on valid multi-requirement lineage (V1->R1, V2->R2) and restores original requirement IDs exactly."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)
    org_id = seeds["org_id"]
    sup_id = seeds["supplier_id"]
    dt_id = seeds["doc_type_id"]
    req1_id = seeds["req_id"]

    # Create second requirement
    req2_id = uuid.uuid4()
    await pg_conn.execute(
        "INSERT INTO document_requirements (id, organization_id, document_type_id, name) VALUES ($1, $2, $3, 'Second Req');",
        req2_id,
        org_id,
        dt_id,
    )

    # Insert V1 with req1
    v1_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5, NULL, 1, 'verified', 's/v1.pdf', 'v1.pdf', 100,
            'application/pdf', '0000000000000000000000000000000000000000000000000000000000000001', false
        );
        """,
        v1_id,
        org_id,
        sup_id,
        dt_id,
        req1_id,
    )

    # Insert V2 with req2 pointing to V1
    v2_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5, $6, 2, 'verified', 's/v2.pdf', 'v2.pdf', 100,
            'application/pdf', '0000000000000000000000000000000000000000000000000000000000000002', true
        );
        """,
        v2_id,
        org_id,
        sup_id,
        dt_id,
        req2_id,
        v1_id,
    )

    # Upgrade to HEAD
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)

    # Verify both links were created in the migrated state
    group = await pg_conn.fetchrow("SELECT id FROM supplier_document_groups LIMIT 1;")
    links = await pg_conn.fetch(
        "SELECT requirement_id FROM document_group_requirement_links WHERE document_group_id = $1;",
        group["id"],
    )
    linked_reqs = {r["requirement_id"] for r in links}
    assert linked_reqs == {req1_id, req2_id}

    # Downgrade to BASE_REVISION succeeds without error
    await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)

    # Verify supplier_documents restored legacy requirement_id values exactly
    docs = await pg_conn.fetch(
        "SELECT id, requirement_id, version FROM supplier_documents WHERE id IN ($1, $2);",
        v1_id,
        v2_id,
    )
    doc_map = {d["id"]: d["requirement_id"] for d in docs}
    assert doc_map[v1_id] == req1_id
    assert doc_map[v2_id] == req2_id


async def test_downgrade_succeeds_when_multiple_documents_share_requirement(
    pg_conn: asyncpg.Connection,
):
    """Downgrade succeeds when multiple documents in the same group share the same legacy requirement."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)
    org_id = seeds["org_id"]
    sup_id = seeds["supplier_id"]
    dt_id = seeds["doc_type_id"]
    req_id = seeds["req_id"]

    v1_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5, NULL, 1, 'verified', 's/v1.pdf', 'v1.pdf', 100,
            'application/pdf', '0000000000000000000000000000000000000000000000000000000000000001', false
        );
        """,
        v1_id,
        org_id,
        sup_id,
        dt_id,
        req_id,
    )

    v2_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5, $6, 2, 'verified', 's/v2.pdf', 'v2.pdf', 100,
            'application/pdf', '0000000000000000000000000000000000000000000000000000000000000002', true
        );
        """,
        v2_id,
        org_id,
        sup_id,
        dt_id,
        req_id,
        v1_id,
    )

    # Upgrade to HEAD
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)

    # Verify single requirement link was created
    group = await pg_conn.fetchrow("SELECT id FROM supplier_document_groups LIMIT 1;")
    links = await pg_conn.fetch(
        "SELECT requirement_id FROM document_group_requirement_links WHERE document_group_id = $1;",
        group["id"],
    )
    assert len(links) == 1
    assert links[0]["requirement_id"] == req_id

    # Downgrade to BASE_REVISION succeeds without error
    await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)

    # Verify both restored req_id
    docs = await pg_conn.fetch(
        "SELECT id, requirement_id FROM supplier_documents WHERE id IN ($1, $2);",
        v1_id,
        v2_id,
    )
    assert all(d["requirement_id"] == req_id for d in docs)


async def test_downgrade_preflight_blocks_on_added_requirement_link(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort if an extra requirement link was added post-migration (not present on legacy documents)."""
    seeds, _ = await _setup_clean_migrated_db(pg_conn)
    group = await pg_conn.fetchrow("SELECT * FROM supplier_document_groups LIMIT 1;")

    # Add a second requirement and link it to the group (while document only has req_id)
    req2_id = uuid.uuid4()
    await pg_conn.execute(
        "INSERT INTO document_requirements (id, organization_id, document_type_id, name) VALUES ($1, $2, $3, 'Added Req');",
        req2_id,
        seeds["org_id"],
        seeds["doc_type_id"],
    )
    link2_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO document_group_requirement_links (id, organization_id, document_group_id, requirement_id, link_origin)
        VALUES ($1, $2, $3, $4, 'manual');
        """,
        link2_id,
        seeds["org_id"],
        group["id"],
        req2_id,
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert "added post-migration (not present in legacy document requirements)" in str(
        exc_info.value
    )

    await pg_conn.execute(
        "DELETE FROM document_group_requirement_links WHERE id = $1;", link2_id
    )
    await pg_conn.execute("DELETE FROM document_requirements WHERE id = $1;", req2_id)


async def test_downgrade_preflight_blocks_on_removed_requirement_link(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort if a legacy-derived requirement link was removed post-migration."""
    _seeds, _ = await _setup_clean_migrated_db(pg_conn)
    group = await pg_conn.fetchrow("SELECT * FROM supplier_document_groups LIMIT 1;")

    # Delete the legacy-derived link from document_group_requirement_links
    await pg_conn.execute(
        "DELETE FROM document_group_requirement_links WHERE document_group_id = $1;",
        group["id"],
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert (
        "no longer linked in document_group_requirement_links (requirement association was removed post-migration)"
        in str(exc_info.value)
    )


async def test_downgrade_preflight_blocks_on_changed_requirement_link(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort if a group's requirement link was changed from one requirement to another."""
    seeds, _ = await _setup_clean_migrated_db(pg_conn)
    group = await pg_conn.fetchrow("SELECT * FROM supplier_document_groups LIMIT 1;")

    # Create new requirement
    req2_id = uuid.uuid4()
    await pg_conn.execute(
        "INSERT INTO document_requirements (id, organization_id, document_type_id, name) VALUES ($1, $2, $3, 'Replacement Req');",
        req2_id,
        seeds["org_id"],
        seeds["doc_type_id"],
    )

    # Change the existing link to point to req2_id
    await pg_conn.execute(
        "UPDATE document_group_requirement_links SET requirement_id = $1 WHERE document_group_id = $2;",
        req2_id,
        group["id"],
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert "added post-migration (not present in legacy document requirements)" in str(
        exc_info.value
    )

    # Restore link to original requirement
    await pg_conn.execute(
        "UPDATE document_group_requirement_links SET requirement_id = $1 WHERE document_group_id = $2;",
        seeds["req_id"],
        group["id"],
    )
    await pg_conn.execute("DELETE FROM document_requirements WHERE id = $1;", req2_id)


async def test_downgrade_preflight_blocks_on_empty_group_with_links(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort if an empty group has requirement links."""
    seeds, _ = await _setup_clean_migrated_db(pg_conn)
    dt = await pg_conn.fetchrow(
        "SELECT name FROM document_types WHERE id = $1;", seeds["doc_type_id"]
    )

    empty_group_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_document_groups (id, organization_id, supplier_id, document_type_id, display_name)
        VALUES ($1, $2, $3, $4, $5);
        """,
        empty_group_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        dt["name"],
    )

    link_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO document_group_requirement_links (id, organization_id, document_group_id, requirement_id, link_origin)
        VALUES ($1, $2, $3, $4, 'manual');
        """,
        link_id,
        seeds["org_id"],
        empty_group_id,
        seeds["req_id"],
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert "added post-migration (not present in legacy document requirements)" in str(
        exc_info.value
    )

    await pg_conn.execute(
        "DELETE FROM document_group_requirement_links WHERE id = $1;", link_id
    )
    await pg_conn.execute(
        "DELETE FROM supplier_document_groups WHERE id = $1;", empty_group_id
    )


async def test_downgrade_succeeds_with_null_legacy_requirement(
    pg_conn: asyncpg.Connection,
):
    """Downgrade succeeds when document has no legacy requirement (NULL) and restores NULL exactly."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)
    org_id = seeds["org_id"]
    sup_id = seeds["supplier_id"]
    dt_id = seeds["doc_type_id"]

    doc_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, NULL, NULL, 1, 'verified', 's/null_req.pdf', 'null_req.pdf', 100,
            'application/pdf', '0000000000000000000000000000000000000000000000000000000000000003', true
        );
        """,
        doc_id,
        org_id,
        sup_id,
        dt_id,
    )

    # Upgrade to HEAD
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)

    # Verify no links created for null requirement
    group = await pg_conn.fetchrow("SELECT id FROM supplier_document_groups LIMIT 1;")
    links = await pg_conn.fetch(
        "SELECT requirement_id FROM document_group_requirement_links WHERE document_group_id = $1;",
        group["id"],
    )
    assert len(links) == 0

    # Downgrade to BASE_REVISION succeeds without error
    await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)

    # Verify restored document requirement_id is NULL
    doc = await pg_conn.fetchrow(
        "SELECT id, requirement_id FROM supplier_documents WHERE id = $1;", doc_id
    )
    assert doc["requirement_id"] is None


async def test_reupgrade_succeeds_after_multi_requirement_downgrade(
    pg_conn: asyncpg.Connection,
):
    """Re-upgrade to HEAD_REVISION succeeds cleanly after a multi-requirement lineage downgrade."""
    await _cleanup_base_tables(pg_conn)
    seeds = await _seed_base_entities(pg_conn)
    org_id = seeds["org_id"]
    sup_id = seeds["supplier_id"]
    dt_id = seeds["doc_type_id"]
    req1_id = seeds["req_id"]

    req2_id = uuid.uuid4()
    await pg_conn.execute(
        "INSERT INTO document_requirements (id, organization_id, document_type_id, name) VALUES ($1, $2, $3, 'Second Req');",
        req2_id,
        org_id,
        dt_id,
    )

    v1_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5, NULL, 1, 'verified', 's/v1.pdf', 'v1.pdf', 100,
            'application/pdf', '0000000000000000000000000000000000000000000000000000000000000001', false
        );
        """,
        v1_id,
        org_id,
        sup_id,
        dt_id,
        req1_id,
    )

    v2_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, requirement_id,
            previous_version_id, version, status, file_path, file_name, file_size_bytes,
            file_mime_type, file_hash_sha256, is_active_version
        ) VALUES (
            $1, $2, $3, $4, $5, $6, 2, 'verified', 's/v2.pdf', 'v2.pdf', 100,
            'application/pdf', '0000000000000000000000000000000000000000000000000000000000000002', true
        );
        """,
        v2_id,
        org_id,
        sup_id,
        dt_id,
        req2_id,
        v1_id,
    )

    # 1. Initial upgrade
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)

    # 2. Downgrade to base
    await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)

    # 3. Re-upgrade to HEAD
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)

    # Verify groups and links in re-upgraded state
    group = await pg_conn.fetchrow("SELECT id FROM supplier_document_groups LIMIT 1;")
    assert group is not None
    links = await pg_conn.fetch(
        "SELECT requirement_id FROM document_group_requirement_links WHERE document_group_id = $1;",
        group["id"],
    )
    linked_reqs = {r["requirement_id"] for r in links}
    assert linked_reqs == {req1_id, req2_id}

    # Verify both documents are in the group with correct legacy_requirement_id
    migrated_docs = await pg_conn.fetch(
        "SELECT id, document_group_id, legacy_requirement_id FROM supplier_documents WHERE id IN ($1, $2);",
        v1_id,
        v2_id,
    )
    assert len(migrated_docs) == 2
    for doc in migrated_docs:
        assert doc["document_group_id"] == group["id"]
    migrated_map = {d["id"]: d["legacy_requirement_id"] for d in migrated_docs}
    assert migrated_map[v1_id] == req1_id
    assert migrated_map[v2_id] == req2_id

    # Downgrade back to base to leave database clean
    await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)


async def test_downgrade_succeeds_when_requirement_associations_unchanged(
    pg_conn: asyncpg.Connection,
):
    """Positive test: Downgrade succeeds when requirement associations are unmodified, faithfully restoring legacy state."""
    seeds, doc_id = await _setup_clean_migrated_db(pg_conn)

    # Verify initial migrated state has 1 link matching legacy_requirement_id
    link = await pg_conn.fetchrow(
        "SELECT * FROM document_group_requirement_links WHERE requirement_id = $1;",
        seeds["req_id"],
    )
    assert link is not None

    # Downgrade to BASE_REVISION succeeds without error
    await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)

    # Verify supplier_documents restored legacy requirement_id exactly
    legacy_doc = await pg_conn.fetchrow(
        "SELECT id, requirement_id, is_active_version, status FROM supplier_documents WHERE id = $1;",
        doc_id,
    )
    assert legacy_doc["requirement_id"] == seeds["req_id"]
    assert legacy_doc["is_active_version"] is True
    assert legacy_doc["status"] == "verified"

    # Re-upgrade to HEAD_REVISION succeeds cleanly
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)


async def test_downgrade_preflight_blocks_on_custom_group_metadata(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort if group has custom metadata (document_reference, scope, authority)."""
    _seeds, _ = await _setup_clean_migrated_db(pg_conn)
    group = await pg_conn.fetchrow("SELECT * FROM supplier_document_groups LIMIT 1;")

    await pg_conn.execute(
        "UPDATE supplier_document_groups SET document_reference = 'CERT-REF-999' WHERE id = $1;",
        group["id"],
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert "document groups have custom metadata" in str(exc_info.value)

    await pg_conn.execute(
        "UPDATE supplier_document_groups SET document_reference = NULL WHERE id = $1;",
        group["id"],
    )


async def test_downgrade_preflight_blocks_on_multiple_effective_versions_per_supplier_doc_type(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort if multiple effective versions exist for the same supplier and document type."""
    seeds, _ = await _setup_clean_migrated_db(pg_conn)

    # Create second group for same supplier and doc_type
    group2_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_document_groups (id, organization_id, supplier_id, document_type_id, display_name)
        VALUES ($1, $2, $3, $4, 'GST Certificate 2');
        """,
        group2_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
    )

    # Insert effective document in group 2 (with version 2 so it doesn't collide with group 1 version 1)
    doc2_id = uuid.uuid4()
    await pg_conn.execute(
        """
        INSERT INTO supplier_documents (
            id, organization_id, supplier_id, document_type_id, document_group_id,
            version, processing_status, legacy_status, is_effective_version,
            file_path, file_name, file_size_bytes, file_mime_type, file_hash_sha256
        ) VALUES (
            $1, $2, $3, $4, $5, 2, 'extracted', 'verified', true,
            's/doc2.pdf', 'doc2.pdf', 100, 'application/pdf', '0000000000000000000000000000000000000000000000000000000000000088'
        );
        """,
        doc2_id,
        seeds["org_id"],
        seeds["supplier_id"],
        seeds["doc_type_id"],
        group2_id,
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert (
        "pairs have multiple effective versions" in str(exc_info.value)
        or "unique constraint" in str(exc_info.value).lower()
    )

    await pg_conn.execute("DELETE FROM supplier_documents WHERE id = $1;", doc2_id)
    await pg_conn.execute(
        "DELETE FROM supplier_document_groups WHERE id = $1;", group2_id
    )


async def test_downgrade_preflight_blocks_on_revoked_effective_document(
    pg_conn: asyncpg.Connection,
):
    """Downgrade must abort if an effective document was revoked by a latest rejected review."""
    seeds, doc_id = await _setup_clean_migrated_db(pg_conn)

    # Insert a review with decision 'rejected' for the effective document
    await pg_conn.execute(
        """
        INSERT INTO document_reviews (id, organization_id, document_id, reviewer_id, decision, verified_data)
        VALUES ($1, $2, $3, $4, 'rejected', '{}'::jsonb);
        """,
        uuid.uuid4(),
        seeds["org_id"],
        doc_id,
        seeds["user_id"],
    )

    with pytest.raises(Exception) as exc_info:
        await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
    assert "effective documents have been explicitly revoked" in str(exc_info.value)

    await pg_conn.execute(
        "DELETE FROM document_reviews WHERE document_id = $1;", doc_id
    )


async def test_clean_downgrade_and_reupgrade_roundtrip(
    pg_conn: asyncpg.Connection,
):
    """Test successful clean downgrade on legacy-compatible data and subsequent re-upgrade."""
    _seeds, doc_id = await _setup_clean_migrated_db(pg_conn)

    # Run clean downgrade
    await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)

    # 1. Verify Phase 3.4 tables are dropped
    tables = await pg_conn.fetch(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name IN (
            'supplier_sites',
            'supplier_document_groups',
            'document_group_requirement_links',
            'supplier_document_migration_discrepancies'
        );
        """
    )
    assert len(tables) == 0

    # 2. Verify supplier_documents restored legacy columns
    cols = await pg_conn.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'supplier_documents'
          AND column_name IN ('requirement_id', 'is_active_version', 'status', 'document_group_id', 'processing_status');
        """
    )
    col_names = {c["column_name"] for c in cols}
    assert "requirement_id" in col_names
    assert "is_active_version" in col_names
    assert "status" in col_names
    assert "document_group_id" not in col_names
    assert "processing_status" not in col_names

    # 3. Test re-upgrade to head succeeds cleanly
    await asyncio.to_thread(_run_alembic, "upgrade", HEAD_REVISION)

    groups = await pg_conn.fetch("SELECT * FROM supplier_document_groups;")
    assert len(groups) == 1

    migrated_doc = await pg_conn.fetchrow(
        "SELECT * FROM supplier_documents WHERE id = $1;", doc_id
    )
    assert migrated_doc["document_group_id"] == groups[0]["id"]
    assert migrated_doc["processing_status"] == "extracted"
    assert migrated_doc["is_effective_version"] is True

    # Downgrade back to base revision
    await asyncio.to_thread(_run_alembic, "downgrade", BASE_REVISION)
