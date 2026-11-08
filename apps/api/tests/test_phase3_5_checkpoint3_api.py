"""Comprehensive Route-Level API Integration Tests for Phase 3.5 Checkpoint 3.

ARCHITECTURE & SAFELY GUARANTEED INVARIANTS:
1. Zero database engine connections (app.dependency_overrides for get_db and get_organization_context).
2. Complete RBAC enforcement testing (viewer, reviewer, admin, owner).
3. Pre-execution authorization failure prevents service invocation.
4. Trusted tenant and actor context, client IP, and user-agent propagation.
5. Cross-supplier and cross-tenant ownership rejection (404 Not Found).
6. Strict PATCH semantics (omitted vs explicit null vs non-nullable rejection).
7. Pagination boundary enforcement before service execution.
8. Single effective document retrieval (200 OK vs 404 Not Found when none active).
9. Requirement link DELETE returns HTTP 204 No Content with zero body bytes.
10. OpenAPI schema audit verifying zero document creation or upload routes exist.
11. Document response serialization guarantees zero exposure of internal file_path.
12. Signature-aware mock verification ensuring route keyword arguments strictly match domain service signatures.
13. 100% endpoint route contract coverage across all 16 Checkpoint 3 API endpoints.
"""

import inspect
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException, status

from app.auth.context import OrganizationContext
from app.auth.dependencies import get_organization_context
from app.db.session import get_db
from app.main import app
from app.models.document_group_requirement_link import DocumentGroupRequirementLink
from app.models.document_review import DocumentReview
from app.models.supplier_document import SupplierDocument
from app.models.supplier_document_group import SupplierDocumentGroup
from app.models.supplier_site import SupplierSite
from app.services.supplier_document import SupplierDocumentDomainService
from app.services.supplier_document_group import SupplierDocumentGroupService
from app.services.supplier_site import SupplierSiteService

_OMITTED = object()


def create_signature_aware_async_mock(
    target_fn, return_value=_OMITTED, side_effect=None
) -> AsyncMock:
    """Create an AsyncMock that validates argument names and binding against target_fn.

    Strips the leading 'self' parameter so that calls made through instance dispatch
    (e.g., service.method(context=..., ...)) strictly reject invalid keyword arguments
    with a TypeError, while preserving full AsyncMock awaiting, assertions, and return values.
    """
    sig = inspect.signature(target_fn)
    params = list(sig.parameters.values())
    if params and params[0].name == "self":
        sig = sig.replace(parameters=params[1:])

    mock = AsyncMock()
    if return_value is not _OMITTED:
        mock.return_value = return_value

    orig_side_effect = side_effect

    async def validated_side_effect(*args, **kwargs):
        sig.bind(*args, **kwargs)
        if orig_side_effect is not None:
            if callable(orig_side_effect):
                res = orig_side_effect(*args, **kwargs)
                if inspect.isawaitable(res):
                    return await res
                return res
            raise orig_side_effect
        return mock.return_value

    mock.side_effect = validated_side_effect
    return mock


def create_mock_context(role: str = "admin") -> OrganizationContext:
    return OrganizationContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        role=role,
        user_email=f"{role}@example.com",
        user_full_name=f"{role.capitalize()} User",
    )


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    return session


@pytest.fixture
async def async_client(
    mock_session: AsyncMock,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    async def override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# =============================================================================
# 1. RBAC & Pre-Execution Authorization Tests
# =============================================================================


@pytest.mark.asyncio
async def test_viewer_role_blocked_from_site_create(
    async_client: httpx.AsyncClient,
):
    """Assert that a viewer attempting site mutation receives 403 and service is never called."""
    viewer_ctx = create_mock_context(role="viewer")
    app.dependency_overrides[get_organization_context] = lambda: viewer_ctx
    supplier_id = uuid.uuid4()

    mock_create = create_signature_aware_async_mock(SupplierSiteService.create_site)
    with patch(
        "app.services.supplier_site.SupplierSiteService.create_site",
        mock_create,
    ):
        response = await async_client.post(
            f"/api/v1/suppliers/{supplier_id}/sites",
            json={
                "site_code": "SITE-01",
                "site_name": "Primary Site",
                "country_code": "IN",
            },
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Insufficient role permissions" in response.json()["detail"]
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_reviewer_role_blocked_from_revocation(
    async_client: httpx.AsyncClient,
):
    """Assert that a reviewer is forbidden from revoking an effective document (owner/admin only)."""
    reviewer_ctx = create_mock_context(role="reviewer")
    app.dependency_overrides[get_organization_context] = lambda: reviewer_ctx
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()

    mock_revoke = create_signature_aware_async_mock(
        SupplierDocumentDomainService.revoke_effective_version
    )
    with patch(
        "app.services.supplier_document.SupplierDocumentDomainService.revoke_effective_version",
        mock_revoke,
    ):
        response = await async_client.post(
            f"/api/v1/suppliers/{supplier_id}/document-groups/{group_id}/revoke-effective",
            json={"reason": "Revoking statutory certificate"},
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Insufficient role permissions" in response.json()["detail"]
        mock_revoke.assert_not_called()


@pytest.mark.asyncio
async def test_reviewer_role_permitted_to_record_review(
    async_client: httpx.AsyncClient,
):
    """Assert that a reviewer is authorized to submit review decisions (approved/rejected)."""
    reviewer_ctx = create_mock_context(role="reviewer")
    app.dependency_overrides[get_organization_context] = lambda: reviewer_ctx
    supplier_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    now = datetime.now(UTC)
    mock_doc = SupplierDocument(
        id=doc_id,
        organization_id=reviewer_ctx.organization_id,
        supplier_id=supplier_id,
        document_type_id=uuid.uuid4(),
        document_group_id=uuid.uuid4(),
        version=1,
        previous_version_id=None,
        is_effective_version=True,
        processing_status="completed",
        file_path="/safe/internal/path.pdf",
        file_name="tax_doc.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="a" * 64,
        created_at=now,
        updated_at=now,
    )

    mock_record = create_signature_aware_async_mock(
        SupplierDocumentDomainService.record_review_decision, return_value=mock_doc
    )
    with patch(
        "app.services.supplier_document.SupplierDocumentDomainService.record_review_decision",
        mock_record,
    ):
        response = await async_client.post(
            f"/api/v1/suppliers/{supplier_id}/documents/{doc_id}/reviews",
            json={
                "decision": "approved",
                "comments": "Verified and approved",
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        mock_record.assert_awaited_once()
        data = response.json()
        assert data["id"] == str(doc_id)
        assert data["is_effective_version"] is True
        assert "file_path" not in data


# =============================================================================
# 2. Context, Metadata Propagation, and Ownership Tests
# =============================================================================


@pytest.mark.asyncio
async def test_create_site_propagates_trusted_context_and_meta(
    async_client: httpx.AsyncClient,
):
    """Assert that trusted OrganizationContext, URL supplier_id, client IP, and User-Agent reach service."""
    admin_ctx = create_mock_context(role="admin")
    app.dependency_overrides[get_organization_context] = lambda: admin_ctx
    supplier_id = uuid.uuid4()
    site_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_site = SupplierSite(
        id=site_id,
        organization_id=admin_ctx.organization_id,
        supplier_id=supplier_id,
        site_code="SITE-HQ",
        site_name="Corporate HQ",
        address_line1="100 Main St",
        city="Mumbai",
        state="Maharashtra",
        postal_code="400001",
        country_code="IN",
        gstin="27AAPFU0939F1ZV",
        is_active=True,
        created_at=now,
        updated_at=now,
    )

    mock_create = create_signature_aware_async_mock(
        SupplierSiteService.create_site, return_value=mock_site
    )
    with patch(
        "app.services.supplier_site.SupplierSiteService.create_site",
        mock_create,
    ):
        response = await async_client.post(
            f"/api/v1/suppliers/{supplier_id}/sites",
            headers={"User-Agent": "VendorFlow-TestRunner/1.0"},
            json={
                "site_code": "SITE-HQ",
                "site_name": "Corporate HQ",
                "address_line1": "100 Main St",
                "city": "Mumbai",
                "state": "Maharashtra",
                "postal_code": "400001",
                "country_code": "IN",
                "gstin": "27AAPFU0939F1ZV",
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        mock_create.assert_awaited_once()
        _, kwargs = mock_create.call_args
        assert kwargs["context"].user_id == admin_ctx.user_id
        assert kwargs["context"].organization_id == admin_ctx.organization_id
        assert kwargs["supplier_id"] == supplier_id
        assert kwargs["user_agent"] == "VendorFlow-TestRunner/1.0"
        assert response.json()["id"] == str(site_id)


@pytest.mark.asyncio
async def test_cross_supplier_ownership_rejection_propagates_404(
    async_client: httpx.AsyncClient,
):
    """Assert that accessing a resource belonging to a different supplier returns 404."""
    admin_ctx = create_mock_context(role="admin")
    app.dependency_overrides[get_organization_context] = lambda: admin_ctx
    supplier_id = uuid.uuid4()
    site_id = uuid.uuid4()

    mock_get_site = create_signature_aware_async_mock(
        SupplierSiteService.get_site,
        side_effect=HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Site {site_id} not found for supplier {supplier_id}.",
        ),
    )
    with patch(
        "app.services.supplier_site.SupplierSiteService.get_site",
        mock_get_site,
    ):
        response = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/sites/{site_id}"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert (
            f"Site {site_id} not found for supplier {supplier_id}"
            in response.json()["detail"]
        )


# =============================================================================
# 3. Strict PATCH Semantics & Validation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_patch_site_omitted_vs_explicit_null(
    async_client: httpx.AsyncClient,
):
    """Assert PATCH passes model_fields_set with cleared fields and rejects null on non-nullable."""
    admin_ctx = create_mock_context(role="admin")
    app.dependency_overrides[get_organization_context] = lambda: admin_ctx
    supplier_id = uuid.uuid4()
    site_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_site = SupplierSite(
        id=site_id,
        organization_id=admin_ctx.organization_id,
        supplier_id=supplier_id,
        site_code="SITE-HQ",
        site_name="Updated Name",
        address_line1=None,  # cleared
        city="Mumbai",
        state="Maharashtra",
        postal_code="400001",
        country_code="IN",
        is_active=True,
        created_at=now,
        updated_at=now,
    )

    mock_update = create_signature_aware_async_mock(
        SupplierSiteService.update_site, return_value=mock_site
    )
    with patch(
        "app.services.supplier_site.SupplierSiteService.update_site",
        mock_update,
    ):
        response = await async_client.patch(
            f"/api/v1/suppliers/{supplier_id}/sites/{site_id}",
            json={
                "site_name": "Updated Name",
                "address_line1": None,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        mock_update.assert_awaited_once()
        patch_data = mock_update.call_args[1]["data"]
        assert "site_name" in patch_data.model_fields_set
        assert "address_line1" in patch_data.model_fields_set
        assert "city" not in patch_data.model_fields_set

    # Non-nullable site_name set to null -> 422
    response_invalid = await async_client.patch(
        f"/api/v1/suppliers/{supplier_id}/sites/{site_id}",
        json={"site_name": None},
    )
    assert response_invalid.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# =============================================================================
# 4. Group Listing Filters & Pagination Boundary Tests
# =============================================================================


@pytest.mark.asyncio
async def test_list_groups_filter_propagation(
    async_client: httpx.AsyncClient,
):
    """Assert document_type_id, site_id, and pagination reach list_groups cleanly."""
    viewer_ctx = create_mock_context(role="viewer")
    app.dependency_overrides[get_organization_context] = lambda: viewer_ctx
    supplier_id = uuid.uuid4()
    doc_type_id = uuid.uuid4()
    site_id = uuid.uuid4()
    group_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_group = SupplierDocumentGroup(
        id=group_id,
        organization_id=viewer_ctx.organization_id,
        supplier_id=supplier_id,
        document_type_id=doc_type_id,
        site_id=site_id,
        coverage_scope="site",
        display_name="Site GST Certificate",
        created_at=now,
        updated_at=now,
    )

    mock_list = create_signature_aware_async_mock(
        SupplierDocumentGroupService.list_groups, return_value=([mock_group], 1)
    )
    with patch(
        "app.services.supplier_document_group.SupplierDocumentGroupService.list_groups",
        mock_list,
    ):
        response = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/document-groups",
            params={
                "document_type_id": str(doc_type_id),
                "site_id": str(site_id),
                "page": 2,
                "page_size": 10,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        mock_list.assert_awaited_once_with(
            context=viewer_ctx,
            supplier_id=supplier_id,
            document_type_id=doc_type_id,
            site_id=site_id,
            page=2,
            page_size=10,
        )
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 2
        assert len(data["items"]) == 1


@pytest.mark.asyncio
async def test_list_groups_pagination_bounds_validation(
    async_client: httpx.AsyncClient,
):
    """Assert page=0 or page_size>100 is rejected with 422 before service invocation."""
    viewer_ctx = create_mock_context(role="viewer")
    app.dependency_overrides[get_organization_context] = lambda: viewer_ctx
    supplier_id = uuid.uuid4()

    mock_list = create_signature_aware_async_mock(
        SupplierDocumentGroupService.list_groups
    )
    with patch(
        "app.services.supplier_document_group.SupplierDocumentGroupService.list_groups",
        mock_list,
    ):
        # 1. page=0 rejected
        res1 = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/document-groups?page=0"
        )
        assert res1.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # 2. page_size=101 rejected
        res2 = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/document-groups?page_size=101"
        )
        assert res2.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        mock_list.assert_not_called()


# =============================================================================
# 5. Effective Document Retrieval & 404 Mapping Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_effective_document_returns_404_when_none(
    async_client: httpx.AsyncClient,
):
    """Assert retrieving effective document returns 404 with clear message when none active."""
    viewer_ctx = create_mock_context(role="viewer")
    app.dependency_overrides[get_organization_context] = lambda: viewer_ctx
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()

    mock_get_eff = create_signature_aware_async_mock(
        SupplierDocumentDomainService.get_effective_version, return_value=None
    )
    with patch(
        "app.services.supplier_document.SupplierDocumentDomainService.get_effective_version",
        mock_get_eff,
    ):
        response = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/document-groups/{group_id}/effective-document"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert (
            f"No effective document version found in document group {group_id}"
            in response.json()["detail"]
        )
        mock_get_eff.assert_awaited_once()
        _, kwargs = mock_get_eff.call_args
        assert "document_group_id" in kwargs
        assert "group_id" not in kwargs
        assert kwargs["document_group_id"] == group_id


# =============================================================================
# 6. DELETE 204 Empty Body and OpenAPI Safety Audits
# =============================================================================


@pytest.mark.asyncio
async def test_delete_requirement_link_returns_empty_204(
    async_client: httpx.AsyncClient,
):
    """Assert requirement link removal returns 204 with exactly 0 body bytes."""
    admin_ctx = create_mock_context(role="admin")
    app.dependency_overrides[get_organization_context] = lambda: admin_ctx
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    req_id = uuid.uuid4()

    mock_remove = create_signature_aware_async_mock(
        SupplierDocumentGroupService.remove_requirement_link, return_value=None
    )
    with patch(
        "app.services.supplier_document_group.SupplierDocumentGroupService.remove_requirement_link",
        mock_remove,
    ):
        response = await async_client.delete(
            f"/api/v1/suppliers/{supplier_id}/document-groups/{group_id}/requirements/{req_id}"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.content == b""
        mock_remove.assert_awaited_once()


@pytest.mark.asyncio
async def test_document_responses_strictly_omit_file_path(
    async_client: httpx.AsyncClient,
):
    """Assert serialized document endpoints never expose internal file_path."""
    viewer_ctx = create_mock_context(role="viewer")
    app.dependency_overrides[get_organization_context] = lambda: viewer_ctx
    supplier_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_doc = SupplierDocument(
        id=doc_id,
        organization_id=viewer_ctx.organization_id,
        supplier_id=supplier_id,
        document_type_id=uuid.uuid4(),
        document_group_id=uuid.uuid4(),
        version=1,
        previous_version_id=None,
        is_effective_version=True,
        processing_status="completed",
        file_path="/secrets/storage/tenant/sensitive_path.pdf",
        file_name="tax.pdf",
        file_size_bytes=2048,
        file_mime_type="application/pdf",
        file_hash_sha256="b" * 64,
        issued_date=date(2026, 1, 1),
        created_at=now,
        updated_at=now,
    )

    mock_get_doc = create_signature_aware_async_mock(
        SupplierDocumentDomainService.get_document, return_value=mock_doc
    )
    with patch(
        "app.services.supplier_document.SupplierDocumentDomainService.get_document",
        mock_get_doc,
    ):
        response = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/documents/{doc_id}"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "file_path" not in data
        assert data["file_name"] == "tax.pdf"
        assert data["file_hash_sha256"] == "b" * 64


def test_openapi_schema_strictly_omits_document_upload_and_file_path():
    """Forensic OpenAPI audit: verify no document creation/upload route exists in OpenAPI definition."""
    schema = app.openapi()
    paths = schema.get("paths", {})

    # Ensure no POST route exists for /documents
    document_post_routes = [
        path for path in paths if path.endswith("/documents") and "post" in paths[path]
    ]
    assert document_post_routes == [], (
        f"Forbidden document upload route found: {document_post_routes}"
    )

    # Verify SupplierDocumentResponse in components schemas omits file_path
    schemas = schema.get("components", {}).get("schemas", {})
    doc_schema = schemas.get("SupplierDocumentResponse", {})
    properties = doc_schema.get("properties", {})
    assert "file_path" not in properties


@pytest.mark.asyncio
async def test_review_history_deterministic_pagination(
    async_client: httpx.AsyncClient,
):
    """Assert review history listing endpoint returns paginated items with deterministic pagination metadata."""
    viewer_ctx = create_mock_context(role="viewer")
    app.dependency_overrides[get_organization_context] = lambda: viewer_ctx
    supplier_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_review = DocumentReview(
        id=uuid.uuid4(),
        organization_id=viewer_ctx.organization_id,
        document_id=doc_id,
        reviewer_id=uuid.uuid4(),
        decision="approved",
        comments="Approved statutory filing",
        rejection_reason_code=None,
        verified_data={"cert_number": "GST123"},
        created_at=now,
    )

    mock_list_rev = create_signature_aware_async_mock(
        SupplierDocumentDomainService.list_reviews_for_document,
        return_value=([mock_review], 1),
    )
    with patch(
        "app.services.supplier_document.SupplierDocumentDomainService.list_reviews_for_document",
        mock_list_rev,
    ):
        response = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/documents/{doc_id}/reviews?page=1&page_size=20"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["decision"] == "approved"
        assert data["items"][0]["verified_data"] == {"cert_number": "GST123"}


# =============================================================================
# 7. Six Missing Endpoints Contract Coverage (R-03 Remediation)
# =============================================================================


@pytest.mark.asyncio
async def test_list_supplier_sites_endpoint(
    async_client: httpx.AsyncClient,
):
    """Assert GET /suppliers/{id}/sites invokes list_sites with trusted context and filters."""
    viewer_ctx = create_mock_context(role="viewer")
    app.dependency_overrides[get_organization_context] = lambda: viewer_ctx
    supplier_id = uuid.uuid4()
    site_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_site = SupplierSite(
        id=site_id,
        organization_id=viewer_ctx.organization_id,
        supplier_id=supplier_id,
        site_code="SITE-PUNE",
        site_name="Pune Assembly Plant",
        country_code="IN",
        is_active=True,
        created_at=now,
        updated_at=now,
    )

    mock_list = create_signature_aware_async_mock(
        SupplierSiteService.list_sites, return_value=[mock_site]
    )
    with patch("app.services.supplier_site.SupplierSiteService.list_sites", mock_list):
        response = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/sites?is_active=true"
        )
        assert response.status_code == status.HTTP_200_OK
        mock_list.assert_awaited_once()
        _, kwargs = mock_list.call_args
        assert kwargs["context"].organization_id == viewer_ctx.organization_id
        assert kwargs["supplier_id"] == supplier_id
        assert kwargs["is_active"] is True
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == str(site_id)
        assert data["items"][0]["site_code"] == "SITE-PUNE"


@pytest.mark.asyncio
async def test_create_supplier_document_group_endpoint(
    async_client: httpx.AsyncClient,
):
    """Assert POST .../document-groups enforces trusted context, propagates metadata, and serializes response."""
    admin_ctx = create_mock_context(role="admin")
    app.dependency_overrides[get_organization_context] = lambda: admin_ctx
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    doc_type_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_group = SupplierDocumentGroup(
        id=group_id,
        organization_id=admin_ctx.organization_id,
        supplier_id=supplier_id,
        document_type_id=doc_type_id,
        coverage_scope="supplier",
        site_id=None,
        document_reference="REF-2026-TAX",
        display_name="GST Certificate",
        issuing_authority="Govt of India",
        created_at=now,
        updated_at=now,
    )

    mock_create = create_signature_aware_async_mock(
        SupplierDocumentGroupService.create_group, return_value=mock_group
    )
    with patch(
        "app.services.supplier_document_group.SupplierDocumentGroupService.create_group",
        mock_create,
    ):
        response = await async_client.post(
            f"/api/v1/suppliers/{supplier_id}/document-groups",
            headers={"User-Agent": "TestClient/2.0"},
            json={
                "document_type_id": str(doc_type_id),
                "coverage_scope": "supplier",
                "document_reference": "REF-2026-TAX",
                "display_name": "GST Certificate",
                "issuing_authority": "Govt of India",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        mock_create.assert_awaited_once()
        _, kwargs = mock_create.call_args
        assert kwargs["context"].organization_id == admin_ctx.organization_id
        assert kwargs["supplier_id"] == supplier_id
        assert kwargs["document_type_id"] == doc_type_id
        assert kwargs["user_agent"] == "TestClient/2.0"
        data = response.json()
        assert data["id"] == str(group_id)
        assert data["coverage_scope"] == "supplier"
        assert data["document_reference"] == "REF-2026-TAX"


@pytest.mark.asyncio
async def test_get_supplier_document_group_endpoint(
    async_client: httpx.AsyncClient,
):
    """Assert GET .../document-groups/{group_id} invokes get_group with context, supplier_id, and group_id."""
    viewer_ctx = create_mock_context(role="viewer")
    app.dependency_overrides[get_organization_context] = lambda: viewer_ctx
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_group = SupplierDocumentGroup(
        id=group_id,
        organization_id=viewer_ctx.organization_id,
        supplier_id=supplier_id,
        document_type_id=uuid.uuid4(),
        coverage_scope="supplier",
        display_name="ISO 9001 Certificate",
        created_at=now,
        updated_at=now,
    )

    mock_get = create_signature_aware_async_mock(
        SupplierDocumentGroupService.get_group, return_value=mock_group
    )
    with patch(
        "app.services.supplier_document_group.SupplierDocumentGroupService.get_group",
        mock_get,
    ):
        response = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/document-groups/{group_id}"
        )
        assert response.status_code == status.HTTP_200_OK
        mock_get.assert_awaited_once()
        _, kwargs = mock_get.call_args
        assert kwargs["context"].organization_id == viewer_ctx.organization_id
        assert kwargs["supplier_id"] == supplier_id
        assert kwargs["group_id"] == group_id
        data = response.json()
        assert data["id"] == str(group_id)
        assert data["display_name"] == "ISO 9001 Certificate"


@pytest.mark.asyncio
async def test_list_group_requirement_links_endpoint(
    async_client: httpx.AsyncClient,
):
    """Assert GET .../requirements invokes list_requirement_links and serializes items."""
    viewer_ctx = create_mock_context(role="viewer")
    app.dependency_overrides[get_organization_context] = lambda: viewer_ctx
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    link_id = uuid.uuid4()
    req_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_link = DocumentGroupRequirementLink(
        id=link_id,
        organization_id=viewer_ctx.organization_id,
        document_group_id=group_id,
        requirement_id=req_id,
        link_origin="manual",
        linked_at=now,
    )

    mock_list_links = create_signature_aware_async_mock(
        SupplierDocumentGroupService.list_requirement_links,
        return_value=[mock_link],
    )
    with patch(
        "app.services.supplier_document_group.SupplierDocumentGroupService.list_requirement_links",
        mock_list_links,
    ):
        response = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/document-groups/{group_id}/requirements"
        )
        assert response.status_code == status.HTTP_200_OK
        mock_list_links.assert_awaited_once()
        _, kwargs = mock_list_links.call_args
        assert kwargs["context"].organization_id == viewer_ctx.organization_id
        assert kwargs["supplier_id"] == supplier_id
        assert kwargs["group_id"] == group_id
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == str(link_id)
        assert data["items"][0]["requirement_id"] == str(req_id)


@pytest.mark.asyncio
async def test_add_group_requirement_link_endpoint(
    async_client: httpx.AsyncClient,
):
    """Assert POST .../requirements associates requirement with trusted context and metadata."""
    admin_ctx = create_mock_context(role="admin")
    app.dependency_overrides[get_organization_context] = lambda: admin_ctx
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    link_id = uuid.uuid4()
    req_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_link = DocumentGroupRequirementLink(
        id=link_id,
        organization_id=admin_ctx.organization_id,
        document_group_id=group_id,
        requirement_id=req_id,
        link_origin="manual",
        linked_at=now,
    )

    mock_add = create_signature_aware_async_mock(
        SupplierDocumentGroupService.add_requirement_link, return_value=mock_link
    )
    with patch(
        "app.services.supplier_document_group.SupplierDocumentGroupService.add_requirement_link",
        mock_add,
    ):
        response = await async_client.post(
            f"/api/v1/suppliers/{supplier_id}/document-groups/{group_id}/requirements",
            headers={"User-Agent": "ComplianceRunner/1.0"},
            json={
                "requirement_id": str(req_id),
                "link_origin": "manual",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        mock_add.assert_awaited_once()
        _, kwargs = mock_add.call_args
        assert kwargs["context"].organization_id == admin_ctx.organization_id
        assert kwargs["supplier_id"] == supplier_id
        assert kwargs["group_id"] == group_id
        assert kwargs["requirement_id"] == req_id
        assert kwargs["link_origin"] == "manual"
        assert kwargs["user_agent"] == "ComplianceRunner/1.0"
        data = response.json()
        assert data["id"] == str(link_id)
        assert data["requirement_id"] == str(req_id)


@pytest.mark.asyncio
async def test_list_group_document_versions_endpoint(
    async_client: httpx.AsyncClient,
):
    """Assert GET .../documents invokes list_documents_in_group with document_group_id kwarg."""
    viewer_ctx = create_mock_context(role="viewer")
    app.dependency_overrides[get_organization_context] = lambda: viewer_ctx
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_doc = SupplierDocument(
        id=doc_id,
        organization_id=viewer_ctx.organization_id,
        supplier_id=supplier_id,
        document_type_id=uuid.uuid4(),
        document_group_id=group_id,
        version=1,
        previous_version_id=None,
        is_effective_version=True,
        processing_status="completed",
        file_path="/safe/path.pdf",
        file_name="cert_v1.pdf",
        file_size_bytes=1024,
        file_mime_type="application/pdf",
        file_hash_sha256="c" * 64,
        created_at=now,
        updated_at=now,
    )

    mock_list_docs = create_signature_aware_async_mock(
        SupplierDocumentDomainService.list_documents_in_group,
        return_value=[mock_doc],
    )
    with patch(
        "app.services.supplier_document.SupplierDocumentDomainService.list_documents_in_group",
        mock_list_docs,
    ):
        response = await async_client.get(
            f"/api/v1/suppliers/{supplier_id}/document-groups/{group_id}/documents"
        )
        assert response.status_code == status.HTTP_200_OK
        mock_list_docs.assert_awaited_once()
        _, kwargs = mock_list_docs.call_args
        # Critical assertion verifying R-01 fix:
        assert kwargs["context"].organization_id == viewer_ctx.organization_id
        assert kwargs["supplier_id"] == supplier_id
        assert "document_group_id" in kwargs
        assert "group_id" not in kwargs
        assert kwargs["document_group_id"] == group_id
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == str(doc_id)
        assert data[0]["file_name"] == "cert_v1.pdf"
        assert "file_path" not in data[0]


# =============================================================================
# 8. Focused Regression Tests (R-01 & R-02 Verification)
# =============================================================================


@pytest.mark.asyncio
async def test_regression_signature_validation_catches_kwarg_mismatch():
    """Regression test proving signature validation catches group_id vs document_group_id mismatch.

    1. Invoking the signature-aware mock with invalid kwarg 'group_id' must raise TypeError.
    2. Invoking with the corrected kwarg 'document_group_id' must succeed and bind cleanly.
    3. Explicit return_value=None returns None (does not default to AsyncMock).
    4. Omitted return_value defaults to an AsyncMock.
    """
    mock_ctx = create_mock_context(role="viewer")
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()

    # 1. Invalid kwarg raises TypeError:
    mock_eff = create_signature_aware_async_mock(
        SupplierDocumentDomainService.get_effective_version, return_value=None
    )
    with pytest.raises(TypeError) as exc_info:
        await mock_eff(
            context=mock_ctx,
            supplier_id=supplier_id,
            group_id=group_id,  # Original defective kwarg
        )
    assert "unexpected keyword argument 'group_id'" in str(
        exc_info.value
    ) or "missing a required argument: 'document_group_id'" in str(exc_info.value)

    # 2. Correct kwarg succeeds with explicit return_value=None:
    res = await mock_eff(
        context=mock_ctx,
        supplier_id=supplier_id,
        document_group_id=group_id,  # Corrected kwarg
    )
    assert res is None

    # 3. Omitted return_value returns default AsyncMock:
    mock_omitted = create_signature_aware_async_mock(
        SupplierDocumentDomainService.get_effective_version
    )
    res_omitted = await mock_omitted(
        context=mock_ctx,
        supplier_id=supplier_id,
        document_group_id=group_id,
    )
    assert isinstance(res_omitted, AsyncMock)
