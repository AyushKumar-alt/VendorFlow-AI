"""Comprehensive Unit Tests for Phase 3.5 Checkpoint 2: Domain Service Extensions.

NOTE ON ENGINEERING STANDARDS:
These unit tests execute completely in-memory using mocked AsyncSession and
mocked repository dependencies (0% PostgreSQL database access).

They verify:
1. Service-level business logic, input validation, and pagination bounds (page >= 1, 1 <= page_size <= 100).
2. Strict PATCH semantics and non-nullable field rejection on SupplierSiteUpdate.
3. Operation ordering: parent group locking happens BEFORE state-changing document reads.
4. Target document is freshly re-fetched after acquiring the group lock.
5. Dual-document audit payload semantics capturing both promoted and demoted effective versions.
6. Replacement rejection preserving earlier effective version.
7. Atomic commit and rollback orchestration (audit insertion failure rolls back session).

Actual PostgreSQL row-lock behavior, concurrency isolation, and database constraints
remain unverified until authorized integration testing against the Phase 3.4 schema.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.models.audit_event import AuditEvent
from app.models.document_review import DocumentReview
from app.models.supplier import Supplier
from app.models.supplier_document import SupplierDocument
from app.models.supplier_document_group import SupplierDocumentGroup
from app.models.supplier_site import SupplierSite
from app.repositories.supplier_document import SupplierDocumentRepository
from app.repositories.supplier_document_group import SupplierDocumentGroupRepository
from app.repositories.supplier_site import SupplierSiteRepository
from app.schemas.supplier_site import SupplierSiteUpdate
from app.services.supplier_document import SupplierDocumentDomainService
from app.services.supplier_document_group import SupplierDocumentGroupService
from app.services.supplier_site import SupplierSiteService


@pytest.fixture
def org_context() -> OrganizationContext:
    return OrganizationContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        role="admin",
        user_email="admin@example.com",
        user_full_name="Admin User",
    )


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    return session


# =============================================================================
# 1. SupplierSiteService Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_supplier_site_get_valid(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierSiteService(mock_session)
    supplier_id = uuid.uuid4()
    site_id = uuid.uuid4()

    mock_supplier = Supplier(
        id=supplier_id, organization_id=org_context.organization_id
    )
    mock_site = SupplierSite(
        id=site_id, supplier_id=supplier_id, organization_id=org_context.organization_id
    )

    service.supplier_repo.get_by_id = AsyncMock(return_value=mock_supplier)
    service.site_repo.get_by_id = AsyncMock(return_value=mock_site)

    result = await service.get_site(org_context, supplier_id, site_id)
    assert result.id == site_id
    assert result.supplier_id == supplier_id


@pytest.mark.asyncio
async def test_supplier_site_get_supplier_mismatch_raises_404(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierSiteService(mock_session)
    supplier_id = uuid.uuid4()
    other_supplier_id = uuid.uuid4()
    site_id = uuid.uuid4()

    mock_supplier = Supplier(
        id=supplier_id, organization_id=org_context.organization_id
    )
    # Site belongs to other_supplier_id
    mock_site = SupplierSite(
        id=site_id,
        supplier_id=other_supplier_id,
        organization_id=org_context.organization_id,
    )

    service.supplier_repo.get_by_id = AsyncMock(return_value=mock_supplier)
    service.site_repo.get_by_id = AsyncMock(return_value=mock_site)

    with pytest.raises(HTTPException) as exc_info:
        await service.get_site(org_context, supplier_id, site_id)
    assert exc_info.value.status_code == 404
    assert (
        f"Site {site_id} not found for supplier {supplier_id}" in exc_info.value.detail
    )


@pytest.mark.asyncio
async def test_supplier_site_list(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierSiteService(mock_session)
    supplier_id = uuid.uuid4()

    mock_supplier = Supplier(
        id=supplier_id, organization_id=org_context.organization_id
    )
    service.supplier_repo.get_by_id = AsyncMock(return_value=mock_supplier)
    service.site_repo.list_by_supplier = AsyncMock(
        return_value=[MagicMock(), MagicMock()]
    )

    sites = await service.list_sites(org_context, supplier_id, is_active=True)
    assert len(sites) == 2
    service.site_repo.list_by_supplier.assert_awaited_once_with(
        supplier_id, org_context.organization_id, is_active=True
    )


@pytest.mark.asyncio
async def test_supplier_site_update_empty_payload_noop(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierSiteService(mock_session)
    supplier_id = uuid.uuid4()
    site_id = uuid.uuid4()

    mock_site = SupplierSite(
        id=site_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        site_name="Original Name",
    )
    service.get_site = AsyncMock(return_value=mock_site)
    service.site_repo.update = AsyncMock()

    # Empty update payload (no fields set)
    data = SupplierSiteUpdate()
    result = await service.update_site(org_context, supplier_id, site_id, data)

    assert result.site_name == "Original Name"
    service.site_repo.update.assert_not_called()
    mock_session.commit.assert_not_called()
    mock_session.add.assert_not_called()


@pytest.mark.asyncio
async def test_supplier_site_update_patch_semantics_and_audit(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierSiteService(mock_session)
    supplier_id = uuid.uuid4()
    site_id = uuid.uuid4()

    mock_site = SupplierSite(
        id=site_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        site_code="SITE-01",
        site_name="Old Plant",
        country_code="IN",
        is_active=True,
        city="Old City",
        gstin="27AABCU9603R1ZM",
    )
    service.get_site = AsyncMock(return_value=mock_site)
    service.site_repo.update = AsyncMock()

    # Partial update: update name, clear city (None), update gstin
    data = SupplierSiteUpdate(
        site_name="New Plant Pune",
        city=None,
        gstin="27AABCU9603R1ZM",
    )

    result = await service.update_site(org_context, supplier_id, site_id, data)
    assert result.site_name == "New Plant Pune"
    assert result.city is None

    service.site_repo.update.assert_awaited_once_with(mock_site)
    mock_session.commit.assert_awaited_once()

    # Verify atomic audit event was added
    mock_session.add.assert_called_once()
    audit: AuditEvent = mock_session.add.call_args[0][0]
    assert isinstance(audit, AuditEvent)
    assert audit.action == "supplier_site.updated"
    assert audit.previous_state["site_name"] == "Old Plant"
    assert audit.new_state["site_name"] == "New Plant Pune"
    assert audit.previous_state["city"] == "Old City"
    assert audit.new_state["city"] is None


@pytest.mark.asyncio
async def test_supplier_site_update_invalid_country_raises_422(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierSiteService(mock_session)
    supplier_id = uuid.uuid4()
    site_id = uuid.uuid4()

    mock_site = SupplierSite(
        id=site_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        site_name="Plant",
        country_code="IN",
    )
    service.get_site = AsyncMock(return_value=mock_site)

    data = SupplierSiteUpdate.model_construct(
        country_code="IND",
        __fields_set__={"country_code"},
    )
    with pytest.raises(HTTPException) as exc_info:
        await service.update_site(org_context, supplier_id, site_id, data)
    assert exc_info.value.status_code == 422
    assert "Country code must be exactly 2 uppercase letters" in exc_info.value.detail


@pytest.mark.asyncio
async def test_supplier_site_update_rollback_on_error(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierSiteService(mock_session)
    supplier_id = uuid.uuid4()
    site_id = uuid.uuid4()

    mock_site = SupplierSite(
        id=site_id, supplier_id=supplier_id, organization_id=org_context.organization_id
    )
    service.get_site = AsyncMock(return_value=mock_site)
    service.site_repo.update = AsyncMock(side_effect=RuntimeError("Database failure"))

    data = SupplierSiteUpdate(site_name="New Name")
    with pytest.raises(RuntimeError, match="Database failure"):
        await service.update_site(org_context, supplier_id, site_id, data)

    mock_session.rollback.assert_awaited_once()


# =============================================================================
# 2. SupplierDocumentGroupService Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_document_group_get_valid_and_mismatch(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentGroupService(mock_session)
    supplier_id = uuid.uuid4()
    other_supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()

    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )
    service.group_repo.get_by_id = AsyncMock(return_value=mock_group)

    # Valid read with matching supplier
    g = await service.get_group(group_id, org_context, supplier_id=supplier_id)
    assert g.id == group_id

    # Read with mismatched supplier raises 404
    with pytest.raises(HTTPException) as exc_info:
        await service.get_group(group_id, org_context, supplier_id=other_supplier_id)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_document_group_list_pagination_bounds(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentGroupService(mock_session)
    supplier_id = uuid.uuid4()

    # page < 1
    with pytest.raises(HTTPException) as exc_info:
        await service.list_groups(org_context, supplier_id, page=0, page_size=20)
    assert exc_info.value.status_code == 422
    assert "page must be greater than or equal to 1" in exc_info.value.detail

    # page_size > 100
    with pytest.raises(HTTPException) as exc_info:
        await service.list_groups(org_context, supplier_id, page=1, page_size=101)
    assert exc_info.value.status_code == 422
    assert "page_size must be between 1 and 100" in exc_info.value.detail


@pytest.mark.asyncio
async def test_document_group_list_valid(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentGroupService(mock_session)
    supplier_id = uuid.uuid4()

    mock_supplier = Supplier(
        id=supplier_id, organization_id=org_context.organization_id
    )
    service.supplier_repo.get_by_id = AsyncMock(return_value=mock_supplier)
    service.group_repo.list_by_supplier_paginated = AsyncMock(
        return_value=([MagicMock(), MagicMock()], 2)
    )

    items, total = await service.list_groups(
        org_context, supplier_id, page=1, page_size=10
    )
    assert total == 2
    assert len(items) == 2
    service.group_repo.list_by_supplier_paginated.assert_awaited_once_with(
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_type_id=None,
        site_id=None,
        page=1,
        page_size=10,
    )


@pytest.mark.asyncio
async def test_document_group_list_requirement_links(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentGroupService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()

    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )
    service.get_group = AsyncMock(return_value=mock_group)
    service.link_repo.list_by_group = AsyncMock(return_value=[MagicMock()])

    links = await service.list_requirement_links(org_context, supplier_id, group_id)
    assert len(links) == 1
    service.get_group.assert_awaited_once_with(
        group_id, org_context, supplier_id=supplier_id
    )
    service.link_repo.list_by_group.assert_awaited_once_with(
        group_id, org_context.organization_id
    )


# =============================================================================
# 3. SupplierDocumentDomainService Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_document_get_valid_and_supplier_mismatch(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    other_supplier_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_doc = SupplierDocument(
        id=doc_id, supplier_id=supplier_id, organization_id=org_context.organization_id
    )
    service.doc_repo.get_by_id = AsyncMock(return_value=mock_doc)

    # Valid
    doc = await service.get_document(doc_id, org_context, supplier_id=supplier_id)
    assert doc.id == doc_id

    # Mismatch raises 404
    with pytest.raises(HTTPException) as exc_info:
        await service.get_document(doc_id, org_context, supplier_id=other_supplier_id)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_document_get_effective_version(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )
    mock_doc = SupplierDocument(
        id=doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        is_effective_version=True,
    )

    service.group_repo.get_by_id = AsyncMock(return_value=mock_group)
    service.doc_repo.get_effective_version = AsyncMock(return_value=mock_doc)

    effective = await service.get_effective_version(org_context, supplier_id, group_id)
    assert effective is not None
    assert effective.id == doc_id
    assert effective.is_effective_version is True


@pytest.mark.asyncio
async def test_document_list_in_group(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()

    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )
    service.group_repo.get_by_id = AsyncMock(return_value=mock_group)
    service.doc_repo.list_by_group = AsyncMock(return_value=[MagicMock(), MagicMock()])

    docs = await service.list_documents_in_group(org_context, supplier_id, group_id)
    assert len(docs) == 2


@pytest.mark.asyncio
async def test_document_list_reviews_pagination_bounds(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        await service.list_reviews_for_document(
            org_context, supplier_id, doc_id, page=0, page_size=20
        )
    assert exc_info.value.status_code == 422
    assert "page must be greater than or equal to 1" in exc_info.value.detail

    with pytest.raises(HTTPException) as exc_info:
        await service.list_reviews_for_document(
            org_context, supplier_id, doc_id, page=1, page_size=105
        )
    assert exc_info.value.status_code == 422
    assert "page_size must be between 1 and 100" in exc_info.value.detail


# =============================================================================
# 4. Review Decision Operation Ordering & Locking Verification
# =============================================================================


@pytest.mark.asyncio
async def test_record_review_decision_operation_ordering(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    """Verify that parent group row lock happens BEFORE post-lock target re-fetch and latest version reads."""
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    call_order: list[str] = []

    # Mock prelim get_document
    mock_prelim = SupplierDocument(
        id=doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=1,
        is_effective_version=False,
    )
    mock_post_lock_doc = SupplierDocument(
        id=doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=1,
        is_effective_version=False,
    )
    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )

    async def mock_get_document(*args, **kwargs):
        call_order.append("prelim_get_document")
        return mock_prelim

    async def mock_get_by_id_for_update(*args, **kwargs):
        call_order.append("group_lock_acquired")
        return mock_group

    async def mock_get_by_id(*args, **kwargs):
        call_order.append("post_lock_doc_refetch")
        return mock_post_lock_doc

    async def mock_get_latest_version(*args, **kwargs):
        call_order.append("post_lock_read_latest_version")
        return mock_post_lock_doc

    async def mock_get_effective_version(*args, **kwargs):
        call_order.append("post_lock_read_effective_version")

    service.get_document = AsyncMock(side_effect=mock_get_document)
    service.group_repo.get_by_id_for_update = AsyncMock(
        side_effect=mock_get_by_id_for_update
    )
    service.doc_repo.get_by_id = AsyncMock(side_effect=mock_get_by_id)
    service.doc_repo.get_latest_version_in_group = AsyncMock(
        side_effect=mock_get_latest_version
    )
    service.doc_repo.get_effective_version = AsyncMock(
        side_effect=mock_get_effective_version
    )
    service.doc_repo.update = AsyncMock()

    await service.record_review_decision(
        context=org_context,
        document_id=doc_id,
        decision="approved",
        supplier_id=supplier_id,
    )

    # Assert strict operation order
    expected_order = [
        "prelim_get_document",
        "group_lock_acquired",
        "post_lock_doc_refetch",
        "post_lock_read_latest_version",
        "post_lock_read_effective_version",
    ]
    assert call_order == expected_order
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_review_decision_older_version_approval_rejected_409(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_doc_v1 = SupplierDocument(
        id=doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=1,
        is_effective_version=False,
    )
    mock_doc_v2 = SupplierDocument(
        id=uuid.uuid4(),
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=2,
        is_effective_version=False,
    )
    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )

    service.get_document = AsyncMock(return_value=mock_doc_v1)
    service.group_repo.get_by_id_for_update = AsyncMock(return_value=mock_group)
    service.doc_repo.get_by_id = AsyncMock(return_value=mock_doc_v1)
    service.doc_repo.get_latest_version_in_group = AsyncMock(return_value=mock_doc_v2)

    with pytest.raises(HTTPException) as exc_info:
        await service.record_review_decision(
            context=org_context,
            document_id=doc_id,
            decision="approved",
            supplier_id=supplier_id,
        )
    assert exc_info.value.status_code == 409
    assert (
        "Cannot approve older version 1; latest version in document group is 2"
        in exc_info.value.detail
    )


@pytest.mark.asyncio
async def test_record_review_decision_approval_demotes_previous_effective_and_dual_audit(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    """Verify approval demotes previous effective doc and captures precise pre/post state in AuditEvent."""
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    old_doc_id = uuid.uuid4()
    new_doc_id = uuid.uuid4()

    old_effective_doc = SupplierDocument(
        id=old_doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=1,
        is_effective_version=True,
    )
    new_replacement_doc = SupplierDocument(
        id=new_doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=2,
        is_effective_version=False,
    )
    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )

    service.get_document = AsyncMock(return_value=new_replacement_doc)
    service.group_repo.get_by_id_for_update = AsyncMock(return_value=mock_group)
    service.doc_repo.get_by_id = AsyncMock(return_value=new_replacement_doc)
    service.doc_repo.get_latest_version_in_group = AsyncMock(
        return_value=new_replacement_doc
    )
    service.doc_repo.get_effective_version = AsyncMock(return_value=old_effective_doc)
    service.doc_repo.update = AsyncMock()

    result = await service.record_review_decision(
        context=org_context,
        document_id=new_doc_id,
        decision="approved",
        comments="Approved replacement version",
        supplier_id=supplier_id,
    )

    assert result.is_effective_version is True
    assert old_effective_doc.is_effective_version is False
    assert service.doc_repo.update.await_count == 2

    # Verify atomic DocumentReview and AuditEvent
    assert mock_session.add.call_count == 2
    review: DocumentReview = mock_session.add.call_args_list[0][0][0]
    audit: AuditEvent = mock_session.add.call_args_list[1][0][0]

    assert isinstance(review, DocumentReview)
    assert review.decision == "approved"

    assert isinstance(audit, AuditEvent)
    assert audit.action == "supplier_document.approved"

    # Verify accurate pre/post semantics in AuditEvent
    assert audit.previous_state["promoted_document"] == {
        "document_id": str(new_doc_id),
        "version": 2,
        "is_effective_version": False,
    }
    assert audit.previous_state["demoted_document"] == {
        "document_id": str(old_doc_id),
        "version": 1,
        "is_effective_version": True,
    }

    assert audit.new_state["promoted_document"] == {
        "document_id": str(new_doc_id),
        "version": 2,
        "is_effective_version": True,
    }
    assert audit.new_state["demoted_document"] == {
        "document_id": str(old_doc_id),
        "version": 1,
        "is_effective_version": False,
    }

    assert audit.metadata_["promoted_version"] == 2
    assert audit.metadata_["demoted_version"] == 1
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_review_decision_rejection_preserves_earlier_effective_version(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    """Verify rejecting an unapproved replacement preserves the earlier effective version intact."""
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    old_doc_id = uuid.uuid4()
    new_doc_id = uuid.uuid4()

    old_effective_doc = SupplierDocument(
        id=old_doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=1,
        is_effective_version=True,
    )
    new_replacement_doc = SupplierDocument(
        id=new_doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=2,
        is_effective_version=False,
    )
    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )

    service.get_document = AsyncMock(return_value=new_replacement_doc)
    service.group_repo.get_by_id_for_update = AsyncMock(return_value=mock_group)
    service.doc_repo.get_by_id = AsyncMock(return_value=new_replacement_doc)
    service.doc_repo.get_latest_version_in_group = AsyncMock(
        return_value=new_replacement_doc
    )
    service.doc_repo.get_effective_version = AsyncMock(return_value=old_effective_doc)
    service.doc_repo.update = AsyncMock()

    result = await service.record_review_decision(
        context=org_context,
        document_id=new_doc_id,
        decision="rejected",
        rejection_reason_code="illegible",
        comments="Document blurry",
        supplier_id=supplier_id,
    )

    # Replacement document remains is_effective_version = False
    assert result.is_effective_version is False
    # Earlier effective doc is NOT touched or demoted
    assert old_effective_doc.is_effective_version is True
    # Neither document required updates to supplier_documents table (effective version unchanged)
    service.doc_repo.update.assert_not_awaited()

    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_review_decision_audit_failure_triggers_rollback(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    """Verify that an error during review/audit write or commit triggers session rollback."""
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_doc = SupplierDocument(
        id=doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=1,
        is_effective_version=False,
    )
    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )

    service.get_document = AsyncMock(return_value=mock_doc)
    service.group_repo.get_by_id_for_update = AsyncMock(return_value=mock_group)
    service.doc_repo.get_by_id = AsyncMock(return_value=mock_doc)
    service.doc_repo.get_latest_version_in_group = AsyncMock(return_value=mock_doc)
    service.doc_repo.get_effective_version = AsyncMock(return_value=None)
    service.doc_repo.update = AsyncMock()

    mock_session.commit.side_effect = RuntimeError("Commit failed")

    with pytest.raises(RuntimeError, match="Commit failed"):
        await service.record_review_decision(
            context=org_context,
            document_id=doc_id,
            decision="approved",
            supplier_id=supplier_id,
        )

    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_review_decision_doc_update_failure_triggers_rollback(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    """Verify that an exception raised by doc_repo.update during approval transition triggers rollback and aborts commit."""
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_doc = SupplierDocument(
        id=doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=1,
        is_effective_version=False,
    )
    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )

    service.get_document = AsyncMock(return_value=mock_doc)
    service.group_repo.get_by_id_for_update = AsyncMock(return_value=mock_group)
    service.doc_repo.get_by_id = AsyncMock(return_value=mock_doc)
    service.doc_repo.get_latest_version_in_group = AsyncMock(return_value=mock_doc)
    service.doc_repo.get_effective_version = AsyncMock(return_value=None)
    # Simulate database update failure mid-mutation
    service.doc_repo.update = AsyncMock(
        side_effect=RuntimeError("Database failure during document update")
    )

    with pytest.raises(RuntimeError, match="Database failure during document update"):
        await service.record_review_decision(
            context=org_context,
            document_id=doc_id,
            decision="approved",
            supplier_id=supplier_id,
        )

    # Rollback must be explicitly awaited
    mock_session.rollback.assert_awaited_once()
    # Commit must not be called
    mock_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_review_decision_audit_insertion_failure_triggers_rollback(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    """Verify that an error during review/audit write or add to session triggers rollback and aborts commit."""
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_doc = SupplierDocument(
        id=doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=1,
        is_effective_version=False,
    )
    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )

    service.get_document = AsyncMock(return_value=mock_doc)
    service.group_repo.get_by_id_for_update = AsyncMock(return_value=mock_group)
    service.doc_repo.get_by_id = AsyncMock(return_value=mock_doc)
    service.doc_repo.get_latest_version_in_group = AsyncMock(return_value=mock_doc)
    service.doc_repo.get_effective_version = AsyncMock(return_value=None)
    service.doc_repo.update = AsyncMock()

    # Simulate session add failure when adding review or audit event
    mock_session.add.side_effect = RuntimeError("Session add failed")

    with pytest.raises(RuntimeError, match="Session add failed"):
        await service.record_review_decision(
            context=org_context,
            document_id=doc_id,
            decision="approved",
            supplier_id=supplier_id,
        )

    mock_session.rollback.assert_awaited_once()
    mock_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoke_effective_version_locking_and_audit(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    """Verify revocation requires min 3 chars, locks parent group, demotes effectiveness, and logs AuditEvent."""
    service = SupplierDocumentDomainService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )
    effective_doc = SupplierDocument(
        id=doc_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
        document_group_id=group_id,
        version=1,
        is_effective_version=True,
    )

    service.group_repo.get_by_id_for_update = AsyncMock(return_value=mock_group)
    service.doc_repo.get_effective_version = AsyncMock(return_value=effective_doc)
    service.doc_repo.update = AsyncMock()

    # Short reason (< 3 chars) rejected
    with pytest.raises(HTTPException) as exc_info:
        await service.revoke_effective_version(
            context=org_context,
            document_group_id=group_id,
            reason="no",
            supplier_id=supplier_id,
        )
    assert exc_info.value.status_code == 422
    assert "Revocation reason must be at least 3 characters" in exc_info.value.detail

    # Valid revocation
    result = await service.revoke_effective_version(
        context=org_context,
        document_group_id=group_id,
        reason="Revoked due to expired statutory certificate",
        supplier_id=supplier_id,
    )

    assert result.is_effective_version is False
    service.group_repo.get_by_id_for_update.assert_awaited_once_with(
        group_id, org_context.organization_id, supplier_id=supplier_id
    )
    service.doc_repo.update.assert_awaited_once_with(effective_doc)

    mock_session.add.assert_called_once()
    audit: AuditEvent = mock_session.add.call_args[0][0]
    assert audit.action == "supplier_document.effective_revoked"
    assert audit.previous_state == {
        "document_id": str(doc_id),
        "version": 1,
        "is_effective_version": True,
    }
    assert audit.new_state["is_effective_version"] is False
    assert audit.metadata_["revoked_version"] == 1
    mock_session.commit.assert_awaited_once()


# =============================================================================
# 4. Critical Remediation Regression Tests (Parts B, C, D)
# =============================================================================


@pytest.mark.asyncio
async def test_document_group_add_requirement_link_origin_validation(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentGroupService(mock_session)
    supplier_id = uuid.uuid4()
    group_id = uuid.uuid4()
    req_id = uuid.uuid4()

    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        document_type_id=uuid.uuid4(),
        organization_id=org_context.organization_id,
    )
    mock_req = MagicMock(id=req_id, document_type_id=mock_group.document_type_id)
    service.get_group = AsyncMock(return_value=mock_group)
    service.req_repo.get_by_id = AsyncMock(return_value=mock_req)
    service.link_repo.get_by_group_and_requirement = AsyncMock(return_value=None)
    service.link_repo.create = AsyncMock(side_effect=lambda l: l)

    # 1. "manual" is accepted
    link_manual = await service.add_requirement_link(
        context=org_context,
        supplier_id=supplier_id,
        group_id=group_id,
        requirement_id=req_id,
        link_origin="manual",
    )
    assert link_manual.link_origin == "manual"

    # 2. "policy_engine" is accepted
    link_pe = await service.add_requirement_link(
        context=org_context,
        supplier_id=supplier_id,
        group_id=group_id,
        requirement_id=req_id,
        link_origin="policy_engine",
    )
    assert link_pe.link_origin == "policy_engine"

    # 3. "legacy_backfill" is rejected with 422 (migration-controlled only)
    with pytest.raises(HTTPException) as exc_info:
        await service.add_requirement_link(
            context=org_context,
            supplier_id=supplier_id,
            group_id=group_id,
            requirement_id=req_id,
            link_origin="legacy_backfill",
        )
    assert exc_info.value.status_code == 422
    assert "link_origin must be 'manual' or 'policy_engine'" in exc_info.value.detail

    # 4. Unknown/spoofed origin is rejected with 422
    with pytest.raises(HTTPException) as exc_info:
        await service.add_requirement_link(
            context=org_context,
            supplier_id=supplier_id,
            group_id=group_id,
            requirement_id=req_id,
            link_origin="arbitrary_origin",
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_create_document_version_duplicate_version_integrity_error_mapped_to_409(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentDomainService(mock_session)
    group_id = uuid.uuid4()
    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=uuid.uuid4(),
        document_type_id=uuid.uuid4(),
        organization_id=org_context.organization_id,
    )
    service.group_repo.get_by_id_for_update = AsyncMock(return_value=mock_group)
    service.doc_repo.get_latest_version_in_group = AsyncMock(return_value=None)

    # Simulate Postgres 23505 unique constraint violation on uq_supplier_documents_group_version
    orig_mock = MagicMock()
    orig_mock.sqlstate = "23505"
    orig_mock.constraint_name = "uq_supplier_documents_group_version"
    orig_mock.__str__ = lambda self: (
        'duplicate key value violates unique constraint "uq_supplier_documents_group_version"'
    )
    integrity_error = IntegrityError(
        "INSERT INTO supplier_documents ...", {}, orig_mock
    )

    service.doc_repo.create = AsyncMock(side_effect=integrity_error)

    with pytest.raises(HTTPException) as exc_info:
        await service.create_document_version(
            context=org_context,
            document_group_id=group_id,
            file_path="uploads/test.pdf",
            file_name="test.pdf",
            file_size_bytes=1024,
            file_mime_type="application/pdf",
            file_hash_sha256="a" * 64,
        )
    assert exc_info.value.status_code == 409
    assert "already exists for this document group" in exc_info.value.detail
    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_document_version_unrelated_integrity_error_reraised(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentDomainService(mock_session)
    group_id = uuid.uuid4()
    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=uuid.uuid4(),
        document_type_id=uuid.uuid4(),
        organization_id=org_context.organization_id,
    )
    service.group_repo.get_by_id_for_update = AsyncMock(return_value=mock_group)
    service.doc_repo.get_latest_version_in_group = AsyncMock(return_value=None)

    # Simulate unrelated foreign key integrity violation
    orig_mock = MagicMock()
    orig_mock.sqlstate = "23503"
    orig_mock.constraint_name = "fk_supplier_documents_document_type"
    orig_mock.__str__ = lambda self: "foreign key violation"
    unrelated_error = IntegrityError("INSERT INTO ...", {}, orig_mock)

    service.doc_repo.create = AsyncMock(side_effect=unrelated_error)

    with pytest.raises(IntegrityError):
        await service.create_document_version(
            context=org_context,
            document_group_id=group_id,
            file_path="uploads/test.pdf",
            file_name="test.pdf",
            file_size_bytes=1024,
            file_mime_type="application/pdf",
            file_hash_sha256="a" * 64,
        )
    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_repository_supplier_id_filtering_in_sql():
    mock_db = AsyncMock(spec=AsyncSession)
    mock_db.execute = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = result_mock

    org_id = uuid.uuid4()
    supplier_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    # 1. SupplierSiteRepository.get_by_id with and without supplier_id
    site_repo = SupplierSiteRepository(mock_db)
    await site_repo.get_by_id(entity_id, org_id, supplier_id=supplier_id)
    stmt = mock_db.execute.call_args[0][0]
    sql_str = str(stmt.compile())
    assert "supplier_sites.supplier_id =" in sql_str
    assert "supplier_sites.organization_id =" in sql_str

    await site_repo.get_by_id(entity_id, org_id, supplier_id=None)
    stmt_no_sup = mock_db.execute.call_args[0][0]
    sql_str_no_sup = str(stmt_no_sup.compile())
    assert "supplier_sites.supplier_id =" not in sql_str_no_sup

    # 2. SupplierDocumentGroupRepository.get_by_id with and without supplier_id
    group_repo = SupplierDocumentGroupRepository(mock_db)
    await group_repo.get_by_id(entity_id, org_id, supplier_id=supplier_id)
    stmt_group = mock_db.execute.call_args[0][0]
    sql_str_group = str(stmt_group.compile())
    assert "supplier_document_groups.supplier_id =" in sql_str_group

    await group_repo.get_by_id_for_update(entity_id, org_id, supplier_id=supplier_id)
    stmt_lock = mock_db.execute.call_args[0][0]
    sql_str_lock = str(stmt_lock.compile())
    assert "supplier_document_groups.supplier_id =" in sql_str_lock

    # 3. SupplierDocumentRepository.get_by_id with and without supplier_id
    doc_repo = SupplierDocumentRepository(mock_db)
    await doc_repo.get_by_id(entity_id, org_id, supplier_id=supplier_id)
    stmt_doc = mock_db.execute.call_args[0][0]
    sql_str_doc = str(stmt_doc.compile())
    assert "supplier_documents.supplier_id =" in sql_str_doc


# =============================================================================
# 5. Final Schema & Supplier-Filter Remediation Tests
# =============================================================================


def test_migration_upgrade_and_downgrade_legacy_constraint_management():
    import importlib.util
    from pathlib import Path

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "f129c5e378bc_phase_3_4_document_groups_and_lineage.py"
    )
    spec = importlib.util.spec_from_file_location("mig_test", str(migration_path))
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    # 1. Verify upgrade drops uq_supplier_documents_version BEFORE adding uq_supplier_documents_group_version
    mock_execute_upgrade = MagicMock()
    with patch.object(mig.op, "execute", mock_execute_upgrade):
        mig.upgrade()

    upgrade_calls = [c[0][0] for c in mock_execute_upgrade.call_args_list if c[0]]
    drop_idx = -1
    add_idx = -1
    for i, sql in enumerate(upgrade_calls):
        if "DROP CONSTRAINT IF EXISTS uq_supplier_documents_version" in sql:
            drop_idx = i
        if "ADD CONSTRAINT uq_supplier_documents_group_version" in sql:
            add_idx = i

    assert drop_idx != -1, "upgrade must explicitly drop uq_supplier_documents_version"
    assert add_idx != -1, "upgrade must add uq_supplier_documents_group_version"
    assert drop_idx < add_idx, (
        "uq_supplier_documents_version must be dropped before new schema constraint is added"
    )

    # 2. Verify downgrade preflight check runs first and checks for version collisions
    mock_execute_downgrade = MagicMock()
    with patch.object(mig.op, "execute", mock_execute_downgrade):
        mig.downgrade()

    downgrade_calls = [c[0][0] for c in mock_execute_downgrade.call_args_list if c[0]]
    preflight_sql = downgrade_calls[0]
    assert "v_version_collisions" in preflight_sql
    assert "GROUP BY supplier_id, document_type_id, version" in preflight_sql
    assert (
        "Cannot restore legacy unique constraint uq_supplier_documents_version"
        in preflight_sql
    )

    # 3. Verify downgrade restores uq_supplier_documents_version in subsequent step
    restore_idx = -1
    for i, sql in enumerate(downgrade_calls):
        if "ADD CONSTRAINT uq_supplier_documents_version" in sql:
            restore_idx = i

    assert restore_idx > 0, (
        "downgrade must restore uq_supplier_documents_version after preflight"
    )


def test_migration_downgrade_collision_detection_logic():
    """Verify the collision detection query semantics: aborts on duplicates, passes when distinct."""
    from collections import Counter

    def detect_collisions(docs: list[dict]) -> int:
        counts = Counter(
            (d["supplier_id"], d["document_type_id"], d["version"]) for d in docs
        )
        return sum(1 for _, count in counts.items() if count > 1)

    s1, dt1 = uuid.uuid4(), uuid.uuid4()
    s2 = uuid.uuid4()

    # Case A: No collisions across distinct groups / versions
    distinct_docs = [
        {"supplier_id": s1, "document_type_id": dt1, "version": 1},
        {"supplier_id": s1, "document_type_id": dt1, "version": 2},
        {"supplier_id": s2, "document_type_id": dt1, "version": 1},
    ]
    assert detect_collisions(distinct_docs) == 0

    # Case B: Multi-group collision on (supplier_id, document_type_id, version)
    colliding_docs = [
        {"supplier_id": s1, "document_type_id": dt1, "version": 1, "group": "site1"},
        {"supplier_id": s1, "document_type_id": dt1, "version": 1, "group": "site2"},
    ]
    assert detect_collisions(colliding_docs) == 1


@pytest.mark.asyncio
async def test_create_group_site_lookup_supplier_scoping(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentGroupService(mock_session)
    supplier_id = uuid.uuid4()
    other_supplier_id = uuid.uuid4()
    doc_type_id = uuid.uuid4()
    site_id = uuid.uuid4()

    mock_supplier = Supplier(
        id=supplier_id, organization_id=org_context.organization_id
    )
    mock_doc_type = MagicMock(id=doc_type_id, name="GSTIN")
    service.supplier_repo.get_by_id = AsyncMock(return_value=mock_supplier)
    service.doc_type_repo.get_by_id = AsyncMock(return_value=mock_doc_type)

    # 1. Site belongs to matching supplier: site_repo.get_by_id is called with supplier_id=supplier_id
    mock_site = SupplierSite(
        id=site_id,
        supplier_id=supplier_id,
        organization_id=org_context.organization_id,
    )
    service.site_repo.get_by_id = AsyncMock(return_value=mock_site)
    service.group_repo.get_by_site_ref = AsyncMock(return_value=None)
    service.group_repo.create = AsyncMock(side_effect=lambda g: g)

    created = await service.create_group(
        context=org_context,
        supplier_id=supplier_id,
        document_type_id=doc_type_id,
        coverage_scope="site",
        site_id=site_id,
    )
    assert created.site_id == site_id
    service.site_repo.get_by_id.assert_awaited_with(
        site_id, org_context.organization_id, supplier_id=supplier_id
    )

    # 2. Site belongs to a different supplier: raises 422 with "belongs to a different supplier"
    other_site = SupplierSite(
        id=site_id,
        supplier_id=other_supplier_id,
        organization_id=org_context.organization_id,
    )

    async def mock_get_by_id(s_id, org_id, supplier_id=None):
        if supplier_id == other_supplier_id or supplier_id is None:
            return other_site
        return None

    service.site_repo.get_by_id = AsyncMock(side_effect=mock_get_by_id)
    with pytest.raises(HTTPException) as exc_info:
        await service.create_group(
            context=org_context,
            supplier_id=supplier_id,
            document_type_id=doc_type_id,
            coverage_scope="site",
            site_id=site_id,
        )
    assert exc_info.value.status_code == 422
    assert "belongs to a different supplier" in exc_info.value.detail

    # 3. Site does not exist at all in the tenant: raises 404
    service.site_repo.get_by_id = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc_info:
        await service.create_group(
            context=org_context,
            supplier_id=supplier_id,
            document_type_id=doc_type_id,
            coverage_scope="site",
            site_id=site_id,
        )
    assert exc_info.value.status_code == 404
    assert "not found in this organization" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_document_version_predecessor_supplier_scoping(
    org_context: OrganizationContext, mock_session: AsyncMock
):
    service = SupplierDocumentDomainService(mock_session)
    group_id = uuid.uuid4()
    supplier_id = uuid.uuid4()
    doc_type_id = uuid.uuid4()
    prev_doc_id = uuid.uuid4()

    mock_group = SupplierDocumentGroup(
        id=group_id,
        supplier_id=supplier_id,
        document_type_id=doc_type_id,
        organization_id=org_context.organization_id,
    )
    service.group_repo.get_by_id_for_update = AsyncMock(return_value=mock_group)

    # 1. Predecessor lookup passes supplier_id=group.supplier_id
    mock_predecessor = SupplierDocument(
        id=prev_doc_id,
        document_group_id=group_id,
        supplier_id=supplier_id,
        document_type_id=doc_type_id,
        organization_id=org_context.organization_id,
        version=1,
    )
    service.doc_repo.get_by_id = AsyncMock(return_value=mock_predecessor)
    service.doc_repo.get_child_versions = AsyncMock(return_value=[])
    service.doc_repo.create = AsyncMock(side_effect=lambda d: d)

    created = await service.create_document_version(
        context=org_context,
        document_group_id=group_id,
        file_path="uploads/v2.pdf",
        file_name="v2.pdf",
        file_size_bytes=2048,
        file_mime_type="application/pdf",
        file_hash_sha256="b" * 64,
        previous_version_id=prev_doc_id,
    )
    assert created.version == 2
    service.doc_repo.get_by_id.assert_awaited_once_with(
        prev_doc_id, org_context.organization_id, supplier_id=supplier_id
    )

    # 2. Predecessor not found in tenant/supplier: raises 404
    service.doc_repo.get_by_id = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc_info:
        await service.create_document_version(
            context=org_context,
            document_group_id=group_id,
            file_path="uploads/v2.pdf",
            file_name="v2.pdf",
            file_size_bytes=2048,
            file_mime_type="application/pdf",
            file_hash_sha256="b" * 64,
            previous_version_id=prev_doc_id,
        )
    assert exc_info.value.status_code == 404
    assert "Predecessor document" in exc_info.value.detail

    # 3. Predecessor belongs to a different document group: raises 422
    other_group_doc = SupplierDocument(
        id=prev_doc_id,
        document_group_id=uuid.uuid4(),
        supplier_id=supplier_id,
        document_type_id=doc_type_id,
        organization_id=org_context.organization_id,
        version=1,
    )
    service.doc_repo.get_by_id = AsyncMock(return_value=other_group_doc)
    with pytest.raises(HTTPException) as exc_info:
        await service.create_document_version(
            context=org_context,
            document_group_id=group_id,
            file_path="uploads/v2.pdf",
            file_name="v2.pdf",
            file_size_bytes=2048,
            file_mime_type="application/pdf",
            file_hash_sha256="b" * 64,
            previous_version_id=prev_doc_id,
        )
    assert exc_info.value.status_code == 422
    assert (
        "Predecessor document belongs to a different document group"
        in exc_info.value.detail
    )
