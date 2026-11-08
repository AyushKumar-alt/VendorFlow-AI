import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.core.config import settings
from app.models.document_review import DocumentReview
from app.models.document_type import DocumentType
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.supplier import Supplier
from app.models.supplier_document import SupplierDocument
from app.models.supplier_document_group import SupplierDocumentGroup
from app.models.user import User
from app.repositories.document_review import DocumentReviewRepository
from app.schemas.document_group_requirement_link import (
    DocumentGroupRequirementLinkCreate,
)
from app.schemas.document_review import (
    DocumentReviewCreate,
    validate_verified_data_payload,
)
from app.schemas.supplier_document import (
    EffectiveDocumentRevokeRequest,
    SupplierDocumentResponse,
)
from app.schemas.supplier_document_group import (
    SupplierDocumentGroupCreate,
)
from app.schemas.supplier_site import (
    SupplierSiteCreate,
    SupplierSiteUpdate,
)

# =============================================================================
# 1. SupplierSite Schema Unit Tests
# =============================================================================


def test_supplier_site_create_valid():
    site = SupplierSiteCreate(
        site_code="PUNE-01",
        site_name="Chakan Manufacturing Plant",
        address_line1="Plot 42, MIDC Phase II",
        city="Pune",
        state="Maharashtra",
        postal_code="410501",
        country_code="IN",
        gstin="27AABCU9603R1ZM",
    )
    assert site.site_code == "PUNE-01"
    assert site.site_name == "Chakan Manufacturing Plant"
    assert site.country_code == "IN"


def test_supplier_site_create_rejects_whitespace_name():
    with pytest.raises(ValidationError, match="empty or whitespace-only"):
        SupplierSiteCreate(
            site_code="PUNE-01",
            site_name="   \t\n  ",
        )


def test_supplier_site_create_rejects_invalid_gstin_and_country():
    with pytest.raises(ValidationError):
        SupplierSiteCreate(
            site_code="PUNE-01",
            site_name="Chakan Plant",
            gstin="INVALID_GSTIN_123",
        )
    with pytest.raises(ValidationError):
        SupplierSiteCreate(
            site_code="PUNE-01",
            site_name="Chakan Plant",
            country_code="IND",  # Must be 2 uppercase letters
        )


def test_supplier_site_update_patch_semantics():
    # 1. Omitted fields remain omitted from model_fields_set
    update_omitted = SupplierSiteUpdate(city="Pune")
    assert update_omitted.model_fields_set == {"city"}
    assert update_omitted.city == "Pune"
    assert "state" not in update_omitted.model_fields_set
    assert update_omitted.state is None

    # 2. Explicit null is permitted for clearable nullable fields
    update_clear = SupplierSiteUpdate(
        address_line1=None,
        address_line2=None,
        city=None,
        state=None,
        postal_code=None,
        gstin=None,
    )
    assert update_clear.model_fields_set == {
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "gstin",
    }
    assert update_clear.address_line1 is None
    assert update_clear.gstin is None

    # 3. Explicit null is REJECTED for non-nullable fields
    with pytest.raises(ValidationError, match="Field 'site_name' is non-nullable"):
        SupplierSiteUpdate(site_name=None)

    with pytest.raises(ValidationError, match="Field 'country_code' is non-nullable"):
        SupplierSiteUpdate(country_code=None)

    with pytest.raises(ValidationError, match="Field 'is_active' is non-nullable"):
        SupplierSiteUpdate(is_active=None)

    # 4. Whitespace-only site_name is rejected
    with pytest.raises(ValidationError, match="empty or whitespace-only"):
        SupplierSiteUpdate(site_name="   ")

    # 5. Immutable and unknown fields are forbidden
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SupplierSiteUpdate.model_validate({"site_code": "NEW_CODE"})

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SupplierSiteUpdate.model_validate({"supplier_id": str(uuid.uuid4())})

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SupplierSiteUpdate.model_validate({"organization_id": str(uuid.uuid4())})


# =============================================================================
# 2. Document Group & Requirement Link Schema Tests
# =============================================================================


def test_supplier_document_group_create_scope_validation():
    doc_type_id = uuid.uuid4()
    site_id = uuid.uuid4()

    # Valid supplier scope
    g1 = SupplierDocumentGroupCreate(
        document_type_id=doc_type_id,
        coverage_scope="supplier",
        site_id=None,
    )
    assert g1.coverage_scope == "supplier"

    # Supplier scope with site_id must fail
    with pytest.raises(
        ValidationError, match="site_id must be null for supplier-scoped"
    ):
        SupplierDocumentGroupCreate(
            document_type_id=doc_type_id,
            coverage_scope="supplier",
            site_id=site_id,
        )

    # Site scope without site_id must fail
    with pytest.raises(ValidationError, match="site_id is required for site-scoped"):
        SupplierDocumentGroupCreate(
            document_type_id=doc_type_id,
            coverage_scope="site",
            site_id=None,
        )

    # Valid site scope
    g2 = SupplierDocumentGroupCreate(
        document_type_id=doc_type_id,
        coverage_scope="site",
        site_id=site_id,
    )
    assert g2.coverage_scope == "site"
    assert g2.site_id == site_id


def test_document_group_requirement_link_create():
    req_id = uuid.uuid4()
    link = DocumentGroupRequirementLinkCreate(
        requirement_id=req_id, link_origin="manual"
    )
    assert link.link_origin == "manual"

    # Default is "manual"
    link_default = DocumentGroupRequirementLinkCreate(requirement_id=req_id)
    assert link_default.link_origin == "manual"

    # Disallowed origins on client schema (e.g. policy_engine and legacy_backfill are not client-creatable)
    with pytest.raises(ValidationError):
        DocumentGroupRequirementLinkCreate(
            requirement_id=req_id, link_origin="legacy_backfill"
        )

    with pytest.raises(ValidationError):
        DocumentGroupRequirementLinkCreate(
            requirement_id=req_id, link_origin="policy_engine"
        )

    with pytest.raises(ValidationError):
        DocumentGroupRequirementLinkCreate(requirement_id=req_id, link_origin="system")

    with pytest.raises(ValidationError):
        DocumentGroupRequirementLinkCreate(requirement_id=req_id, link_origin="system")


# =============================================================================
# 3. Document Response & Revocation Schema Tests
# =============================================================================


def test_supplier_document_response_omits_file_path():
    # Verify file_path is NOT a field on SupplierDocumentResponse
    fields = SupplierDocumentResponse.model_fields.keys()
    assert "file_path" not in fields, (
        "file_path must not be exposed in public API schema!"
    )

    doc_data = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "supplier_id": uuid.uuid4(),
        "document_type_id": uuid.uuid4(),
        "document_group_id": uuid.uuid4(),
        "version": 1,
        "previous_version_id": None,
        "is_effective_version": False,
        "processing_status": "uploaded",
        "legacy_status": None,
        "approval_provenance": None,
        "file_name": "certificate.pdf",
        "file_size_bytes": 1024,
        "file_mime_type": "application/pdf",
        "file_hash_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "issued_date": None,
        "expiry_date": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    resp = SupplierDocumentResponse.model_validate(doc_data)
    serialized = resp.model_dump()
    assert "file_path" not in serialized
    assert resp.is_effective_version is False
    assert resp.processing_status == "uploaded"


def test_effective_document_revoke_request():
    req = EffectiveDocumentRevokeRequest(reason="License expired by authority")
    assert req.reason == "License expired by authority"

    with pytest.raises(ValidationError, match="empty or whitespace-only"):
        EffectiveDocumentRevokeRequest(reason="   ")

    with pytest.raises(ValidationError):
        EffectiveDocumentRevokeRequest(reason="no")  # min_length=3


# =============================================================================
# 4. Document Review & Recursive verified_data Schema Tests
# =============================================================================


def test_document_review_create_decision_policy():
    # 1. Approved requires rejection_reason_code to be None
    r_approved = DocumentReviewCreate(decision="approved")
    assert r_approved.decision == "approved"
    assert r_approved.rejection_reason_code is None

    with pytest.raises(ValidationError, match="rejection_reason_code must be null"):
        DocumentReviewCreate(decision="approved", rejection_reason_code="illegible")

    # 2. Rejected requires rejection_reason_code
    with pytest.raises(ValidationError, match="rejection_reason_code is mandatory"):
        DocumentReviewCreate(decision="rejected", rejection_reason_code=None)

    r_rejected = DocumentReviewCreate(
        decision="rejected",
        rejection_reason_code="mismatched_gstin",
        comments="GSTIN does not match legal entity",
    )
    assert r_rejected.rejection_reason_code == "mismatched_gstin"

    # 3. Revision requested requires rejection_reason_code
    with pytest.raises(ValidationError, match="rejection_reason_code is mandatory"):
        DocumentReviewCreate(decision="revision_requested", rejection_reason_code=None)

    r_rev = DocumentReviewCreate(
        decision="revision_requested",
        rejection_reason_code="incomplete_pages",
    )
    assert r_rev.rejection_reason_code == "incomplete_pages"


def test_verified_data_valid_structure():
    payload = {
        "gstin": "27AABCU9603R1ZM",
        "taxpayer_name": "Tata AutoComp Systems Ltd",
        "confidence_score": 0.985,
        "is_verified": True,
        "error_codes": None,
        "nested_details": {
            "state_code": 27,
            "center_jurisdiction": "Pune-1",
            "flags": ["active", "regular"],
        },
    }
    validated = validate_verified_data_payload(payload)
    assert validated["gstin"] == "27AABCU9603R1ZM"
    assert validated["confidence_score"] == 0.985


def test_verified_data_rejects_non_dict_root():
    with pytest.raises((ValueError, TypeError), match="root must be a JSON object"):
        validate_verified_data_payload(["item1", "item2"])

    with pytest.raises((ValueError, TypeError), match="root must be a JSON object"):
        validate_verified_data_payload("scalar string")


def test_verified_data_size_and_root_keys_limit():
    # Exceed 50 root keys
    too_many_keys = {f"key_{i}": i for i in range(51)}
    with pytest.raises(ValueError, match="cannot exceed 50 keys"):
        validate_verified_data_payload(too_many_keys)

    # Exceed 64 KB
    large_payload = {"large_text": "A" * 66_000}
    with pytest.raises(ValueError, match="exceeds maximum limit of 65536 bytes"):
        validate_verified_data_payload(large_payload)


def test_verified_data_nesting_depth_limit():
    # Max depth is 5 (root dictionary counts as depth 1)
    # depth 5: root (1) -> l2 (2) -> l3 (3) -> l4 (4) -> l5 (5)
    valid_depth_5 = {"level_2": {"level_3": {"level_4": {"level_5": "deep value"}}}}
    validate_verified_data_payload(valid_depth_5)

    # depth 6: root (1) -> l2 (2) -> l3 (3) -> l4 (4) -> l5 (5) -> l6 (6) -> FAILS
    invalid_depth_6 = {
        "level_2": {"level_3": {"level_4": {"level_5": {"level_6": "too deep"}}}}
    }
    with pytest.raises(ValueError, match="exceeds maximum nesting depth of 5"):
        validate_verified_data_payload(invalid_depth_6)


def test_verified_data_collection_and_node_limits():
    # Dict with > 50 keys inside
    dict_too_large = {"nested": {f"inner_{i}": i for i in range(51)}}
    with pytest.raises(ValueError, match="exceeds maximum allowed keys"):
        validate_verified_data_payload(dict_too_large)

    # List with > 100 items
    list_too_large = {"items": list(range(101))}
    with pytest.raises(ValueError, match="exceeds maximum allowed items"):
        validate_verified_data_payload(list_too_large)

    # Total nodes > 200:
    # 25 keys, each has a dict with 2 lists of 3 items = 1 + 25 * (1 dict + 2 lists + 6 scalars) = 226 nodes
    many_nodes = {f"k_{i}": {"a": [1, 2, 3], "b": [4, 5, 6]} for i in range(25)}
    with pytest.raises(ValueError, match="exceeds maximum total node count of 200"):
        validate_verified_data_payload(many_nodes)


def test_verified_data_key_format_validation():
    # Valid keys: alphanumeric, dash, underscore
    validate_verified_data_payload({"valid_key-1": 123, "Another_Key": "abc"})

    # Invalid keys (spaces, periods, symbols)
    with pytest.raises(ValueError, match="Invalid dictionary key"):
        validate_verified_data_payload({"invalid key with space": 123})

    with pytest.raises(ValueError, match="Invalid dictionary key"):
        validate_verified_data_payload({"dotted.key": 123})

    with pytest.raises(ValueError, match="Invalid dictionary key"):
        validate_verified_data_payload({"": 123})


def test_verified_data_rejects_non_finite_floats_and_unsupported_types():
    with pytest.raises(ValueError, match="Non-finite float values"):
        validate_verified_data_payload({"score": float("nan")})

    with pytest.raises(ValueError, match="Non-finite float values"):
        validate_verified_data_payload({"score": float("inf")})

    with pytest.raises(ValueError, match="Non-finite float values"):
        validate_verified_data_payload({"score": float("-inf")})

    # Unsupported types
    with pytest.raises(ValueError, match="Unsupported value type"):
        validate_verified_data_payload({"set_val": {1, 2, 3}})

    with pytest.raises(ValueError, match="Unsupported value type"):
        validate_verified_data_payload({"tuple_val": (1, 2)})


def test_verified_data_unicode_multibyte_byte_limit():
    # Multi-byte UTF-8 string: Hindi devanagari characters (3 bytes each)
    hindi_text = "नमस्ते भारत " * 1000
    serialized = json.dumps({"text": hindi_text}, ensure_ascii=False)
    byte_len = len(serialized.encode("utf-8"))
    assert byte_len > len(hindi_text)  # Confirms multi-byte UTF-8 calculation

    payload = {"text": hindi_text}
    validate_verified_data_payload(payload)


# =============================================================================
# 5. DocumentReviewRepository Integration Tests
# =============================================================================


def _ensure_head_schema():
    api_dir = Path(__file__).resolve().parents[1]
    alembic_ini = api_dir / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(api_dir / "alembic"))
    command.upgrade(cfg, "f129c5e378bc")


@pytest.fixture
async def test_engine():
    test_url = settings.get_test_database_url()
    approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
    if test_url.database != approved_db:
        raise RuntimeError(
            f"Safety guard failure: Expected '{approved_db}', got '{test_url.database}'."
        )
    if test_url.database == "vendorflow":
        raise RuntimeError(
            f"CRITICAL SAFETY VIOLATION: Refusing to connect to production database '{test_url.database}'!"
        )
    await asyncio.to_thread(_ensure_head_schema)
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


@pytest.mark.asyncio
async def test_document_review_repository_ordering_and_tenant_isolation(
    db_session: AsyncSession,
):
    repo = DocumentReviewRepository(db_session)

    # 1. Create two distinct tenant organizations
    org_a = Organization(name="Tenant A", slug=f"tenant-a-{uuid.uuid4().hex[:8]}")
    org_b = Organization(name="Tenant B", slug=f"tenant-b-{uuid.uuid4().hex[:8]}")
    user_a = User(
        email=f"reviewer_a_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="pw",
        full_name="Reviewer A",
    )
    user_b = User(
        email=f"reviewer_b_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="pw",
        full_name="Reviewer B",
    )
    db_session.add_all([org_a, org_b, user_a, user_b])
    await db_session.flush()

    mem_a = Membership(
        organization_id=org_a.id, user_id=user_a.id, role="reviewer", is_active=True
    )
    mem_b = Membership(
        organization_id=org_b.id, user_id=user_b.id, role="reviewer", is_active=True
    )
    db_session.add_all([mem_a, mem_b])
    await db_session.flush()

    # Create supplier and document type in Tenant A
    sup_a = Supplier(
        organization_id=org_a.id,
        legal_name="Supplier Alpha",
        category="raw_materials",
        tier="tier_1",
    )
    dt_a = DocumentType(
        organization_id=org_a.id,
        code="gst_certificate",
        name="GST Certificate",
        category="statutory",
    )
    db_session.add_all([sup_a, dt_a])
    await db_session.flush()

    # Create group in Tenant A
    grp_a = SupplierDocumentGroup(
        organization_id=org_a.id,
        supplier_id=sup_a.id,
        document_type_id=dt_a.id,
        coverage_scope="supplier",
        display_name="GST Certificate",
    )
    db_session.add(grp_a)
    await db_session.flush()

    # Create document in Tenant A directly (compatible with Phase 3.4+ schema)
    doc_a = SupplierDocument(
        organization_id=org_a.id,
        supplier_id=sup_a.id,
        document_type_id=dt_a.id,
        document_group_id=grp_a.id,
        version=1,
        processing_status="uploaded",
        file_path="storage://tenanta/group/doc1.pdf",
        file_name="doc1.pdf",
        file_size_bytes=2048,
        file_mime_type="application/pdf",
        file_hash_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        is_effective_version=True,
    )
    db_session.add(doc_a)
    await db_session.flush()

    # 2. Insert 3 reviews with explicitly controlled timestamps and deterministic tie-breaker
    t_fixed = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)
    id1 = uuid.uuid4()
    id2 = uuid.uuid4()
    id3 = uuid.uuid4()
    # Sort UUIDs so we know the exact id descending order
    sorted_ids = sorted([id1, id2, id3], reverse=True)

    r1 = DocumentReview(
        id=sorted_ids[2],
        organization_id=org_a.id,
        document_id=doc_a.id,
        reviewer_id=user_a.id,
        decision="rejected",
        rejection_reason_code="illegible",
        comments="First review",
        created_at=t_fixed,
    )
    r2 = DocumentReview(
        id=sorted_ids[1],
        organization_id=org_a.id,
        document_id=doc_a.id,
        reviewer_id=user_a.id,
        decision="revision_requested",
        rejection_reason_code="incomplete_pages",
        comments="Second review (same timestamp)",
        created_at=t_fixed,
    )
    r3 = DocumentReview(
        id=sorted_ids[0],
        organization_id=org_a.id,
        document_id=doc_a.id,
        reviewer_id=user_a.id,
        decision="approved",
        comments="Third review (same timestamp)",
        created_at=t_fixed,
    )

    await repo.create(r1)
    await repo.create(r2)
    await repo.create(r3)

    # 3. Test deterministic ordering: created_at DESC, id DESC
    items, total = await repo.list_by_document(doc_a.id, org_a.id, page=1, page_size=10)
    assert total == 3
    assert len(items) == 3
    assert [item.id for item in items] == sorted_ids

    # 4. Test pagination bounds
    page_1_items, _ = await repo.list_by_document(
        doc_a.id, org_a.id, page=1, page_size=2
    )
    page_2_items, _ = await repo.list_by_document(
        doc_a.id, org_a.id, page=2, page_size=2
    )
    assert len(page_1_items) == 2
    assert len(page_2_items) == 1
    assert page_1_items[0].id == sorted_ids[0]
    assert page_1_items[1].id == sorted_ids[1]
    assert page_2_items[0].id == sorted_ids[2]

    # 5. Test latest review retrieval
    latest = await repo.get_latest_for_document(doc_a.id, org_a.id)
    assert latest is not None
    assert latest.id == sorted_ids[0]

    # 6. Test Tenant Isolation: Tenant B querying Tenant A's document review returns nothing
    items_b, total_b = await repo.list_by_document(
        doc_a.id, org_b.id, page=1, page_size=10
    )
    assert total_b == 0
    assert len(items_b) == 0

    latest_b = await repo.get_latest_for_document(doc_a.id, org_b.id)
    assert latest_b is None

    get_b = await repo.get_by_id(sorted_ids[0], org_b.id)
    assert get_b is None
