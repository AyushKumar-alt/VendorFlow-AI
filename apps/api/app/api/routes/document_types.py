from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.auth.dependencies import get_organization_context
from app.db.session import get_db
from app.schemas.document_type import (
    DocumentTypeCreate,
    DocumentTypeListResponse,
    DocumentTypeResponse,
    DocumentTypeUpdate,
)
from app.services.document_type import DocumentTypeService

router = APIRouter(prefix="/document-types", tags=["document-types"])


@router.post(
    "",
    response_model=DocumentTypeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new document type in the current organization",
)
async def create_document_type(
    request: Request,
    data: DocumentTypeCreate,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentTypeResponse:
    """Create a new document type scoped to the authenticated caller's organization."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = DocumentTypeService(db)
    doc_type = await service.create_document_type(
        context=ctx,
        data=data,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return DocumentTypeResponse.model_validate(doc_type)


@router.get(
    "",
    response_model=DocumentTypeListResponse,
    status_code=status.HTTP_200_OK,
    summary="List document types for the current organization",
)
async def list_document_types(
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    category: Annotated[
        str | None,
        Query(
            description="Filter by category (statutory, tax, quality, financial, legal, safety)"
        ),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Search term matching name, code, or description"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number (1-based)")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
) -> DocumentTypeListResponse:
    """List document types strictly within the authenticated organization boundary."""
    service = DocumentTypeService(db)
    return await service.list_document_types(
        context=ctx,
        category=category,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{document_type_id}",
    response_model=DocumentTypeResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a document type by ID",
)
async def get_document_type(
    document_type_id: UUID,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentTypeResponse:
    """Fetch a document type by ID within the caller's organization."""
    service = DocumentTypeService(db)
    doc_type = await service.get_document_type(ctx, document_type_id)
    return DocumentTypeResponse.model_validate(doc_type)


@router.patch(
    "/{document_type_id}",
    response_model=DocumentTypeResponse,
    status_code=status.HTTP_200_OK,
    summary="Partially update a document type",
)
async def update_document_type(
    request: Request,
    document_type_id: UUID,
    data: DocumentTypeUpdate,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentTypeResponse:
    """Update editable fields of a document type within the caller's organization."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = DocumentTypeService(db)
    updated = await service.update_document_type(
        context=ctx,
        document_type_id=document_type_id,
        data=data,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return DocumentTypeResponse.model_validate(updated)
