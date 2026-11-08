from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.auth.dependencies import require_roles
from app.db.session import get_db
from app.schemas.supplier_site import (
    SupplierSiteCreate,
    SupplierSiteListResponse,
    SupplierSiteResponse,
    SupplierSiteUpdate,
)
from app.services.supplier_site import SupplierSiteService

router = APIRouter(prefix="/suppliers/{supplier_id}/sites", tags=["supplier-sites"])


@router.post(
    "",
    response_model=SupplierSiteResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_supplier_site",
    summary="Create a physical or operational site for a supplier",
)
async def create_supplier_site(
    supplier_id: UUID,
    data: SupplierSiteCreate,
    ctx: Annotated[OrganizationContext, Depends(require_roles("owner", "admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> SupplierSiteResponse:
    """Create a new supplier site record strictly within the caller's organization boundary."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = SupplierSiteService(db)
    site = await service.create_site(
        context=ctx,
        supplier_id=supplier_id,
        site_code=data.site_code,
        site_name=data.site_name,
        address_line1=data.address_line1,
        address_line2=data.address_line2,
        city=data.city,
        state=data.state,
        postal_code=data.postal_code,
        country_code=data.country_code,
        gstin=data.gstin,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return SupplierSiteResponse.model_validate(site)


@router.get(
    "",
    response_model=SupplierSiteListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_supplier_sites",
    summary="List all sites for a supplier",
)
async def list_supplier_sites(
    supplier_id: UUID,
    ctx: Annotated[
        OrganizationContext,
        Depends(require_roles("owner", "admin", "reviewer", "viewer")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    is_active: Annotated[
        bool | None,
        Query(description="Filter sites by active status"),
    ] = None,
) -> SupplierSiteListResponse:
    """Retrieve all sites belonging to a supplier within caller's organization boundary."""
    service = SupplierSiteService(db)
    sites = await service.list_sites(
        context=ctx,
        supplier_id=supplier_id,
        is_active=is_active,
    )
    items = [SupplierSiteResponse.model_validate(s) for s in sites]
    return SupplierSiteListResponse.create(
        items=items,
        total=len(items),
        page=1,
        page_size=max(1, len(items)),
    )


@router.get(
    "/{site_id}",
    response_model=SupplierSiteResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_supplier_site",
    summary="Retrieve a single supplier site by ID",
)
async def get_supplier_site(
    supplier_id: UUID,
    site_id: UUID,
    ctx: Annotated[
        OrganizationContext,
        Depends(require_roles("owner", "admin", "reviewer", "viewer")),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SupplierSiteResponse:
    """Retrieve a supplier site verifying both organization and supplier ownership."""
    service = SupplierSiteService(db)
    site = await service.get_site(
        context=ctx,
        supplier_id=supplier_id,
        site_id=site_id,
    )
    return SupplierSiteResponse.model_validate(site)


@router.patch(
    "/{site_id}",
    response_model=SupplierSiteResponse,
    status_code=status.HTTP_200_OK,
    operation_id="update_supplier_site",
    summary="Partially update a supplier site",
)
async def update_supplier_site(
    supplier_id: UUID,
    site_id: UUID,
    data: SupplierSiteUpdate,
    ctx: Annotated[OrganizationContext, Depends(require_roles("owner", "admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> SupplierSiteResponse:
    """Partially update supplier site attributes using strict PATCH semantics."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = SupplierSiteService(db)
    site = await service.update_site(
        context=ctx,
        supplier_id=supplier_id,
        site_id=site_id,
        data=data,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    return SupplierSiteResponse.model_validate(site)
