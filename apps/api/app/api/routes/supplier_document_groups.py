from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.auth.dependencies import require_roles
from app.db.session import get_db
from app.schemas.document_group_requirement_link import (
    DocumentGroupRequirementLinkCreate,
    DocumentGroupRequirementLinkListResponse,
    DocumentGroupRequirementLinkResponse,
)
from app.schemas.supplier_document_group import (
    SupplierDocumentGroupCreate,
    SupplierDocumentGroupListResponse,
    SupplierDocumentGroupResponse,
)
from app.services.supplier_document_group import SupplierDocumentGroupService

router = APIRouter(
    prefix="/suppliers/{supplier_id}/document-groups",
    tags=["supplier-document-groups"],
)


@router.post(
    "",
    response_model=SupplierDocumentGroupResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_supplier_document_group",
    summary="Create a new document group for a supplier",
)
async def create_supplier_document_group(
    supplier_id: UUID,
    data: SupplierDocumentGroupCreate,
    ctx: Annotated[OrganizationContext, Depends(require_roles("owner", "admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> SupplierDocumentGroupResponse:
    """Create a new document group container binding supplier, document type, and scope."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = SupplierDocumentGroupService(db)
    group = await service.create_group(
        context=ctx,
        supplier_id=supplier_id,
        document_type_id=data.document_type_id,
        coverage_scope=data.coverage_scope,
        site_id=data.site_id,
        document_reference=data.document_reference,
        display_name=data.display_name,
        issuing_authority=data.issuing_authority,
        requirement_ids=data.requirement_ids,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return SupplierDocumentGroupResponse.model_validate(group)


@router.get(
    "",
    response_model=SupplierDocumentGroupListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_supplier_document_groups",
    summary="List document groups for a supplier with optional type and site filters",
)
async def list_supplier_document_groups(
    supplier_id: UUID,
    ctx: Annotated[
        OrganizationContext,
        Depends(require_roles("owner", "admin", "reviewer", "viewer")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    document_type_id: Annotated[
        UUID | None,
        Query(description="Filter by document type UUID"),
    ] = None,
    site_id: Annotated[
        UUID | None,
        Query(description="Filter by supplier site UUID"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number (1-based)")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, description="Page size (max 100)")
    ] = 20,
) -> SupplierDocumentGroupListResponse:
    """Return paginated list of document groups for a supplier within caller's organization."""
    service = SupplierDocumentGroupService(db)
    items, total = await service.list_groups(
        context=ctx,
        supplier_id=supplier_id,
        document_type_id=document_type_id,
        site_id=site_id,
        page=page,
        page_size=page_size,
    )
    group_responses = [SupplierDocumentGroupResponse.model_validate(g) for g in items]
    return SupplierDocumentGroupListResponse.create(
        items=group_responses,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{group_id}",
    response_model=SupplierDocumentGroupResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_supplier_document_group",
    summary="Retrieve a single document group by ID",
)
async def get_supplier_document_group(
    supplier_id: UUID,
    group_id: UUID,
    ctx: Annotated[
        OrganizationContext,
        Depends(require_roles("owner", "admin", "reviewer", "viewer")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SupplierDocumentGroupResponse:
    """Retrieve a single document group verifying organization and supplier ownership."""
    service = SupplierDocumentGroupService(db)
    group = await service.get_group(
        context=ctx,
        supplier_id=supplier_id,
        group_id=group_id,
    )
    return SupplierDocumentGroupResponse.model_validate(group)


@router.get(
    "/{group_id}/requirements",
    response_model=DocumentGroupRequirementLinkListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_group_requirement_links",
    summary="List compliance requirement links for a document group",
)
async def list_group_requirement_links(
    supplier_id: UUID,
    group_id: UUID,
    ctx: Annotated[
        OrganizationContext,
        Depends(require_roles("owner", "admin", "reviewer", "viewer")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentGroupRequirementLinkListResponse:
    """List all requirement links associated with a document group."""
    service = SupplierDocumentGroupService(db)
    links = await service.list_requirement_links(
        context=ctx,
        supplier_id=supplier_id,
        group_id=group_id,
    )
    link_responses = [
        DocumentGroupRequirementLinkResponse.model_validate(l) for l in links
    ]
    return DocumentGroupRequirementLinkListResponse(
        items=link_responses,
        total=len(link_responses),
    )


@router.post(
    "/{group_id}/requirements",
    response_model=DocumentGroupRequirementLinkResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="add_group_requirement_link",
    summary="Link a requirement to a document group",
)
async def add_group_requirement_link(
    supplier_id: UUID,
    group_id: UUID,
    data: DocumentGroupRequirementLinkCreate,
    ctx: Annotated[OrganizationContext, Depends(require_roles("owner", "admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> DocumentGroupRequirementLinkResponse:
    """Associate a compliance requirement with a document group."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = SupplierDocumentGroupService(db)
    link = await service.add_requirement_link(
        context=ctx,
        group_id=group_id,
        requirement_id=data.requirement_id,
        link_origin=data.link_origin,
        supplier_id=supplier_id,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return DocumentGroupRequirementLinkResponse.model_validate(link)


@router.delete(
    "/{group_id}/requirements/{requirement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    operation_id="remove_group_requirement_link",
    summary="Remove a requirement link from a document group",
)
async def remove_group_requirement_link(
    supplier_id: UUID,
    group_id: UUID,
    requirement_id: UUID,
    ctx: Annotated[OrganizationContext, Depends(require_roles("owner", "admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> Response:
    """Disassociate a compliance requirement from a document group."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = SupplierDocumentGroupService(db)
    await service.remove_requirement_link(
        context=ctx,
        group_id=group_id,
        requirement_id=requirement_id,
        supplier_id=supplier_id,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
