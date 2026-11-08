"""Comprehensive domain layer integration tests for Phase 3.4.

Tests cover:
- Tenant isolation across sites, groups, links, and documents
- Supplier and document type ownership validation
- Group identity immutability via database triggers
- Linear version lineage validation and invalid predecessor rejection
- Lineage fork prevention
- Multiple requirement links and duplicate link rejection
- Immutable legacy requirement associations
- Single effective version enforcement and demotion
- Rejected and revision-requested replacement behavior (preserving earlier effective version)
- Effective version revocation
- Background processing failure behavior
- Transaction rollback on audit failure
"""

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.auth.context import OrganizationContext
from app.core.config import settings
from app.models import (
    DocumentGroupRequirementLink,
    DocumentRequirement,
    DocumentReview,
    DocumentType,
    Membership,
    Organization,
    Supplier,
    SupplierDocument,
    User,
)
from app.services.supplier_document import SupplierDocumentDomainService
from app.services.supplier_document_group import SupplierDocumentGroupService
from app.services.supplier_site import SupplierSiteService

BASE_REVISION = "9b7d3318e727"
HEAD_REVISION = "f129c5e378bc"


def _run_alembic(action: str, target: str) -> None:
    test_url = settings.get_test_database_url()
    approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
    assert test_url.database == approved_db
    assert test_url.database != "vendorflow"
    os.environ["ALEMBIC_DB"] = "test"
    api_dir = Path(__file__).resolve().parents[1]
    alembic_ini = api_dir / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(api_dir / "alembic"))
    if action == "upgrade":
        command.upgrade(cfg, target)
    elif action == "downgrade":
        command.downgrade(cfg, target)


async def _cleanup_test_database() -> None:
    """Safely clear test data so downgrade preflight checks pass cleanly."""
    approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
    assert approved_db != "vendorflow"
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
        await conn.execute("DELETE FROM document_group_requirement_links;")
        await conn.execute("DELETE FROM supplier_document_migration_discrepancies;")
        while True:
            res = await conn.execute(
                "DELETE FROM supplier_documents WHERE id NOT IN (SELECT previous_version_id FROM supplier_documents WHERE previous_version_id IS NOT NULL) AND previous_version_id IS NOT NULL;"
            )
            if res == "DELETE 0":
                break
        await conn.execute("DELETE FROM supplier_documents;")
        await conn.execute("DELETE FROM supplier_document_groups;")
        await conn.execute("DELETE FROM supplier_sites;")
        await conn.execute("DELETE FROM document_requirements;")
        await conn.execute("DELETE FROM document_types;")
        await conn.execute("DELETE FROM memberships;")
        await conn.execute("DELETE FROM suppliers;")
        await conn.execute("DELETE FROM users;")
        await conn.execute("DELETE FROM organizations;")
    finally:
        await conn.close()


@pytest.fixture(scope="module", autouse=True)
def setup_module_schema():
    """Ensure database is at HEAD_REVISION during testing and returned to BASE_REVISION afterwards."""
    os.environ["PHASE34_SCHEMA_ACTIVE"] = "1"
    _run_alembic("upgrade", HEAD_REVISION)
    try:
        yield
    finally:
        asyncio.run(_cleanup_test_database())
        _run_alembic("downgrade", BASE_REVISION)
        os.environ.pop("PHASE34_SCHEMA_ACTIVE", None)


@pytest.fixture(autouse=True)
async def clean_database_per_test():
    """Ensure each test runs with a completely clean database."""
    await _cleanup_test_database()
    yield
    await _cleanup_test_database()


@pytest.fixture
async def session():
    """Provide an AsyncSession connected directly to approved verification database."""
    test_url = settings.get_test_database_url()
    approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
    assert test_url.database == approved_db
    assert test_url.database != "vendorflow"
    engine = create_async_engine(test_url, poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def session_factory():
    """Factory yielding independent AsyncSessions for concurrency testing."""
    test_url = settings.get_test_database_url()
    approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
    assert test_url.database == approved_db
    assert test_url.database != "vendorflow"
    engine = create_async_engine(test_url, poolclass=NullPool)

    def _make():
        return AsyncSession(engine, expire_on_commit=False)

    yield _make
    await engine.dispose()


@pytest.fixture
async def test_org_a(session: AsyncSession):
    """Setup buyer Organization A, user, and membership."""
    org = Organization(
        name="Tata AutoComp Pune",
        slug=f"tata-{uuid.uuid4().hex[:8]}",
        status="active",
    )
    user = User(
        email=f"user_a_{uuid.uuid4().hex[:8]}@tata.com",
        hashed_password="pw_hash_a",
        full_name="Rajesh Sharma",
        is_active=True,
    )
    session.add_all([org, user])
    await session.flush()

    mem = Membership(
        organization_id=org.id,
        user_id=user.id,
        role="admin",
        is_active=True,
    )
    session.add(mem)
    await session.commit()

    context = OrganizationContext(
        user_id=user.id,
        organization_id=org.id,
        membership_id=mem.id,
        role="admin",
        user_email=user.email,
        user_full_name=user.full_name,
        organization_name=org.name,
        organization_slug=org.slug,
    )
    return {"org": org, "user": user, "context": context}


@pytest.fixture
async def test_org_b(session: AsyncSession):
    """Setup buyer Organization B, user, and membership."""
    org = Organization(
        name="Bharat Forge Pune",
        slug=f"bharat-{uuid.uuid4().hex[:8]}",
        status="active",
    )
    user = User(
        email=f"user_b_{uuid.uuid4().hex[:8]}@bharat.com",
        hashed_password="pw_hash_b",
        full_name="Pooja Deshmukh",
        is_active=True,
    )
    session.add_all([org, user])
    await session.flush()

    mem = Membership(
        organization_id=org.id,
        user_id=user.id,
        role="admin",
        is_active=True,
    )
    session.add(mem)
    await session.commit()

    context = OrganizationContext(
        user_id=user.id,
        organization_id=org.id,
        membership_id=mem.id,
        role="admin",
        user_email=user.email,
        user_full_name=user.full_name,
        organization_name=org.name,
        organization_slug=org.slug,
    )
    return {"org": org, "user": user, "context": context}


@pytest.fixture
async def sample_doc_type(session: AsyncSession, test_org_a: dict):
    """Create a sample document type in Org A."""
    ctx = test_org_a["context"]
    dt = DocumentType(
        organization_id=ctx.organization_id,
        name="GST Certificate",
        code=f"GST-{uuid.uuid4().hex[:6]}",
    )
    session.add(dt)
    await session.commit()
    await session.refresh(dt)
    return dt


@pytest.fixture
async def sample_supplier(session: AsyncSession, test_org_a: dict):
    """Create a sample supplier in Org A."""
    ctx = test_org_a["context"]
    supplier = Supplier(
        organization_id=ctx.organization_id,
        legal_name="Apex Fasteners Ltd",
        vendor_code=f"APX-{uuid.uuid4().hex[:6]}",
        status="approved",
    )
    session.add(supplier)
    await session.commit()
    await session.refresh(supplier)
    return supplier


# =============================================================================
# Step 4 Tests
# =============================================================================


async def test_tenant_isolation(
    session: AsyncSession,
    test_org_a: dict,
    test_org_b: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Cross-tenant operations on sites, groups, links, and documents must be rejected."""
    ctx_a = test_org_a["context"]
    ctx_b = test_org_b["context"]

    site_service = SupplierSiteService(session)
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    # 1. Org A creates a site, group, and document
    site_a = await site_service.create_site(
        context=ctx_a,
        supplier_id=sample_supplier.id,
        site_code="PLANT-01",
        site_name="Pune Main Plant",
    )

    group_a = await group_service.create_group(
        context=ctx_a,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
        document_reference="GST-REF-001",
    )

    doc_a = await doc_service.create_document_version(
        context=ctx_a,
        document_group_id=group_a.id,
        file_path="uploads/org_a/doc_v1.pdf",
        file_name="doc_v1.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="a" * 64,
    )

    # 2. Org B attempts to create a site under Org A's supplier -> 404
    with pytest.raises(HTTPException) as exc_info:
        await site_service.create_site(
            context=ctx_b,
            supplier_id=sample_supplier.id,
            site_code="PLANT-01",
            site_name="Illicit Plant",
        )
    assert exc_info.value.status_code == 404

    # Org B attempts to fetch Org A's site -> returns None
    assert (
        await site_service.site_repo.get_by_id(site_a.id, ctx_b.organization_id) is None
    )

    # 3. Org B attempts to access Org A's group -> 404
    with pytest.raises(HTTPException) as exc_info:
        await group_service.get_group(group_a.id, ctx_b)
    assert exc_info.value.status_code == 404

    # 4. Org B attempts to create a group referencing Org A's supplier -> 404
    with pytest.raises(HTTPException) as exc_info:
        await group_service.create_group(
            context=ctx_b,
            supplier_id=sample_supplier.id,
            document_type_id=sample_doc_type.id,
            coverage_scope="supplier",
        )
    assert exc_info.value.status_code == 404

    # 5. Org B attempts to create a document version in Org A's group -> 404
    with pytest.raises(HTTPException) as exc_info:
        await doc_service.create_document_version(
            context=ctx_b,
            document_group_id=group_a.id,
            file_path="uploads/org_b/doc.pdf",
            file_name="doc.pdf",
            file_size_bytes=1024,
            file_mime_type="application/pdf",
            file_hash_sha256="b" * 64,
        )
    assert exc_info.value.status_code == 404

    # 6. Org B attempts to read or review Org A's document -> 404
    with pytest.raises(HTTPException) as exc_info:
        await doc_service.get_document(doc_a.id, ctx_b)
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        await doc_service.record_review_decision(
            context=ctx_b,
            document_id=doc_a.id,
            decision="approved",
        )
    assert exc_info.value.status_code == 404


async def test_supplier_and_doc_type_ownership(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Document groups and documents must belong to the correct supplier, document type, and site."""
    ctx = test_org_a["context"]
    site_service = SupplierSiteService(session)
    group_service = SupplierDocumentGroupService(session)

    # Create a second supplier in same org
    supp2 = Supplier(
        organization_id=ctx.organization_id,
        legal_name="Second Supplier",
        vendor_code=f"SUP2-{uuid.uuid4().hex[:6]}",
        status="approved",
    )
    session.add(supp2)
    await session.commit()

    # Create site under Supplier 2
    site2 = await site_service.create_site(
        context=ctx,
        supplier_id=supp2.id,
        site_code="SITE-2",
        site_name="Supplier 2 Site",
    )

    # Attempt to create a site-scoped group for Supplier 1 pointing to Site 2 (owned by Supplier 2) -> 422
    with pytest.raises(HTTPException) as exc_info:
        await group_service.create_group(
            context=ctx,
            supplier_id=sample_supplier.id,
            document_type_id=sample_doc_type.id,
            coverage_scope="site",
            site_id=site2.id,
        )
    assert exc_info.value.status_code == 422
    assert "belongs to a different supplier" in exc_info.value.detail

    # Requirement ownership: create requirement for a different doc_type
    dt2 = DocumentType(
        organization_id=ctx.organization_id,
        name="ISO 9001",
        code=f"ISO-{uuid.uuid4().hex[:6]}",
    )
    session.add(dt2)
    await session.flush()

    req2 = DocumentRequirement(
        organization_id=ctx.organization_id,
        document_type_id=dt2.id,
        name="ISO Certification",
    )
    session.add(req2)
    await session.commit()

    # Create group for doc_type 1
    group1 = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    # Attempt to link requirement of doc_type 2 to group of doc_type 1 -> 422
    with pytest.raises(HTTPException) as exc_info:
        await group_service.add_requirement_link(
            context=ctx,
            group_id=group1.id,
            requirement_id=req2.id,
        )
    assert exc_info.value.status_code == 422
    assert (
        "document_type_id does not match group document_type_id"
        in exc_info.value.detail
    )


async def test_group_identity_immutability(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Database trigger trg_freeze_supplier_document_group_identity must block mutating group identity once documents exist."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    # Add a document to the group
    await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/doc_v1.pdf",
        file_name="doc_v1.pdf",
        file_size_bytes=2048,
        file_mime_type="application/pdf",
        file_hash_sha256="c" * 64,
    )

    # Attempt to alter supplier_id via raw update
    other_supp = Supplier(
        organization_id=ctx.organization_id,
        legal_name="Other Supplier",
        vendor_code=f"OTH-{uuid.uuid4().hex[:6]}",
        status="approved",
    )
    session.add(other_supp)
    await session.commit()

    with pytest.raises(Exception) as exc_info:
        await session.execute(
            text(
                "UPDATE supplier_document_groups SET supplier_id = :new_supp WHERE id = :gid"
            ),
            {"new_supp": other_supp.id, "gid": group.id},
        )
        await session.commit()
    assert (
        "Cannot modify supplier_id of supplier_document_groups with existing documents"
        in str(exc_info.value)
    )
    await session.rollback()


async def test_valid_and_invalid_lineage(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Version lineage must follow strict linear predecessor rules."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    # 1. Valid v1 creation
    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
        previous_version_id=None,
    )
    assert v1.version == 1
    assert v1.previous_version_id is None

    # 2. Valid v2 creation referencing v1
    v2 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="2" * 64,
        previous_version_id=v1.id,
    )
    assert v2.version == 2
    assert v2.previous_version_id == v1.id

    # 3. Invalid: attempting to create another root version in existing group -> 422
    with pytest.raises(HTTPException) as exc_info:
        await doc_service.create_document_version(
            context=ctx,
            document_group_id=group.id,
            file_path="uploads/root_fork.pdf",
            file_name="root_fork.pdf",
            file_size_bytes=1000,
            file_mime_type="application/pdf",
            file_hash_sha256="3" * 64,
            previous_version_id=None,
        )
    assert exc_info.value.status_code == 422
    assert "must specify previous_version_id" in exc_info.value.detail

    # 4. Invalid: non-existent predecessor -> 404
    with pytest.raises(HTTPException) as exc_info:
        await doc_service.create_document_version(
            context=ctx,
            document_group_id=group.id,
            file_path="uploads/missing_pred.pdf",
            file_name="missing_pred.pdf",
            file_size_bytes=1000,
            file_mime_type="application/pdf",
            file_hash_sha256="4" * 64,
            previous_version_id=uuid.uuid4(),
        )
    assert exc_info.value.status_code == 404


async def test_fork_prevention(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Lineage cannot branch: a predecessor version can have at most one direct child."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="a" * 64,
        previous_version_id=None,
    )

    # v2 child created for v1
    await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="b" * 64,
        previous_version_id=v1.id,
    )

    # Attempt to create another child for v1 -> 409 Conflict
    with pytest.raises(HTTPException) as exc_info:
        await doc_service.create_document_version(
            context=ctx,
            document_group_id=group.id,
            file_path="uploads/v2_fork.pdf",
            file_name="v2_fork.pdf",
            file_size_bytes=1000,
            file_mime_type="application/pdf",
            file_hash_sha256="c" * 64,
            previous_version_id=v1.id,
        )
    assert exc_info.value.status_code == 409
    assert "Lineage fork detected" in exc_info.value.detail


async def test_multiple_requirement_links(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """A document group can be linked to multiple requirements of the same document type."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)

    req1 = DocumentRequirement(
        organization_id=ctx.organization_id,
        document_type_id=sample_doc_type.id,
        name="GST Form Regular",
    )
    req2 = DocumentRequirement(
        organization_id=ctx.organization_id,
        document_type_id=sample_doc_type.id,
        name="GST Form Composition",
    )
    session.add_all([req1, req2])
    await session.commit()

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
        requirement_ids=[req1.id, req2.id],
    )

    links = await group_service.link_repo.list_by_group(group.id, ctx.organization_id)
    assert len(links) == 2
    linked_req_ids = {link.requirement_id for link in links}
    assert linked_req_ids == {req1.id, req2.id}


async def test_duplicate_requirement_links(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Duplicate requirement links on the same document group must be rejected with 409 Conflict."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)

    req = DocumentRequirement(
        organization_id=ctx.organization_id,
        document_type_id=sample_doc_type.id,
        name="Single Requirement",
    )
    session.add(req)
    await session.commit()

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
        requirement_ids=[req.id],
    )

    with pytest.raises(HTTPException) as exc_info:
        await group_service.add_requirement_link(
            context=ctx,
            group_id=group.id,
            requirement_id=req.id,
        )
    assert exc_info.value.status_code == 409
    assert "already linked" in exc_info.value.detail


async def test_immutable_legacy_requirement_associations(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """legacy_requirement_id on supplier_documents is immutable and protected by database triggers."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    req1 = DocumentRequirement(
        organization_id=ctx.organization_id,
        document_type_id=sample_doc_type.id,
        name="Req 1",
    )
    req2 = DocumentRequirement(
        organization_id=ctx.organization_id,
        document_type_id=sample_doc_type.id,
        name="Req 2",
    )
    session.add_all([req1, req2])
    await session.commit()

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    doc = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/doc.pdf",
        file_name="doc.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="d" * 64,
        legacy_requirement_id=req1.id,
    )
    assert doc.legacy_requirement_id == req1.id

    # Direct UPDATE attempt on legacy_requirement_id must fail via trigger
    with pytest.raises(Exception) as exc_info:
        await session.execute(
            text(
                "UPDATE supplier_documents SET legacy_requirement_id = :new_req WHERE id = :doc_id"
            ),
            {"new_req": req2.id, "doc_id": doc.id},
        )
        await session.commit()
    assert "legacy_requirement_id on supplier_documents is immutable" in str(
        exc_info.value
    )
    await session.rollback()


async def test_effective_version_uniqueness(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Only one document version can be effective per group; approving a replacement demotes the predecessor."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
    )

    # Approve v1
    v1_reviewed = await doc_service.record_review_decision(
        context=ctx,
        document_id=v1.id,
        decision="approved",
    )
    assert v1_reviewed.is_effective_version is True

    # Create v2
    v2 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="2" * 64,
        previous_version_id=v1.id,
    )
    assert v2.is_effective_version is False

    # Approve v2 -> v2 becomes effective, v1 is demoted
    v2_reviewed = await doc_service.record_review_decision(
        context=ctx,
        document_id=v2.id,
        decision="approved",
    )
    assert v2_reviewed.is_effective_version is True

    # Refresh v1 and verify demotion
    await session.refresh(v1)
    assert v1.is_effective_version is False

    # Query all effective versions in group
    result = await session.execute(
        select(SupplierDocument).where(
            SupplierDocument.document_group_id == group.id,
            SupplierDocument.is_effective_version.is_(True),
        )
    )
    effective_docs = result.scalars().all()
    assert len(effective_docs) == 1
    assert effective_docs[0].id == v2.id


async def test_rejected_replacement_behavior(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Rejecting a replacement version must not demote the earlier approved effective version."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    # v1 approved as effective
    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
    )
    await doc_service.record_review_decision(
        context=ctx,
        document_id=v1.id,
        decision="approved",
    )

    # v2 uploaded as replacement
    v2 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="2" * 64,
        previous_version_id=v1.id,
    )

    # v2 rejected by reviewer
    v2_reviewed = await doc_service.record_review_decision(
        context=ctx,
        document_id=v2.id,
        decision="rejected",
        comments="Blurry scan",
        rejection_reason_code="illegible",
    )
    assert v2_reviewed.is_effective_version is False

    # v1 must remain effective!
    await session.refresh(v1)
    assert v1.is_effective_version is True


async def test_revision_requested_replacement_behavior(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """A revision-requested replacement version must not demote the earlier approved effective version."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    # v1 approved as effective
    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
    )
    await doc_service.record_review_decision(
        context=ctx,
        document_id=v1.id,
        decision="approved",
    )

    # v2 uploaded as replacement
    v2 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="2" * 64,
        previous_version_id=v1.id,
    )

    # v2 reviewed with revision_requested
    v2_reviewed = await doc_service.record_review_decision(
        context=ctx,
        document_id=v2.id,
        decision="revision_requested",
        comments="Page 2 signature missing",
        rejection_reason_code="incomplete_pages",
    )
    assert v2_reviewed.is_effective_version is False

    # v1 must remain effective!
    await session.refresh(v1)
    assert v1.is_effective_version is True


async def test_effective_version_revocation(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Subsequent rejection or explicit revocation of an effective document must revoke its effectiveness."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
    )
    await doc_service.record_review_decision(
        context=ctx,
        document_id=v1.id,
        decision="approved",
    )
    assert v1.is_effective_version is True

    # 1. Later review rejects the effective document
    v1_revoked = await doc_service.record_review_decision(
        context=ctx,
        document_id=v1.id,
        decision="rejected",
        comments="Audit revealed fraudulent stamp",
        rejection_reason_code="other",
    )
    assert v1_revoked.is_effective_version is False

    # Re-approve for explicit group revocation test
    await doc_service.record_review_decision(
        context=ctx,
        document_id=v1.id,
        decision="approved",
    )
    await session.refresh(v1)
    assert v1.is_effective_version is True

    # 2. Explicit group effective revocation
    await doc_service.revoke_effective_version(
        context=ctx,
        document_group_id=group.id,
        reason="Administrative hold",
    )
    await session.refresh(v1)
    assert v1.is_effective_version is False


async def test_processing_failure_does_not_demote_effective_version(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Background processing failure on a replacement version must not silently demote existing effective evidence."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
    )
    await doc_service.record_review_decision(
        context=ctx,
        document_id=v1.id,
        decision="approved",
    )
    assert v1.is_effective_version is True

    # Replacement v2 created
    v2 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="2" * 64,
        previous_version_id=v1.id,
    )

    # v2 processing fails
    await doc_service.handle_processing_failure(
        context=ctx,
        document_id=v2.id,
        error_message="OCR worker timeout during processing",
    )
    await session.refresh(v2)
    assert v2.processing_status == "failed"

    # v1 must remain effective!
    await session.refresh(v1)
    assert v1.is_effective_version is True


async def test_transaction_rollback_on_audit_failure(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
):
    """If audit event logging fails, the transaction must roll back and no domain entity must be persisted."""
    ctx = test_org_a["context"]
    site_service = SupplierSiteService(session)
    supplier_id = sample_supplier.id

    # Context with an invalid user_id that does not exist in memberships
    # Violates fk_audit_events_actor_membership on audit event write
    invalid_ctx = OrganizationContext(
        user_id=uuid.uuid4(),
        organization_id=ctx.organization_id,
        membership_id=uuid.uuid4(),
        role="admin",
        user_email="ghost@tata.com",
        user_full_name="Ghost User",
        organization_name=ctx.organization_name,
        organization_slug=ctx.organization_slug,
    )

    with pytest.raises(Exception) as exc_info:
        await site_service.create_site(
            context=invalid_ctx,
            supplier_id=supplier_id,
            site_code="ROLLBACK-01",
            site_name="Rollback Plant",
        )
    assert "fk_audit_events_actor_membership" in str(exc_info.value)

    # Verify site was NOT persisted due to transaction rollback
    persisted = await site_service.site_repo.get_by_code(
        supplier_id, "ROLLBACK-01", ctx.organization_id
    )
    assert persisted is None


async def test_prevent_accidental_reactivation_of_older_versions(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """An older version cannot become effective through normal approval when a newer version exists."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    # 1. Create and approve v1
    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
    )
    await doc_service.record_review_decision(
        context=ctx,
        document_id=v1.id,
        decision="approved",
    )
    await session.refresh(v1)
    assert v1.is_effective_version is True

    # 2. Create and approve v2
    v2 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="2" * 64,
        previous_version_id=v1.id,
    )
    await doc_service.record_review_decision(
        context=ctx,
        document_id=v2.id,
        decision="approved",
    )
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_effective_version is False
    assert v2.is_effective_version is True

    # 3. Attempt to approve v1 again while v2 is the latest version
    with pytest.raises(HTTPException) as exc_info:
        await doc_service.record_review_decision(
            context=ctx,
            document_id=v1.id,
            decision="approved",
        )
    assert exc_info.value.status_code == 409
    assert (
        "Cannot approve older version 1; latest version in document group is 2"
        in str(exc_info.value.detail)
    )

    # 4. Confirm v2 remains effective, v1 remains not effective
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_effective_version is False
    assert v2.is_effective_version is True

    # 5. Confirm review history on v1 is unchanged (only 1 review persisted)
    res = await session.execute(
        select(DocumentReview).where(DocumentReview.document_id == v1.id)
    )
    reviews_v1 = list(res.scalars().all())
    assert len(reviews_v1) == 1
    assert reviews_v1[0].decision == "approved"


async def test_older_version_approval_rejected_when_latest_replacement_rejected(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """When a replacement v2 is rejected, earlier v1 remains effective and cannot be re-approved through normal approval."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
    )
    await doc_service.record_review_decision(
        context=ctx,
        document_id=v1.id,
        decision="approved",
    )
    await session.refresh(v1)
    assert v1.is_effective_version is True

    # Replacement v2
    v2 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="2" * 64,
        previous_version_id=v1.id,
    )

    # v2 is rejected
    await doc_service.record_review_decision(
        context=ctx,
        document_id=v2.id,
        decision="rejected",
        rejection_reason_code="illegible",
    )
    await session.refresh(v1)
    await session.refresh(v2)
    assert v1.is_effective_version is True
    assert v2.is_effective_version is False

    # Attempt to approve v1 again fails
    with pytest.raises(HTTPException) as exc_info:
        await doc_service.record_review_decision(
            context=ctx,
            document_id=v1.id,
            decision="approved",
        )
    assert exc_info.value.status_code == 409
    assert (
        "Cannot approve older version 1; latest version in document group is 2"
        in str(exc_info.value.detail)
    )

    # v1 remains effective
    await session.refresh(v1)
    assert v1.is_effective_version is True


async def test_concurrent_competing_approvals_in_same_group(
    session: AsyncSession,
    session_factory,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Two concurrent approval operations in the same group are serialized by group-level row locking."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
    )
    await doc_service.record_review_decision(
        context=ctx,
        document_id=v1.id,
        decision="approved",
    )

    v2 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="2" * 64,
        previous_version_id=v1.id,
    )

    # Concurrent operations using independent sessions:
    # Worker 1 tries to approve latest v2
    # Worker 2 concurrently tries to approve older v1
    async def worker_approve_v2():
        async with session_factory() as s:
            ds = SupplierDocumentDomainService(s)
            return await ds.record_review_decision(
                context=ctx,
                document_id=v2.id,
                decision="approved",
            )

    async def worker_approve_v1():
        async with session_factory() as s:
            ds = SupplierDocumentDomainService(s)
            return await ds.record_review_decision(
                context=ctx,
                document_id=v1.id,
                decision="approved",
            )

    results = await asyncio.gather(
        worker_approve_v2(),
        worker_approve_v1(),
        return_exceptions=True,
    )

    # Exactly one succeeded (v2) and one raised HTTPException 409 (v1 older version)
    # Neither raised an unhandled 500 or raw database IntegrityError
    exceptions = [r for r in results if isinstance(r, Exception)]
    successes = [r for r in results if not isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(exceptions) == 1
    assert isinstance(exceptions[0], HTTPException)
    assert exceptions[0].status_code == 409
    assert "Cannot approve older version 1" in str(exceptions[0].detail)

    # Confirm final database state has strictly at most one effective version
    await session.refresh(v1)
    await session.refresh(v2)
    effective_docs = await doc_service.doc_repo.list_by_group(
        group.id, ctx.organization_id
    )
    effective_set = [d for d in effective_docs if d.is_effective_version]
    assert len(effective_set) == 1
    assert effective_set[0].id == v2.id


async def test_concurrent_same_version_approval(
    session: AsyncSession,
    session_factory,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Two concurrent approvals targeting the exact same document version are serialized cleanly."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)
    doc_service = SupplierDocumentDomainService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    v1 = await doc_service.create_document_version(
        context=ctx,
        document_group_id=group.id,
        file_path="uploads/v1.pdf",
        file_name="v1.pdf",
        file_size_bytes=1000,
        file_mime_type="application/pdf",
        file_hash_sha256="1" * 64,
    )

    async def worker_approve():
        async with session_factory() as s:
            ds = SupplierDocumentDomainService(s)
            return await ds.record_review_decision(
                context=ctx,
                document_id=v1.id,
                decision="approved",
            )

    results = await asyncio.gather(
        worker_approve(),
        worker_approve(),
        return_exceptions=True,
    )

    # Both succeed sequentially without collision or raw DB errors
    for r in results:
        assert not isinstance(r, Exception), f"Unexpected failure: {r}"

    await session.refresh(v1)
    assert v1.is_effective_version is True


async def test_duplicate_requirement_link_race_and_error_translation(
    session: AsyncSession,
    session_factory,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Duplicate requirement links are translated into 409 Conflict both sequentially and under concurrent requests."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    req1 = DocumentRequirement(
        organization_id=ctx.organization_id,
        document_type_id=sample_doc_type.id,
        name="GST Certificate Req 1",
        is_mandatory=True,
    )
    req2 = DocumentRequirement(
        organization_id=ctx.organization_id,
        document_type_id=sample_doc_type.id,
        name="GST Certificate Req 2",
        is_mandatory=False,
    )
    session.add_all([req1, req2])
    await session.flush()

    # 1. Sequential duplicate link creation
    await group_service.add_requirement_link(ctx, group.id, req1.id)
    with pytest.raises(HTTPException) as exc_info:
        await group_service.add_requirement_link(ctx, group.id, req1.id)
    assert exc_info.value.status_code == 409
    assert "is already linked" in str(exc_info.value.detail)

    # 2. Concurrent duplicate link creation using independent sessions
    async def worker_add_link():
        async with session_factory() as s:
            gs = SupplierDocumentGroupService(s)
            return await gs.add_requirement_link(ctx, group.id, req2.id)

    results = await asyncio.gather(
        worker_add_link(),
        worker_add_link(),
        return_exceptions=True,
    )

    exceptions = [r for r in results if isinstance(r, Exception)]
    successes = [r for r in results if not isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(exceptions) == 1
    assert isinstance(exceptions[0], HTTPException)
    assert exceptions[0].status_code == 409
    assert "is already linked" in str(exceptions[0].detail)

    # Confirm exactly 2 links exist for the group in DB (req1 and req2)
    links = await group_service.link_repo.list_by_group(group.id, ctx.organization_id)
    assert len(links) == 2
    linked_req_ids = {link.requirement_id for link in links}
    assert linked_req_ids == {req1.id, req2.id}


async def test_unrelated_integrity_error_propagation(
    session: AsyncSession,
    test_org_a: dict,
    sample_supplier: Supplier,
    sample_doc_type: DocumentType,
):
    """Unrelated integrity errors (such as non-existent requirement foreign key) are not converted to duplicate link conflicts."""
    ctx = test_org_a["context"]
    group_service = SupplierDocumentGroupService(session)

    group = await group_service.create_group(
        context=ctx,
        supplier_id=sample_supplier.id,
        document_type_id=sample_doc_type.id,
        coverage_scope="supplier",
    )

    non_existent_req_id = uuid.uuid4()
    # add_requirement_link checks existence and returns 404
    with pytest.raises(HTTPException) as exc_info:
        await group_service.add_requirement_link(ctx, group.id, non_existent_req_id)
    assert exc_info.value.status_code == 404

    # Direct repository insert with non-existent requirement violates foreign key
    bogus_link = DocumentGroupRequirementLink(
        organization_id=ctx.organization_id,
        document_group_id=group.id,
        requirement_id=non_existent_req_id,
        link_origin="manual",
    )
    with pytest.raises(IntegrityError) as db_exc:
        await group_service.link_repo.create(bogus_link)
    assert "fk_group_req_links_requirement" in str(db_exc.value)
