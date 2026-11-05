import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, settings
from app.db.session import check_database
from app.models import (
    AuditEvent,
    DocumentExtraction,
    DocumentRequirement,
    DocumentReview,
    DocumentType,
    Membership,
    Organization,
    Supplier,
    SupplierDocument,
    User,
)


@pytest.fixture
async def test_engine():
    """Create test engine targeting vendorflow_test.

    CRITICAL SAFETY RULE:
    Strictly verifies that the target database is 'vendorflow_test' before connecting.
    """
    test_url = settings.get_test_database_url()
    if test_url.database != "vendorflow_test":
        raise RuntimeError(
            f"Safety guard failure: Expected 'vendorflow_test', got '{test_url.database}'."
        )
    engine = create_async_engine(test_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(test_engine):
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest.fixture
async def tenant_fixture(db_session: AsyncSession):
    # Tenant A
    org_a = Organization(
        name="Tata AutoComp Pune",
        slug=f"tata-autocomp-{uuid.uuid4().hex[:8]}",
        status="active",
    )
    # Tenant B
    org_b = Organization(
        name="Bharat Forge Pune",
        slug=f"bharat-forge-{uuid.uuid4().hex[:8]}",
        status="active",
    )
    # Users
    user_a = User(
        email=f"reviewer_a_{uuid.uuid4().hex[:8]}@tata.com",
        hashed_password="hashed_secret_a",
        full_name="Rajesh Sharma",
    )
    user_b = User(
        email=f"reviewer_b_{uuid.uuid4().hex[:8]}@bharat.com",
        hashed_password="hashed_secret_b",
        full_name="Anil Kulkarni",
    )
    db_session.add_all([org_a, org_b, user_a, user_b])
    await db_session.flush()

    # Memberships
    member_a = Membership(
        organization_id=org_a.id,
        user_id=user_a.id,
        role="reviewer",
        is_active=True,
    )
    member_b = Membership(
        organization_id=org_b.id,
        user_id=user_b.id,
        role="reviewer",
        is_active=True,
    )
    # Suppliers
    supplier_a = Supplier(
        organization_id=org_a.id,
        legal_name="Precision Fasteners Pvt Ltd",
        category="machined_components",
        tier="tier_1",
    )
    supplier_b = Supplier(
        organization_id=org_b.id,
        legal_name="Maharashtra Steel Works",
        category="raw_materials",
        tier="tier_2",
    )
    # Document Types
    doc_type_gst_a = DocumentType(
        organization_id=org_a.id,
        code="gst_certificate",
        name="GST Registration Certificate",
        category="statutory",
        is_system_preset=True,
    )
    doc_type_pan_a = DocumentType(
        organization_id=org_a.id,
        code="pan_card",
        name="Company PAN Card",
        category="statutory",
        is_system_preset=True,
    )
    doc_type_gst_b = DocumentType(
        organization_id=org_b.id,
        code="gst_certificate",
        name="GST Registration Certificate",
        category="statutory",
        is_system_preset=True,
    )
    db_session.add_all(
        [
            member_a,
            member_b,
            supplier_a,
            supplier_b,
            doc_type_gst_a,
            doc_type_pan_a,
            doc_type_gst_b,
        ]
    )
    await db_session.flush()

    # Requirements
    req_gst_a = DocumentRequirement(
        organization_id=org_a.id,
        document_type_id=doc_type_gst_a.id,
        name="Tier 1 Mandatory GST Certificate",
        is_mandatory=True,
    )
    req_pan_a = DocumentRequirement(
        organization_id=org_a.id,
        document_type_id=doc_type_pan_a.id,
        name="Mandatory PAN",
        is_mandatory=True,
    )
    db_session.add_all([req_gst_a, req_pan_a])
    await db_session.flush()

    return {
        "org_a": org_a,
        "org_b": org_b,
        "user_a": user_a,
        "user_b": user_b,
        "member_a": member_a,
        "member_b": member_b,
        "supplier_a": supplier_a,
        "supplier_b": supplier_b,
        "doc_type_gst_a": doc_type_gst_a,
        "doc_type_pan_a": doc_type_pan_a,
        "doc_type_gst_b": doc_type_gst_b,
        "req_gst_a": req_gst_a,
        "req_pan_a": req_pan_a,
    }


async def test_successful_database_connection(test_engine):
    connected = await check_database(test_engine)
    assert connected is True


async def test_successful_table_creation_through_migration(
    db_session: AsyncSession,
):
    result = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    tables = {row[0] for row in result.fetchall()}
    expected_tables = {
        "organizations",
        "users",
        "memberships",
        "suppliers",
        "document_types",
        "document_requirements",
        "supplier_documents",
        "document_extractions",
        "document_reviews",
        "audit_events",
        "alembic_version",
    }
    assert expected_tables.issubset(tables)


async def test_valid_tenant_owned_records(
    db_session: AsyncSession, tenant_fixture: dict
):
    f = tenant_fixture
    org_a = f["org_a"]
    supplier_a = f["supplier_a"]
    doc_type_gst_a = f["doc_type_gst_a"]
    req_gst_a = f["req_gst_a"]
    user_a = f["user_a"]

    # 1. Create document
    doc = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a.id,
        document_type_id=doc_type_gst_a.id,
        requirement_id=req_gst_a.id,
        version=1,
        status="uploaded",
        file_path="storage/documents/org/sup/doc.pdf",
        file_name="gst_cert.pdf",
        file_size_bytes=102400,
        file_mime_type="application/pdf",
        file_hash_sha256="a" * 64,
        is_active_version=True,
    )
    db_session.add(doc)
    await db_session.flush()
    assert doc.id is not None

    # 2. Create extraction
    extraction = DocumentExtraction(
        organization_id=org_a.id,
        document_id=doc.id,
        extraction_engine="local_ocr",
        confidence_score=Decimal("0.950"),
        raw_text="GSTIN: 27AABCU9603R1ZM",
        extracted_data={"gstin": "27AABCU9603R1ZM"},
        status="completed",
    )
    db_session.add(extraction)
    await db_session.flush()
    assert extraction.id is not None

    # 3. Create review
    review = DocumentReview(
        organization_id=org_a.id,
        document_id=doc.id,
        reviewer_id=user_a.id,
        decision="approved",
        comments="Statutory GST matches profile.",
        verified_data={"gstin": "27AABCU9603R1ZM"},
    )
    db_session.add(review)
    await db_session.flush()
    assert review.id is not None

    # 4. Create audit event
    audit = AuditEvent(
        organization_id=org_a.id,
        actor_id=user_a.id,
        actor_type="user",
        entity_type="document",
        entity_id=doc.id,
        action="document.approved",
    )
    db_session.add(audit)
    await db_session.flush()
    assert audit.id is not None


async def test_rejection_of_cross_tenant_supplier_references(
    db_session: AsyncSession, tenant_fixture: dict
):
    f = tenant_fixture
    org_a = f["org_a"]
    supplier_b = f["supplier_b"]  # Belongs to org_b!
    doc_type_gst_a = f["doc_type_gst_a"]

    # Document in org_a attempting to reference supplier in org_b
    doc = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_b.id,
        document_type_id=doc_type_gst_a.id,
        version=1,
        status="uploaded",
        file_path="storage/documents/test.pdf",
        file_name="test.pdf",
        file_size_bytes=2048,
        file_mime_type="application/pdf",
        file_hash_sha256="b" * 64,
        is_active_version=True,
    )
    with pytest.raises(IntegrityError) as exc_info:
        async with db_session.begin_nested():
            db_session.add(doc)
            await db_session.flush()
    assert "fk_supplier_documents_supplier" in str(exc_info.value)

    # Verify outer transaction remains healthy and usable
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_rejection_of_mismatched_document_requirement_references(
    db_session: AsyncSession, tenant_fixture: dict
):
    f = tenant_fixture
    org_a = f["org_a"]
    supplier_a = f["supplier_a"]
    doc_type_pan_a = f["doc_type_pan_a"]  # Document is PAN
    req_gst_a = f["req_gst_a"]  # Requirement is GST!

    doc = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a.id,
        document_type_id=doc_type_pan_a.id,
        requirement_id=req_gst_a.id,  # Mismatched type!
        version=1,
        status="uploaded",
        file_path="storage/documents/test.pdf",
        file_name="test.pdf",
        file_size_bytes=2048,
        file_mime_type="application/pdf",
        file_hash_sha256="c" * 64,
        is_active_version=True,
    )
    with pytest.raises(IntegrityError) as exc_info:
        async with db_session.begin_nested():
            db_session.add(doc)
            await db_session.flush()
    assert "fk_supplier_documents_requirement" in str(exc_info.value)

    # Verify outer transaction remains healthy and usable
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_rejection_of_cross_tenant_reviewer_references(
    db_session: AsyncSession, tenant_fixture: dict
):
    f = tenant_fixture
    org_a = f["org_a"]
    supplier_a = f["supplier_a"]
    doc_type_gst_a = f["doc_type_gst_a"]
    user_b = f["user_b"]  # Belongs to org_b!

    doc = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a.id,
        document_type_id=doc_type_gst_a.id,
        version=1,
        status="uploaded",
        file_path="storage/documents/test.pdf",
        file_name="test.pdf",
        file_size_bytes=2048,
        file_mime_type="application/pdf",
        file_hash_sha256="d" * 64,
        is_active_version=True,
    )
    db_session.add(doc)
    await db_session.flush()

    # Review in org_a by user_b who only has membership in org_b
    review = DocumentReview(
        organization_id=org_a.id,
        document_id=doc.id,
        reviewer_id=user_b.id,
        decision="approved",
    )
    with pytest.raises(IntegrityError) as exc_info:
        async with db_session.begin_nested():
            db_session.add(review)
            await db_session.flush()
    assert "fk_doc_reviews_reviewer_membership" in str(exc_info.value)

    # Verify outer transaction remains healthy and usable
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_rejection_of_cross_tenant_audit_actor_references(
    db_session: AsyncSession, tenant_fixture: dict
):
    f = tenant_fixture
    org_a = f["org_a"]
    supplier_a = f["supplier_a"]
    user_b = f["user_b"]  # Belongs to org_b!

    # Audit event in org_a attributed to user_b
    audit = AuditEvent(
        organization_id=org_a.id,
        actor_id=user_b.id,
        actor_type="user",
        entity_type="supplier",
        entity_id=supplier_a.id,
        action="supplier.updated",
    )
    with pytest.raises(IntegrityError) as exc_info:
        async with db_session.begin_nested():
            db_session.add(audit)
            await db_session.flush()
    assert "fk_audit_events_actor_membership" in str(exc_info.value)

    # Verify outer transaction remains healthy and usable
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_rejection_of_previous_version_belonging_to_another_supplier(
    db_session: AsyncSession, tenant_fixture: dict
):
    f = tenant_fixture
    org_a = f["org_a"]
    supplier_a1 = f["supplier_a"]
    doc_type_gst_a = f["doc_type_gst_a"]

    # Another supplier in org_a
    supplier_a2 = Supplier(
        organization_id=org_a.id,
        legal_name="Apex Tooling Pvt Ltd",
        category="tooling",
        tier="tier_2",
    )
    db_session.add(supplier_a2)
    await db_session.flush()

    # Document 1 for supplier_a1
    doc_1 = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a1.id,
        document_type_id=doc_type_gst_a.id,
        version=1,
        status="uploaded",
        file_path="storage/documents/doc1.pdf",
        file_name="doc1.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
        is_active_version=True,
    )
    db_session.add(doc_1)
    await db_session.flush()

    # Document 2 for supplier_a2 referencing doc_1 as previous version!
    doc_2 = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a2.id,  # Different supplier!
        document_type_id=doc_type_gst_a.id,
        previous_version_id=doc_1.id,
        version=2,
        status="uploaded",
        file_path="storage/documents/doc2.pdf",
        file_name="doc2.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="2" * 64,
        is_active_version=True,
    )
    with pytest.raises(IntegrityError) as exc_info:
        async with db_session.begin_nested():
            db_session.add(doc_2)
            await db_session.flush()
    assert "fk_supplier_documents_previous_version" in str(exc_info.value)

    # Verify outer transaction remains healthy and usable
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_rejection_of_second_active_document_for_same_supplier_and_type(
    db_session: AsyncSession, tenant_fixture: dict
):
    f = tenant_fixture
    org_a = f["org_a"]
    supplier_a = f["supplier_a"]
    doc_type_gst_a = f["doc_type_gst_a"]

    doc_v1 = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a.id,
        document_type_id=doc_type_gst_a.id,
        version=1,
        status="uploaded",
        file_path="storage/documents/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="e" * 64,
        is_active_version=True,
    )
    db_session.add(doc_v1)
    await db_session.flush()

    # Second active document for same supplier and same document_type!
    doc_v2 = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a.id,
        document_type_id=doc_type_gst_a.id,
        version=2,
        status="uploaded",
        file_path="storage/documents/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="f" * 64,
        is_active_version=True,  # Conflict with v1!
    )
    with pytest.raises(IntegrityError) as exc_info:
        async with db_session.begin_nested():
            db_session.add(doc_v2)
            await db_session.flush()
    assert "uq_supplier_docs_active_version" in str(exc_info.value)

    # Verify outer transaction remains healthy and usable
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_acceptance_of_new_version_after_previous_version_deactivated(
    db_session: AsyncSession, tenant_fixture: dict
):
    f = tenant_fixture
    org_a = f["org_a"]
    supplier_a = f["supplier_a"]
    doc_type_gst_a = f["doc_type_gst_a"]

    doc_v1 = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a.id,
        document_type_id=doc_type_gst_a.id,
        version=1,
        status="rejected",
        file_path="storage/documents/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="a" * 64,
        is_active_version=True,
    )
    db_session.add(doc_v1)
    await db_session.flush()

    # Deactivate v1
    doc_v1.is_active_version = False
    await db_session.flush()

    # Now v2 with is_active_version=True succeeds!
    doc_v2 = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a.id,
        document_type_id=doc_type_gst_a.id,
        previous_version_id=doc_v1.id,
        version=2,
        status="uploaded",
        file_path="storage/documents/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="b" * 64,
        is_active_version=True,
    )
    db_session.add(doc_v2)
    await db_session.flush()

    assert doc_v2.id is not None
    assert doc_v2.version == 2
    assert doc_v2.previous_version_id == doc_v1.id


async def test_preservation_of_previous_version_review_status(
    db_session: AsyncSession, tenant_fixture: dict
):
    f = tenant_fixture
    org_a = f["org_a"]
    supplier_a = f["supplier_a"]
    doc_type_gst_a = f["doc_type_gst_a"]
    user_a = f["user_a"]

    doc_v1 = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a.id,
        document_type_id=doc_type_gst_a.id,
        version=1,
        status="rejected",
        file_path="storage/documents/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
        is_active_version=True,
    )
    db_session.add(doc_v1)
    await db_session.flush()

    review_v1 = DocumentReview(
        organization_id=org_a.id,
        document_id=doc_v1.id,
        reviewer_id=user_a.id,
        decision="rejected",
        comments="Certificate blurred and illegible.",
        rejection_reason_code="illegible",
    )
    db_session.add(review_v1)
    await db_session.flush()

    # Supplier submits v2
    doc_v1.is_active_version = False
    # Notice: doc_v1.status remains 'rejected', NOT overwritten to 'superseded'!
    await db_session.flush()

    doc_v2 = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a.id,
        document_type_id=doc_type_gst_a.id,
        previous_version_id=doc_v1.id,
        version=2,
        status="uploaded",
        file_path="storage/documents/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="2" * 64,
        is_active_version=True,
    )
    db_session.add(doc_v2)
    await db_session.flush()

    # Expire and refresh doc_v1
    await db_session.refresh(doc_v1)
    assert doc_v1.status == "rejected"
    assert doc_v1.is_active_version is False


async def test_action_required_document_status_and_review_transitions(
    db_session: AsyncSession, tenant_fixture: dict
):
    f = tenant_fixture
    org_a = f["org_a"]
    supplier_a = f["supplier_a"]
    doc_type_gst_a = f["doc_type_gst_a"]
    req_gst_a = f["req_gst_a"]
    user_a = f["user_a"]

    # 1. Document can be created with or transitioned to 'action_required'
    doc = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=supplier_a.id,
        document_type_id=doc_type_gst_a.id,
        requirement_id=req_gst_a.id,
        version=1,
        status="uploaded",
        file_path="storage/documents/org/sup/doc.pdf",
        file_name="gst_cert.pdf",
        file_size_bytes=10240,
        file_mime_type="application/pdf",
        file_hash_sha256="9" * 64,
        is_active_version=True,
    )
    db_session.add(doc)
    await db_session.flush()

    # 2. Reviewer requests revision -> document status updates to 'action_required'
    review = DocumentReview(
        organization_id=org_a.id,
        document_id=doc.id,
        reviewer_id=user_a.id,
        decision="revision_requested",
        comments="Please attach Annexure A along with the certificate.",
    )
    db_session.add(review)
    doc.status = "action_required"
    await db_session.flush()

    assert doc.status == "action_required"
    assert review.decision == "revision_requested"

    # 3. Invalid status is rejected by check constraint inside savepoint
    with pytest.raises(IntegrityError) as exc_info:
        async with db_session.begin_nested():
            invalid_doc = SupplierDocument(
                organization_id=org_a.id,
                supplier_id=supplier_a.id,
                document_type_id=doc_type_gst_a.id,
                version=99,
                status="invalid_status_value",
                file_path="storage/documents/invalid.pdf",
                file_name="invalid.pdf",
                file_size_bytes=1024,
                file_mime_type="application/pdf",
                file_hash_sha256="8" * 64,
                is_active_version=False,
            )
            db_session.add(invalid_doc)
            await db_session.flush()
    assert "chk_supplier_docs_status" in str(exc_info.value)

    # Verify transaction recovered
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_updated_at_triggers_metadata(db_session: AsyncSession):
    """Verify that all 7 documented tables have the updated_at trigger attached."""
    expected_tables = {
        "organizations",
        "users",
        "memberships",
        "suppliers",
        "document_types",
        "document_requirements",
        "supplier_documents",
    }
    result = await db_session.execute(
        text(
            """
            SELECT event_object_table, trigger_name
            FROM information_schema.triggers
            WHERE trigger_name LIKE 'trg_%_updated_at'
            """
        )
    )
    found_triggers = {row[0]: row[1] for row in result.fetchall()}
    for table in expected_tables:
        expected_trigger_name = f"trg_{table}_updated_at"
        assert table in found_triggers, f"Missing trigger for {table}"
        assert found_triggers[table] == expected_trigger_name


async def test_raw_sql_updated_at_trigger_execution(test_engine):
    """Verify updated_at trigger executes via raw SQL updates and advances timestamps."""
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        try:
            # 1. Insert organization using raw SQL with past updated_at
            org_id = uuid.uuid4()
            slug = f"trigger-test-{uuid.uuid4().hex[:8]}"
            await conn.execute(
                text(
                    """
                    INSERT INTO organizations (id, name, slug, status, created_at, updated_at)
                    VALUES (:id, 'Trigger Test Org', :slug, 'active', NOW() - INTERVAL '1 second', NOW() - INTERVAL '1 second')
                    """
                ),
                {"id": org_id, "slug": slug},
            )

            # Read initial updated_at
            res_before = await conn.execute(
                text("SELECT updated_at FROM organizations WHERE id = :id"),
                {"id": org_id},
            )
            initial_updated_at = res_before.scalar()

            # Small delay
            await asyncio.sleep(0.05)

            # 2. Update non-timestamp column using raw SQL (no ORM)
            await conn.execute(
                text(
                    "UPDATE organizations SET name = 'Updated Org Name' WHERE id = :id"
                ),
                {"id": org_id},
            )

            # Read updated_at after update
            res_after = await conn.execute(
                text("SELECT updated_at, name FROM organizations WHERE id = :id"),
                {"id": org_id},
            )
            row_after = res_after.fetchone()
            new_updated_at = row_after[0]
            name_after = row_after[1]

            assert name_after == "Updated Org Name"
            assert new_updated_at > initial_updated_at, (
                f"Expected updated_at to increase, but got initial={initial_updated_at}, new={new_updated_at}"
            )
        finally:
            await trans.rollback()


def test_test_database_safety_guard():
    """Verify safety guard protects non-test databases from accidental test execution."""
    # When test database URL is not configured
    bad_settings_none = Settings(
        postgres_password="fake",
        test_database_url=None,
    )
    with pytest.raises(ValueError) as exc_info_none:
        bad_settings_none.get_test_database_url()
    assert "TEST_DATABASE_URL environment variable is not configured" in str(
        exc_info_none.value
    )

    # When test database URL points to development database 'vendorflow'
    bad_settings_dev = Settings(
        postgres_password="fake",
        test_database_url="postgresql+asyncpg://vendorflow:secret@127.0.0.1:5433/vendorflow",
    )
    with pytest.raises(RuntimeError) as exc_info_dev:
        bad_settings_dev.get_test_database_url()
    assert "Test database safety guard violation" in str(exc_info_dev.value)
    assert "vendorflow" in str(exc_info_dev.value)
