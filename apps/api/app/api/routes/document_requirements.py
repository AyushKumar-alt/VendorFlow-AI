from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.auth.dependencies import get_organization_context
from app.db.session import get_db
from app.schemas.document_requirement import (
    DocumentRequirementCreate,
    DocumentRequirementListResponse,
    DocumentRequirementResponse,
    DocumentRequirementUpdate,
)
from app.services.document_requirement import DocumentRequirementService

router = APIRouter(prefix="/document-requirements", tags=["document-requirements"])


@router.post(
    "",
    response_model=DocumentRequirementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new document requirement in the current organization",
)
async def create_document_requirement(
    request: Request,
    data: DocumentRequirementCreate,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentRequirementResponse:
    """Create a new requirement scoped to the caller's organization.

    Ensures document_type belongs to caller's organization and prevents duplicate mappings.
    """
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = DocumentRequirementService(db)
    requirement = await service.create_requirement(
        context=ctx,
        data=data,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return DocumentRequirementResponse.model_validate(requirement)


@router.get(
    "",
    response_model=DocumentRequirementListResponse,
    status_code=status.HTTP_200_OK,
    summary="List document requirements for the current organization",
)
async def list_document_requirements(
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    document_type_id: Annotated[
        UUID | None, Query(description="Filter by document type ID")
    ] = None,
    supplier_category: Annotated[
        str | None, Query(description="Filter by supplier category")
    ] = None,
    supplier_tier: Annotated[
        str | None, Query(description="Filter by supplier tier")
    ] = None,
    is_active: Annotated[
        bool | None, Query(description="Filter by active/disabled status")
    ] = None,
    is_mandatory: Annotated[
        bool | None, Query(description="Filter by mandatory/optional status")
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number (1-based)")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
) -> DocumentRequirementListResponse:
    """List document requirements strictly within the authenticated organization boundary."""
    service = DocumentRequirementService(db)
    return await service.list_requirements(
        context=ctx,
        document_type_id=document_type_id,
        supplier_category=supplier_category,
        supplier_tier=supplier_tier,
        is_active=is_active,
        is_mandatory=is_mandatory,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{requirement_id}",
    response_model=DocumentRequirementResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a document requirement by ID",
)
async def get_document_requirement(
    requirement_id: UUID,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentRequirementResponse:
    """Fetch a document requirement by ID within the caller's organization."""
    service = DocumentRequirementService(db)
    requirement = await service.get_requirement(ctx, requirement_id)
    return DocumentRequirementResponse.model_validate(requirement)


@router.patch(
    "/{requirement_id}",
    response_model=DocumentRequirementResponse,
    status_code=status.HTTP_200_OK,
    summary="Partially update a document requirement",
)
async def update_document_requirement(
    request: Request,
    requirement_id: UUID,
    data: DocumentRequirementUpdate,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentRequirementResponse:
    """Update editable fields of a document requirement within the caller's organization."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = DocumentRequirementService(db)
    updated = await service.update_requirement(
        context=ctx,
        requirement_id=requirement_id,
        data=data,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return DocumentRequirementResponse.model_validate(updated)


@router.post(
    "/{requirement_id}/enable",
    response_model=DocumentRequirementResponse,
    status_code=status.HTTP_200_OK,
    summary="Enable a document requirement",
)
async def enable_document_requirement(
    request: Request,
    requirement_id: UUID,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentRequirementResponse:
    """Enable a document requirement within the caller's organization."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = DocumentRequirementService(db)
    enabled = await service.set_requirement_status(
        context=ctx,
        requirement_id=requirement_id,
        is_active=True,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return DocumentRequirementResponse.model_validate(enabled)


@router.post(
    "/{requirement_id}/disable",
    response_model=DocumentRequirementResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable a document requirement",
)
async def disable_document_requirement(
    request: Request,
    requirement_id: UUID,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentRequirementResponse:
    """Disable a document requirement within the caller's organization."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = DocumentRequirementService(db)
    disabled = await service.set_requirement_status(
        context=ctx,
        requirement_id=requirement_id,
        is_active=False,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return DocumentRequirementResponse.model_validate(disabled)
