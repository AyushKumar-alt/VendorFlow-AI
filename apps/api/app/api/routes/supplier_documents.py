from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.auth.dependencies import require_roles
from app.db.session import get_db
from app.schemas.document_review import (
    DocumentReviewCreate,
    DocumentReviewListResponse,
    DocumentReviewResponse,
)
from app.schemas.supplier_document import (
    EffectiveDocumentRevokeRequest,
    SupplierDocumentResponse,
)
from app.services.supplier_document import SupplierDocumentDomainService

router = APIRouter(prefix="/suppliers/{supplier_id}", tags=["supplier-documents"])


@router.get(
    "/document-groups/{group_id}/effective-document",
    response_model=SupplierDocumentResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_group_effective_document",
    summary="Retrieve the active effective document version for a document group",
)
async def get_group_effective_document(
    supplier_id: UUID,
    group_id: UUID,
    ctx: Annotated[
        OrganizationContext,
        Depends(require_roles("owner", "admin", "reviewer", "viewer")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SupplierDocumentResponse:
    """Retrieve the currently effective compliance document version for a group."""
    service = SupplierDocumentDomainService(db)
    doc = await service.get_effective_version(
        context=ctx,
        supplier_id=supplier_id,
        document_group_id=group_id,
    )
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No effective document version found in document group {group_id}.",
        )
    return SupplierDocumentResponse.model_validate(doc)


@router.get(
    "/document-groups/{group_id}/documents",
    response_model=list[SupplierDocumentResponse],
    status_code=status.HTTP_200_OK,
    operation_id="list_group_document_versions",
    summary="List all versions in a document group ordered by version ascending",
)
async def list_group_document_versions(
    supplier_id: UUID,
    group_id: UUID,
    ctx: Annotated[
        OrganizationContext,
        Depends(require_roles("owner", "admin", "reviewer", "viewer")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[SupplierDocumentResponse]:
    """Retrieve the complete version history/lineage of a document group."""
    service = SupplierDocumentDomainService(db)
    docs = await service.list_documents_in_group(
        context=ctx,
        supplier_id=supplier_id,
        document_group_id=group_id,
    )
    return [SupplierDocumentResponse.model_validate(d) for d in docs]


@router.get(
    "/documents/{document_id}",
    response_model=SupplierDocumentResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_supplier_document",
    summary="Retrieve a specific supplier document by ID",
)
async def get_supplier_document(
    supplier_id: UUID,
    document_id: UUID,
    ctx: Annotated[
        OrganizationContext,
        Depends(require_roles("owner", "admin", "reviewer", "viewer")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SupplierDocumentResponse:
    """Retrieve metadata of a specific document version verifying tenant and supplier scope."""
    service = SupplierDocumentDomainService(db)
    doc = await service.get_document(
        document_id=document_id,
        context=ctx,
        supplier_id=supplier_id,
    )
    return SupplierDocumentResponse.model_validate(doc)


@router.get(
    "/documents/{document_id}/reviews",
    response_model=DocumentReviewListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_document_reviews",
    summary="List review history for a document version",
)
async def list_document_reviews(
    supplier_id: UUID,
    document_id: UUID,
    ctx: Annotated[
        OrganizationContext,
        Depends(require_roles("owner", "admin", "reviewer", "viewer")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1, description="Page number (1-based)")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, description="Page size (max 100)")
    ] = 20,
) -> DocumentReviewListResponse:
    """Retrieve paginated human review decisions for a document ordered newest-first."""
    service = SupplierDocumentDomainService(db)
    items, total = await service.list_reviews_for_document(
        context=ctx,
        supplier_id=supplier_id,
        document_id=document_id,
        page=page,
        page_size=page_size,
    )
    review_responses = [DocumentReviewResponse.model_validate(r) for r in items]
    return DocumentReviewListResponse.create(
        items=review_responses,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/documents/{document_id}/reviews",
    response_model=SupplierDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="record_document_review",
    summary="Record a review decision for a document version",
)
async def record_document_review(
    supplier_id: UUID,
    document_id: UUID,
    data: DocumentReviewCreate,
    ctx: Annotated[
        OrganizationContext,
        Depends(require_roles("owner", "admin", "reviewer")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> SupplierDocumentResponse:
    """Record approval, rejection, or revision request managing effective-version concurrency."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = SupplierDocumentDomainService(db)
    doc = await service.record_review_decision(
        context=ctx,
        document_id=document_id,
        decision=data.decision,
        rejection_reason_code=data.rejection_reason_code,
        comments=data.comments,
        verified_data=data.verified_data,
        supplier_id=supplier_id,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return SupplierDocumentResponse.model_validate(doc)


@router.post(
    "/document-groups/{group_id}/revoke-effective",
    response_model=SupplierDocumentResponse,
    status_code=status.HTTP_200_OK,
    operation_id="revoke_effective_document",
    summary="Revoke the active effective document version in a group",
)
async def revoke_effective_document(
    supplier_id: UUID,
    group_id: UUID,
    data: EffectiveDocumentRevokeRequest,
    ctx: Annotated[OrganizationContext, Depends(require_roles("owner", "admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> SupplierDocumentResponse:
    """Revoke effectiveness from the active document version under exclusive group row lock."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = SupplierDocumentDomainService(db)
    doc = await service.revoke_effective_version(
        context=ctx,
        document_group_id=group_id,
        reason=data.reason,
        supplier_id=supplier_id,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return SupplierDocumentResponse.model_validate(doc)
