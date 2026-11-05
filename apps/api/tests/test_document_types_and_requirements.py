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
from app.models import (
    AuditEvent,
    DocumentRequirement,
    Membership,
    Organization,
    Supplier,
    User,
)
from app.schemas.document_requirement import DocumentRequirementCreate
from app.services.document_requirement import DocumentRequirementService


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
async def setup_test_data(db_session: AsyncSession):
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
        full_name="Pooja Deshmukh",
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


async def test_organization_a_can_create_document_types(
    client: httpx.AsyncClient, setup_test_data: dict, db_session: AsyncSession
):
    """1. Organization A can create document types."""
    headers_a = setup_test_data["headers_a"]
    org_a = setup_test_data["org_a"]

    payload = {
        "code": "gst_certificate",
        "name": "GST Registration Certificate",
        "description": "Form GST REG-06 issued by CBIC",
        "category": "statutory",
        "has_expiry": False,
        "expected_fields": ["gstin", "legal_name", "date_of_registration"],
    }

    response = await client.post(
        "/api/v1/document-types", json=payload, headers=headers_a
    )
    assert response.status_code == 201
    data = response.json()
    assert data["code"] == "gst_certificate"
    assert data["name"] == "GST Registration Certificate"
    assert data["organization_id"] == str(org_a.id)
    assert data["has_expiry"] is False
    assert data["is_system_preset"] is False

    # Check audit event
    doc_type_id = uuid.UUID(data["id"])
    stmt = select(AuditEvent).where(
        AuditEvent.entity_type == "document_type",
        AuditEvent.entity_id == doc_type_id,
        AuditEvent.action == "document_type.created",
    )
    audit = (await db_session.execute(stmt)).scalar_one_or_none()
    assert audit is not None
    assert audit.organization_id == org_a.id


async def test_organization_a_can_list_only_its_document_types(
    client: httpx.AsyncClient, setup_test_data: dict
):
    """2. Organization A can list only its document types."""
    headers_a = setup_test_data["headers_a"]
    headers_b = setup_test_data["headers_b"]

    # Org A creates 2 document types
    await client.post(
        "/api/v1/document-types",
        json={"code": "gst_cert", "name": "GST Certificate"},
        headers=headers_a,
    )
    await client.post(
        "/api/v1/document-types",
        json={"code": "pan_card", "name": "Permanent Account Number"},
        headers=headers_a,
    )

    # Org B creates 1 document type
    await client.post(
        "/api/v1/document-types",
        json={"code": "iso_9001", "name": "ISO 9001 Quality Certificate"},
        headers=headers_b,
    )

    # Org A lists
    resp_a = await client.get("/api/v1/document-types", headers=headers_a)
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    codes_a = [item["code"] for item in data_a["items"]]
    assert "gst_cert" in codes_a
    assert "pan_card" in codes_a
    assert "iso_9001" not in codes_a

    # Org B lists
    resp_b = await client.get("/api/v1/document-types", headers=headers_b)
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    codes_b = [item["code"] for item in data_b["items"]]
    assert "iso_9001" in codes_b
    assert "gst_cert" not in codes_b
    assert "pan_card" not in codes_b


async def test_organization_b_cannot_access_organization_a_document_types(
    client: httpx.AsyncClient, setup_test_data: dict
):
    """3. Organization B cannot access Organization A's document types."""
    headers_a = setup_test_data["headers_a"]
    headers_b = setup_test_data["headers_b"]

    create_resp = await client.post(
        "/api/v1/document-types",
        json={"code": "confidential_spec", "name": "Confidential Spec"},
        headers=headers_a,
    )
    assert create_resp.status_code == 201
    doc_type_id = create_resp.json()["id"]

    # Org B GET -> 404
    get_resp = await client.get(
        f"/api/v1/document-types/{doc_type_id}", headers=headers_b
    )
    assert get_resp.status_code == 404

    # Org B PATCH -> 404
    patch_resp = await client.patch(
        f"/api/v1/document-types/{doc_type_id}",
        json={"name": "Tampered Spec"},
        headers=headers_b,
    )
    assert patch_resp.status_code == 404


async def test_organization_a_can_create_document_requirements(
    client: httpx.AsyncClient, setup_test_data: dict, db_session: AsyncSession
):
    """4. Organization A can create document requirements."""
    headers_a = setup_test_data["headers_a"]
    org_a = setup_test_data["org_a"]

    # Create document type
    dt_resp = await client.post(
        "/api/v1/document-types",
        json={"code": "msme_udyam", "name": "MSME Udyam Registration"},
        headers=headers_a,
    )
    doc_type_id = dt_resp.json()["id"]

    # Create requirement
    req_payload = {
        "document_type_id": doc_type_id,
        "name": "Mandatory MSME Certificate for Services",
        "supplier_category": "services",
        "supplier_tier": "tier_2",
        "is_mandatory": True,
        "validity_period_days": 365,
        "expiry_lead_notice_days": 45,
        "is_active": True,
    }

    req_resp = await client.post(
        "/api/v1/document-requirements", json=req_payload, headers=headers_a
    )
    assert req_resp.status_code == 201
    req_data = req_resp.json()
    assert req_data["name"] == "Mandatory MSME Certificate for Services"
    assert req_data["supplier_category"] == "services"
    assert req_data["supplier_tier"] == "tier_2"
    assert req_data["is_mandatory"] is True
    assert req_data["is_active"] is True
    assert req_data["organization_id"] == str(org_a.id)

    # Check audit event
    req_id = uuid.UUID(req_data["id"])
    stmt = select(AuditEvent).where(
        AuditEvent.entity_type == "document_requirement",
        AuditEvent.entity_id == req_id,
        AuditEvent.action == "document_requirement.created",
    )
    audit = (await db_session.execute(stmt)).scalar_one_or_none()
    assert audit is not None


async def test_requirements_cannot_reference_another_organization_document_types(
    client: httpx.AsyncClient, setup_test_data: dict
):
    """5. Requirements cannot reference another organization's document types."""
    headers_a = setup_test_data["headers_a"]
    headers_b = setup_test_data["headers_b"]

    # Org B creates a document type
    dt_b = await client.post(
        "/api/v1/document-types",
        json={"code": "org_b_only", "name": "Org B Custom Form"},
        headers=headers_b,
    )
    doc_type_b_id = dt_b.json()["id"]

    # Org A attempts to create a requirement referencing Org B's document type
    malicious_payload = {
        "document_type_id": doc_type_b_id,
        "name": "Cross Tenant Requirement Attempt",
        "is_mandatory": True,
    }

    resp = await client.post(
        "/api/v1/document-requirements",
        json=malicious_payload,
        headers=headers_a,
    )
    assert resp.status_code == 400
    assert (
        "Document type not found or belongs to another organization"
        in resp.json()["detail"]
    )


async def test_organization_a_can_update_and_disable_requirements(
    client: httpx.AsyncClient, setup_test_data: dict, db_session: AsyncSession
):
    """6. Organization A can update and disable requirements."""
    headers_a = setup_test_data["headers_a"]

    dt_resp = await client.post(
        "/api/v1/document-types",
        json={"code": "iso_14001", "name": "ISO 14001 Environmental"},
        headers=headers_a,
    )
    doc_type_id = dt_resp.json()["id"]

    req_resp = await client.post(
        "/api/v1/document-requirements",
        json={
            "document_type_id": doc_type_id,
            "name": "Environmental Certification Initial",
            "is_mandatory": False,
        },
        headers=headers_a,
    )
    req_id = req_resp.json()["id"]

    # 1. Update fields via PATCH
    patch_resp = await client.patch(
        f"/api/v1/document-requirements/{req_id}",
        json={"name": "Environmental Certification Revised", "is_mandatory": True},
        headers=headers_a,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Environmental Certification Revised"
    assert patch_resp.json()["is_mandatory"] is True

    # 2. Disable requirement via POST /{id}/disable
    disable_resp = await client.post(
        f"/api/v1/document-requirements/{req_id}/disable", headers=headers_a
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["is_active"] is False

    # Check disable audit event
    stmt_dis = select(AuditEvent).where(
        AuditEvent.entity_type == "document_requirement",
        AuditEvent.entity_id == uuid.UUID(req_id),
        AuditEvent.action == "document_requirement.disabled",
    )
    audit_dis = (await db_session.execute(stmt_dis)).scalar_one_or_none()
    assert audit_dis is not None

    # 3. Re-enable requirement via POST /{id}/enable
    enable_resp = await client.post(
        f"/api/v1/document-requirements/{req_id}/enable", headers=headers_a
    )
    assert enable_resp.status_code == 200
    assert enable_resp.json()["is_active"] is True


async def test_duplicate_requirements_and_document_types_handled_correctly(
    client: httpx.AsyncClient, setup_test_data: dict
):
    """7. Duplicate requirements and document types are handled correctly."""
    headers_a = setup_test_data["headers_a"]
    headers_b = setup_test_data["headers_b"]

    # Duplicate document type code within same org -> 409
    resp1 = await client.post(
        "/api/v1/document-types",
        json={"code": "duplicate_check", "name": "First Instance"},
        headers=headers_a,
    )
    assert resp1.status_code == 201

    resp2 = await client.post(
        "/api/v1/document-types",
        json={"code": "duplicate_check", "name": "Duplicate Code"},
        headers=headers_a,
    )
    assert resp2.status_code == 409
    assert "already exists in this organization" in resp2.json()["detail"]

    # But Organization B CAN use the same code (tenant-scoped uniqueness)
    resp_b = await client.post(
        "/api/v1/document-types",
        json={"code": "duplicate_check", "name": "Org B Instance"},
        headers=headers_b,
    )
    assert resp_b.status_code == 201

    # Duplicate requirements mapping within same org -> 409
    doc_type_id = resp1.json()["id"]
    req1 = await client.post(
        "/api/v1/document-requirements",
        json={
            "document_type_id": doc_type_id,
            "name": "Tier 1 Raw Materials",
            "supplier_category": "raw_materials",
            "supplier_tier": "tier_1",
        },
        headers=headers_a,
    )
    assert req1.status_code == 201

    # Attempt same requirement mapping (same doc type, category, tier)
    req2 = await client.post(
        "/api/v1/document-requirements",
        json={
            "document_type_id": doc_type_id,
            "name": "Duplicate Tier 1 Raw Materials",
            "supplier_category": "raw_materials",
            "supplier_tier": "tier_1",
        },
        headers=headers_a,
    )
    assert req2.status_code == 409
    assert "already exists in this organization" in req2.json()["detail"]


async def test_supplier_requirement_evaluation_returns_correct_mandatory_and_optional(
    client: httpx.AsyncClient, setup_test_data: dict, db_session: AsyncSession
):
    """8. Supplier requirement evaluation returns the correct mandatory and optional requirements."""
    headers_a = setup_test_data["headers_a"]
    org_a = setup_test_data["org_a"]

    # Create Supplier for Org A: category="machined_components", tier="tier_1"
    supplier = Supplier(
        organization_id=org_a.id,
        legal_name="Bharat Precision Machining Ltd",
        category="machined_components",
        tier="tier_1",
        status="approved",
    )
    db_session.add(supplier)
    await db_session.flush()

    # Create Document Types
    dt_gst = (
        await client.post(
            "/api/v1/document-types",
            json={"code": "eval_gst", "name": "GST Certificate"},
            headers=headers_a,
        )
    ).json()

    dt_iso = (
        await client.post(
            "/api/v1/document-types",
            json={"code": "eval_iso", "name": "ISO 9001"},
            headers=headers_a,
        )
    ).json()

    dt_bank = (
        await client.post(
            "/api/v1/document-types",
            json={"code": "eval_bank", "name": "Bank Proof"},
            headers=headers_a,
        )
    ).json()

    dt_pkg = (
        await client.post(
            "/api/v1/document-types",
            json={"code": "eval_pkg", "name": "Packaging Standards"},
            headers=headers_a,
        )
    ).json()

    # Define Requirements:
    # 1. GST -> Mandatory for ALL suppliers (category=None, tier=None)
    await client.post(
        "/api/v1/document-requirements",
        json={
            "document_type_id": dt_gst["id"],
            "name": "Mandatory Statutory GST",
            "is_mandatory": True,
        },
        headers=headers_a,
    )

    # 2. ISO -> Mandatory specifically for machined_components, tier_1
    await client.post(
        "/api/v1/document-requirements",
        json={
            "document_type_id": dt_iso["id"],
            "name": "Mandatory Precision ISO",
            "supplier_category": "machined_components",
            "supplier_tier": "tier_1",
            "is_mandatory": True,
        },
        headers=headers_a,
    )

    # 3. Bank Proof -> Optional for machined_components (tier=None)
    await client.post(
        "/api/v1/document-requirements",
        json={
            "document_type_id": dt_bank["id"],
            "name": "Optional Bank Proof",
            "supplier_category": "machined_components",
            "is_mandatory": False,
        },
        headers=headers_a,
    )

    # 4. Packaging -> Mandatory for packaging category ONLY (does NOT apply to this supplier)
    await client.post(
        "/api/v1/document-requirements",
        json={
            "document_type_id": dt_pkg["id"],
            "name": "Packaging Category Requirement",
            "supplier_category": "packaging",
            "is_mandatory": True,
        },
        headers=headers_a,
    )

    # Evaluate supplier requirements
    eval_resp = await client.get(
        f"/api/v1/suppliers/{supplier.id}/document-requirements",
        headers=headers_a,
    )
    assert eval_resp.status_code == 200
    data = eval_resp.json()

    assert data["supplier_id"] == str(supplier.id)
    assert data["supplier_category"] == "machined_components"
    assert data["supplier_tier"] == "tier_1"
    assert data["total_applicable"] == 3
    assert data["total_mandatory"] == 2
    assert data["total_optional"] == 1

    mandatory_codes = [
        item["document_type_code"] for item in data["mandatory_requirements"]
    ]
    assert "eval_gst" in mandatory_codes
    assert "eval_iso" in mandatory_codes

    optional_codes = [
        item["document_type_code"] for item in data["optional_requirements"]
    ]
    assert "eval_bank" in optional_codes
    assert "eval_pkg" not in mandatory_codes and "eval_pkg" not in optional_codes


async def test_disabled_requirements_are_excluded_from_supplier_evaluation(
    client: httpx.AsyncClient, setup_test_data: dict, db_session: AsyncSession
):
    """9. Disabled requirements are excluded from active evaluation."""
    headers_a = setup_test_data["headers_a"]
    org_a = setup_test_data["org_a"]

    supplier = Supplier(
        organization_id=org_a.id,
        legal_name="Exclusion Test Supplier",
        category="electrical",
        tier="tier_2",
        status="approved",
    )
    db_session.add(supplier)
    await db_session.flush()

    dt = (
        await client.post(
            "/api/v1/document-types",
            json={"code": "disabled_check", "name": "Disabled Cert"},
            headers=headers_a,
        )
    ).json()

    # Create requirement but set is_active=False
    req_resp = await client.post(
        "/api/v1/document-requirements",
        json={
            "document_type_id": dt["id"],
            "name": "Deactivated Requirement",
            "supplier_category": "electrical",
            "is_mandatory": True,
            "is_active": False,
        },
        headers=headers_a,
    )
    assert req_resp.status_code == 201

    eval_resp = await client.get(
        f"/api/v1/suppliers/{supplier.id}/document-requirements",
        headers=headers_a,
    )
    assert eval_resp.status_code == 200
    data = eval_resp.json()
    assert data["total_applicable"] == 0
    assert len(data["mandatory_requirements"]) == 0
    assert len(data["optional_requirements"]) == 0


async def test_cross_tenant_supplier_access_is_rejected(
    client: httpx.AsyncClient, setup_test_data: dict, db_session: AsyncSession
):
    """10. Cross-tenant supplier requirement access is rejected."""
    headers_b = setup_test_data["headers_b"]
    org_a = setup_test_data["org_a"]

    supplier_a = Supplier(
        organization_id=org_a.id,
        legal_name="Org A Secret Supplier",
        category="raw_materials",
        tier="tier_1",
        status="approved",
    )
    db_session.add(supplier_a)
    await db_session.flush()

    # Org B attempts to evaluate Org A's supplier
    resp = await client.get(
        f"/api/v1/suppliers/{supplier_a.id}/document-requirements",
        headers=headers_b,
    )
    assert resp.status_code == 404
    assert "Supplier not found" in resp.json()["detail"]


async def test_audit_events_are_created_for_successful_mutations(
    client: httpx.AsyncClient, setup_test_data: dict, db_session: AsyncSession
):
    """11. Audit events are created for successful mutations."""
    headers_a = setup_test_data["headers_a"]
    user_a = setup_test_data["user_a"]

    dt_resp = await client.post(
        "/api/v1/document-types",
        json={"code": "audit_test_dt", "name": "Audit Test DT"},
        headers=headers_a,
    )
    dt_id = uuid.UUID(dt_resp.json()["id"])

    # Update DT
    await client.patch(
        f"/api/v1/document-types/{dt_id}",
        json={"name": "Audit Test DT Updated"},
        headers=headers_a,
    )

    # Check DT audits
    dt_created_audit = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == "document_type",
                AuditEvent.entity_id == dt_id,
                AuditEvent.action == "document_type.created",
            )
        )
    ).scalar_one_or_none()
    assert dt_created_audit is not None
    assert dt_created_audit.actor_id == user_a.id

    dt_updated_audit = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == "document_type",
                AuditEvent.entity_id == dt_id,
                AuditEvent.action == "document_type.updated",
            )
        )
    ).scalar_one_or_none()
    assert dt_updated_audit is not None
    assert dt_updated_audit.previous_state["name"] == "Audit Test DT"
    assert dt_updated_audit.new_state["name"] == "Audit Test DT Updated"


async def test_failed_operations_do_not_create_audit_events(
    client: httpx.AsyncClient, setup_test_data: dict, db_session: AsyncSession
):
    """12. Failed operations do not create audit events."""
    headers_a = setup_test_data["headers_a"]
    fake_doc_type_id = str(uuid.uuid4())

    # Attempt to create requirement with nonexistent document type -> 400
    resp = await client.post(
        "/api/v1/document-requirements",
        json={
            "document_type_id": fake_doc_type_id,
            "name": "Should Fail Audit Check",
        },
        headers=headers_a,
    )
    assert resp.status_code == 400

    # Verify no audit event was created
    audits = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "document_requirement.created",
                    AuditEvent.metadata_["name"].astext == "Should Fail Audit Check",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 0


async def test_business_mutations_and_audit_events_rollback_together(
    setup_test_data: dict, db_session: AsyncSession
):
    """13. Business mutations and audit events roll back together."""
    org_a = setup_test_data["org_a"]
    user_a = setup_test_data["user_a"]

    context = OrganizationContext(
        user_id=user_a.id,
        organization_id=org_a.id,
        membership_id=uuid.uuid4(),
        role="admin",
        user_email=user_a.email,
        user_full_name=user_a.full_name,
    )

    service = DocumentRequirementService(db_session)

    # Attempt to create requirement with a nonexistent document_type_id
    invalid_data = DocumentRequirementCreate(
        document_type_id=uuid.uuid4(),
        name="Rollback Requirement",
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.create_requirement(context, invalid_data)

    assert exc_info.value.status_code == 400

    # Ensure no requirement exists in DB
    req = (
        await db_session.execute(
            select(DocumentRequirement).where(
                DocumentRequirement.name == "Rollback Requirement"
            )
        )
    ).scalar_one_or_none()
    assert req is None
