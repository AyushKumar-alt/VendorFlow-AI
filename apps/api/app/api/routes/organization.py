from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.context import OrganizationContext
from app.auth.dependencies import (
    get_organization_context,
    require_admin_or_owner,
    require_reviewer,
)

router = APIRouter(prefix="/organization", tags=["organization"])


@router.get("/context")
async def get_current_context(
    ctx: Annotated[OrganizationContext, Depends(get_organization_context)],
) -> dict[str, str | bool]:
    """Retrieve the resolved tenant organization context for the authenticated caller."""
    return {
        "user_id": str(ctx.user_id),
        "organization_id": str(ctx.organization_id),
        "membership_id": str(ctx.membership_id),
        "role": ctx.role,
        "user_email": ctx.user_email,
        "user_full_name": ctx.user_full_name,
        "organization_name": ctx.organization_name,
        "organization_slug": ctx.organization_slug,
        "is_superuser": ctx.is_superuser,
    }


@router.get("/admin-only")
async def get_admin_data(
    ctx: Annotated[OrganizationContext, Depends(require_admin_or_owner)],
) -> dict[str, str]:
    """Endpoint accessible only by administrators and organization owners."""
    return {
        "message": "Authorized for admin/owner.",
        "organization_id": str(ctx.organization_id),
        "role": ctx.role,
    }


@router.get("/reviewer-only")
async def get_reviewer_data(
    ctx: Annotated[OrganizationContext, Depends(require_reviewer)],
) -> dict[str, str]:
    """Endpoint accessible by reviewers, administrators, and organization owners."""
    return {
        "message": "Authorized for reviewer/admin/owner.",
        "organization_id": str(ctx.organization_id),
        "role": ctx.role,
    }
