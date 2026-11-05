import uuid

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.context import OrganizationContext
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models import AuditEvent, Membership, Organization, Supplier, User
from app.schemas.supplier import SupplierCreate
from app.services.supplier import SupplierService


@pytest.fixture
async def test_engine():
    """Create test engine targeting vendorflow_test.

    CRITICAL SAFETY RULE:
    Enforces that the database name is strictly 'vendorflow_test'.
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
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def supplier_test_data(db_session: AsyncSession):
    # Two distinct buyer organizations
    org_a = Organization(
        name="Tata AutoComp Pune",
        slug=f"tata-autocomp-{uuid.uuid4().hex[:8]}",
        status="active",
    )
    org_b = Organization(
        name="Bharat Forge Pune",
        slug=f"bharat-forge-{uuid.uuid4().hex[:8]}",
        status="active",
    )
    # Users for each org
    user_a = User(
        email=f"buyer_a_{uuid.uuid4().hex[:8]}@tata.com",
        hashed_password="hashed_password_a",
        full_name="Rajesh Sharma",
        is_active=True,
    )
    user_b = User(
        email=f"buyer_b_{uuid.uuid4().hex[:8]}@bharat.com",
        hashed_password="hashed_password_b",
        full_name="Anil Kulkarni",
        is_active=True,
    )
    db_session.add_all([org_a, org_b, user_a, user_b])
    await db_session.flush()

    # Memberships
    mem_a = Membership(
        organization_id=org_a.id,
        user_id=user_a.id,
        role="admin",
        is_active=True,
    )
    mem_b = Membership(
        organization_id=org_b.id,
        user_id=user_b.id,
        role="admin",
        is_active=True,
    )
    db_session.add_all([mem_a, mem_b])
    await db_session.flush()

    headers_a = {
        "X-Dev-User-Id": str(user_a.id),
        "X-Dev-Organization-Id": str(org_a.id),
    }
    headers_b = {
        "X-Dev-User-Id": str(user_b.id),
        "X-Dev-Organization-Id": str(org_b.id),
    }

    return {
        "org_a": org_a,
        "org_b": org_b,
        "user_a": user_a,
        "user_b": user_b,
        "headers_a": headers_a,
        "headers_b": headers_b,
    }


async def test_org_a_can_create_supplier(
    client: httpx.AsyncClient, supplier_test_data: dict, db_session: AsyncSession
):
    headers_a = supplier_test_data["headers_a"]
    org_a = supplier_test_data["org_a"]

    payload = {
        "legal_name": "Precision Fasteners Pvt Ltd",
        "trade_name": "Precision Fasteners",
        "vendor_code": "V-1001",
        "category": "machined_components",
        "tier": "tier_1",
        "gstin": "27AABCU9603R1ZM",
        "pan": "AABCU9603R",
        "primary_contact_name": "Suresh Patil",
        "primary_contact_email": "suresh@precisionfasteners.com",
        "city": "Pune",
        "state": "Maharashtra",
        "pincode": "411018",
    }

    response = await client.post("/api/v1/suppliers", json=payload, headers=headers_a)
    assert response.status_code == 201
    data = response.json()
    assert data["legal_name"] == "Precision Fasteners Pvt Ltd"
    assert data["organization_id"] == str(org_a.id)
    assert data["status"] == "draft"
    assert data["compliance_status"] == "pending"
    assert data["vendor_code"] == "V-1001"

    # Verify audit event was created
    supplier_id = uuid.UUID(data["id"])
    stmt = select(AuditEvent).where(
        AuditEvent.entity_id == supplier_id,
        AuditEvent.action == "supplier.created",
    )
    res = await db_session.execute(stmt)
    audit = res.scalar_one_or_none()
    assert audit is not None
    assert audit.organization_id == org_a.id
    assert audit.actor_id == supplier_test_data["user_a"].id


async def test_org_a_can_list_and_paginate_suppliers(
    client: httpx.AsyncClient, supplier_test_data: dict
):
    headers_a = supplier_test_data["headers_a"]

    # Create 3 suppliers
    for i in range(3):
        await client.post(
            "/api/v1/suppliers",
            json={
                "legal_name": f"Supplier A {i}",
                "vendor_code": f"VC-A-{i}",
                "category": "raw_materials",
                "tier": "tier_2",
            },
            headers=headers_a,
        )

    # List with pagination page=1, page_size=2
    response = await client.get(
        "/api/v1/suppliers?page=1&page_size=2", headers=headers_a
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert data["total_pages"] == 2
    assert len(data["items"]) == 2

    # Page 2
    response_p2 = await client.get(
        "/api/v1/suppliers?page=2&page_size=2", headers=headers_a
    )
    assert response_p2.status_code == 200
    data_p2 = response_p2.json()
    assert len(data_p2["items"]) == 1


async def test_org_a_can_retrieve_supplier(
    client: httpx.AsyncClient, supplier_test_data: dict
):
    headers_a = supplier_test_data["headers_a"]

    create_resp = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Apex Tooling Pvt Ltd"},
        headers=headers_a,
    )
    supplier_id = create_resp.json()["id"]

    get_resp = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers_a)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == supplier_id
    assert get_resp.json()["legal_name"] == "Apex Tooling Pvt Ltd"


async def test_org_a_can_update_supplier(
    client: httpx.AsyncClient, supplier_test_data: dict, db_session: AsyncSession
):
    headers_a = supplier_test_data["headers_a"]

    create_resp = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Original Name", "city": "Pune"},
        headers=headers_a,
    )
    supplier_id = create_resp.json()["id"]

    # Partial update
    update_payload = {
        "legal_name": "Updated Name",
        "city": "Mumbai",
        "notes": "Verified facility.",
    }
    update_resp = await client.patch(
        f"/api/v1/suppliers/{supplier_id}", json=update_payload, headers=headers_a
    )
    assert update_resp.status_code == 200
    updated_data = update_resp.json()
    assert updated_data["legal_name"] == "Updated Name"
    assert updated_data["city"] == "Mumbai"
    assert updated_data["notes"] == "Verified facility."

    # Verify audit event for update
    stmt = select(AuditEvent).where(
        AuditEvent.entity_id == uuid.UUID(supplier_id),
        AuditEvent.action == "supplier.updated",
    )
    res = await db_session.execute(stmt)
    audit = res.scalar_one_or_none()
    assert audit is not None
    assert audit.previous_state == {
        "legal_name": "Original Name",
        "city": "Pune",
        "notes": None,
    }
    assert audit.new_state == {
        "legal_name": "Updated Name",
        "city": "Mumbai",
        "notes": "Verified facility.",
    }
    assert set(audit.metadata_["changed_fields"]) == {"legal_name", "city", "notes"}


async def test_org_a_can_archive_supplier(
    client: httpx.AsyncClient, supplier_test_data: dict, db_session: AsyncSession
):
    headers_a = supplier_test_data["headers_a"]

    create_resp = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Archivable Supplier"},
        headers=headers_a,
    )
    supplier_id = create_resp.json()["id"]

    archive_resp = await client.post(
        f"/api/v1/suppliers/{supplier_id}/archive", headers=headers_a
    )
    assert archive_resp.status_code == 200
    assert archive_resp.json()["status"] == "archived"

    # Verify soft archive: record is NOT deleted, status is archived
    supplier_uuid = uuid.UUID(supplier_id)
    stmt = select(Supplier).where(Supplier.id == supplier_uuid)
    res = await db_session.execute(stmt)
    supplier_in_db = res.scalar_one_or_none()
    assert supplier_in_db is not None
    assert supplier_in_db.status == "archived"

    # Verify audit event for archive
    stmt_audit = select(AuditEvent).where(
        AuditEvent.entity_id == supplier_uuid,
        AuditEvent.action == "supplier.archived",
    )
    res_audit = await db_session.execute(stmt_audit)
    audit = res_audit.scalar_one_or_none()
    assert audit is not None
    assert audit.new_state == {"status": "archived"}

    # Repeated archive request is rejected safely
    repeat_resp = await client.post(
        f"/api/v1/suppliers/{supplier_id}/archive", headers=headers_a
    )
    assert repeat_resp.status_code == 400
    assert "already archived" in repeat_resp.json()["detail"]


async def test_cannot_update_archived_supplier(
    client: httpx.AsyncClient, supplier_test_data: dict
):
    headers_a = supplier_test_data["headers_a"]

    create_resp = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "To Be Archived"},
        headers=headers_a,
    )
    supplier_id = create_resp.json()["id"]

    await client.post(f"/api/v1/suppliers/{supplier_id}/archive", headers=headers_a)

    # Attempt to update archived supplier
    update_resp = await client.patch(
        f"/api/v1/suppliers/{supplier_id}",
        json={"legal_name": "Mutated Name"},
        headers=headers_a,
    )
    assert update_resp.status_code == 400
    assert "Cannot modify an archived supplier" in update_resp.json()["detail"]


async def test_org_b_cannot_retrieve_org_a_supplier(
    client: httpx.AsyncClient, supplier_test_data: dict
):
    headers_a = supplier_test_data["headers_a"]
    headers_b = supplier_test_data["headers_b"]

    create_resp = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Org A Confidential Supplier"},
        headers=headers_a,
    )
    supplier_id = create_resp.json()["id"]

    # Org B attempts to retrieve Org A's supplier
    get_resp = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=headers_b)
    assert get_resp.status_code == 404
    assert "Supplier not found" in get_resp.json()["detail"]


async def test_org_b_cannot_update_org_a_supplier(
    client: httpx.AsyncClient, supplier_test_data: dict
):
    headers_a = supplier_test_data["headers_a"]
    headers_b = supplier_test_data["headers_b"]

    create_resp = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Org A Supplier"},
        headers=headers_a,
    )
    supplier_id = create_resp.json()["id"]

    # Org B attempts to update Org A's supplier
    patch_resp = await client.patch(
        f"/api/v1/suppliers/{supplier_id}",
        json={"legal_name": "Compromised Name"},
        headers=headers_b,
    )
    assert patch_resp.status_code == 404


async def test_org_b_cannot_archive_org_a_supplier(
    client: httpx.AsyncClient, supplier_test_data: dict
):
    headers_a = supplier_test_data["headers_a"]
    headers_b = supplier_test_data["headers_b"]

    create_resp = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Org A Supplier for Archival"},
        headers=headers_a,
    )
    supplier_id = create_resp.json()["id"]

    # Org B attempts to archive Org A's supplier
    archive_resp = await client.post(
        f"/api/v1/suppliers/{supplier_id}/archive", headers=headers_b
    )
    assert archive_resp.status_code == 404


async def test_org_a_list_never_includes_org_b_suppliers(
    client: httpx.AsyncClient, supplier_test_data: dict
):
    headers_a = supplier_test_data["headers_a"]
    headers_b = supplier_test_data["headers_b"]

    # Create supplier in Org A
    await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Org A Exclusively"},
        headers=headers_a,
    )

    # Create supplier in Org B
    await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Org B Exclusively"},
        headers=headers_b,
    )

    # Org A lists suppliers
    list_a = await client.get("/api/v1/suppliers", headers=headers_a)
    assert list_a.status_code == 200
    names_a = [s["legal_name"] for s in list_a.json()["items"]]
    assert "Org A Exclusively" in names_a
    assert "Org B Exclusively" not in names_a

    # Org B lists suppliers
    list_b = await client.get("/api/v1/suppliers", headers=headers_b)
    assert list_b.status_code == 200
    names_b = [s["legal_name"] for s in list_b.json()["items"]]
    assert "Org B Exclusively" in names_b
    assert "Org A Exclusively" not in names_b


async def test_cannot_inject_organization_id_in_payload(
    client: httpx.AsyncClient, supplier_test_data: dict
):
    headers_a = supplier_test_data["headers_a"]
    fake_org_id = str(uuid.uuid4())

    payload = {
        "legal_name": "Injection Attempt Supplier",
        "organization_id": fake_org_id,
    }

    # Pydantic schema with extra='forbid' should reject extra fields
    response = await client.post("/api/v1/suppliers", json=payload, headers=headers_a)
    assert response.status_code == 422


async def test_invalid_input_validation(
    client: httpx.AsyncClient, supplier_test_data: dict
):
    headers_a = supplier_test_data["headers_a"]

    # 1. Missing legal_name
    resp_no_name = await client.post(
        "/api/v1/suppliers", json={"category": "tooling"}, headers=headers_a
    )
    assert resp_no_name.status_code == 422

    # 2. Invalid GSTIN format
    resp_bad_gstin = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Bad GST Supplier", "gstin": "INVALID_GSTIN_123"},
        headers=headers_a,
    )
    assert resp_bad_gstin.status_code == 422

    # 3. Invalid PAN format
    resp_bad_pan = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Bad PAN Supplier", "pan": "NOT_A_PAN"},
        headers=headers_a,
    )
    assert resp_bad_pan.status_code == 422

    # 4. Invalid tier
    resp_bad_tier = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": "Bad Tier Supplier", "tier": "tier_99"},
        headers=headers_a,
    )
    assert resp_bad_tier.status_code == 422


async def test_duplicate_uniqueness_rules_within_organization(
    client: httpx.AsyncClient, supplier_test_data: dict
):
    headers_a = supplier_test_data["headers_a"]
    headers_b = supplier_test_data["headers_b"]

    # Create supplier in Org A with vendor_code 'VC-DUP-1' and GSTIN '27AABCU9603R1ZM'
    await client.post(
        "/api/v1/suppliers",
        json={
            "legal_name": "Original Supplier",
            "vendor_code": "VC-DUP-1",
            "gstin": "27AABCU9603R1ZM",
        },
        headers=headers_a,
    )

    # Attempt to create another supplier in Org A with duplicate vendor_code
    resp_dup_vc = await client.post(
        "/api/v1/suppliers",
        json={
            "legal_name": "Duplicate VC Supplier",
            "vendor_code": "VC-DUP-1",
        },
        headers=headers_a,
    )
    assert resp_dup_vc.status_code == 409
    assert "vendor code" in resp_dup_vc.json()["detail"]

    # Attempt to create another supplier in Org A with duplicate GSTIN
    resp_dup_gstin = await client.post(
        "/api/v1/suppliers",
        json={
            "legal_name": "Duplicate GSTIN Supplier",
            "gstin": "27AABCU9603R1ZM",
        },
        headers=headers_a,
    )
    assert resp_dup_gstin.status_code == 409
    assert "GSTIN" in resp_dup_gstin.json()["detail"]

    # Notice: In Org B, the same vendor code and GSTIN CAN be used because records are tenant-scoped!
    resp_org_b_ok = await client.post(
        "/api/v1/suppliers",
        json={
            "legal_name": "Org B Same Supplier",
            "vendor_code": "VC-DUP-1",
            "gstin": "27AABCU9603R1ZM",
        },
        headers=headers_b,
    )
    assert resp_org_b_ok.status_code == 201


async def test_failed_operations_do_not_create_audit_events(
    client: httpx.AsyncClient, supplier_test_data: dict, db_session: AsyncSession
):
    headers_a = supplier_test_data["headers_a"]
    org_a = supplier_test_data["org_a"]

    # Count existing audit events
    stmt_count = select(AuditEvent).where(AuditEvent.organization_id == org_a.id)
    count_before = len((await db_session.execute(stmt_count)).scalars().all())

    # Trigger 422 failure
    resp_fail = await client.post(
        "/api/v1/suppliers",
        json={"legal_name": ""},  # empty string violates min_length=1
        headers=headers_a,
    )
    assert resp_fail.status_code == 422

    # Audit count should remain unchanged
    count_after = len((await db_session.execute(stmt_count)).scalars().all())
    assert count_after == count_before


async def test_supplier_and_audit_atomicity_on_failure(
    supplier_test_data: dict, db_session: AsyncSession
):
    """Verify that if database commit fails or is aborted, supplier and audit event roll back together."""
    org_a = supplier_test_data["org_a"]
    user_a = supplier_test_data["user_a"]

    context = OrganizationContext(
        user_id=user_a.id,
        organization_id=org_a.id,
        membership_id=uuid.uuid4(),
        role="admin",
        user_email=user_a.email,
        user_full_name=user_a.full_name,
    )

    service = SupplierService(db_session)
    data = SupplierCreate(legal_name="Rollback Test Supplier", vendor_code="VC-RB-1")

    # First creation succeeds
    await service.create_supplier(context, data)

    # Second creation with duplicate vendor_code fails and rolls back
    with pytest.raises(HTTPException) as exc_info:
        await service.create_supplier(context, data)
    assert exc_info.value.status_code == 409

    # Verify no dangling audit event for the second attempt was committed
    stmt = select(AuditEvent).where(
        AuditEvent.organization_id == org_a.id,
        AuditEvent.metadata_["vendor_code"].astext == "VC-RB-1",
    )
    res = await db_session.execute(stmt)
    audits = res.scalars().all()
    # Exactly one audit event exists (from the first successful creation)
    assert len(audits) == 1
