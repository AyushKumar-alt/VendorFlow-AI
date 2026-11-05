from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.auth.dependencies import get_organization_context
from app.db.session import get_db
from app.schemas.document_requirement import (
    SupplierRequirementsEvaluationResponse,
)
from app.schemas.supplier import (
    SupplierCreate,
    SupplierListResponse,
    SupplierResponse,
    SupplierUpdate,
)
from app.services.document_requirement import DocumentRequirementService
from app.services.supplier import SupplierService

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.post(
    "",
    response_model=SupplierResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new supplier in the current organization",
)
async def create_supplier(
    request: Request,
    data: SupplierCreate,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SupplierResponse:
    """Create a new supplier record strictly within the caller's organization boundary."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = SupplierService(db)
    supplier = await service.create_supplier(
        context=ctx,
        data=data,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return SupplierResponse.model_validate(supplier)


@router.get(
    "",
    response_model=SupplierListResponse,
    status_code=status.HTTP_200_OK,
    summary="List suppliers belonging to the current organization",
)
async def list_suppliers(
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, description="Page size (max 100)")
    ] = 20,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="Filter by supplier lifecycle status",
        ),
    ] = None,
    search: Annotated[
        str | None,
        Query(
            description="Search term matching legal name, trade name, vendor code, GSTIN, or PAN"
        ),
    ] = None,
) -> SupplierListResponse:
    """Return paginated list of suppliers belonging strictly to caller's organization."""
    service = SupplierService(db)
    items, total = await service.list_suppliers(
        context=ctx,
        status_filter=status_filter,
        search=search,
        page=page,
        page_size=page_size,
    )
    supplier_responses = [SupplierResponse.model_validate(item) for item in items]
    return SupplierListResponse.create(
        items=supplier_responses,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{supplier_id}",
    response_model=SupplierResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a supplier by ID",
)
async def get_supplier(
    supplier_id: UUID,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SupplierResponse:
    """Fetch a single supplier by ID within caller's organization. Returns 404 if outside tenant."""
    service = SupplierService(db)
    supplier = await service.get_supplier(context=ctx, supplier_id=supplier_id)
    return SupplierResponse.model_validate(supplier)


@router.patch(
    "/{supplier_id}",
    response_model=SupplierResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a supplier",
)
async def update_supplier(
    request: Request,
    supplier_id: UUID,
    data: SupplierUpdate,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SupplierResponse:
    """Partially update an existing supplier within caller's organization."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = SupplierService(db)
    supplier = await service.update_supplier(
        context=ctx,
        supplier_id=supplier_id,
        data=data,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return SupplierResponse.model_validate(supplier)


@router.post(
    "/{supplier_id}/archive",
    response_model=SupplierResponse,
    status_code=status.HTTP_200_OK,
    summary="Soft-archive a supplier",
)
async def archive_supplier(
    request: Request,
    supplier_id: UUID,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SupplierResponse:
    """Soft-archive a supplier, preventing future updates while retaining history."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = SupplierService(db)
    supplier = await service.archive_supplier(
        context=ctx,
        supplier_id=supplier_id,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return SupplierResponse.model_validate(supplier)


@router.get(
    "/{supplier_id}/document-requirements",
    response_model=SupplierRequirementsEvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate applicable document requirements for a supplier",
)
async def evaluate_supplier_document_requirements(
    supplier_id: UUID,
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SupplierRequirementsEvaluationResponse:
    """Evaluate mandatory and optional document requirements applicable to a supplier."""
    service = DocumentRequirementService(db)
    return await service.evaluate_supplier_requirements(
        context=ctx,
        supplier_id=supplier_id,
    )
