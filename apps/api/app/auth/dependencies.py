from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.auth.provider import DevIdentityProvider, IdentityProvider
from app.core.config import settings
from app.db.session import get_db

_dev_identity_provider: IdentityProvider = DevIdentityProvider()
_custom_identity_provider: IdentityProvider | None = None


def set_identity_provider(provider: IdentityProvider | None) -> None:
    """Explicitly register a custom identity provider (e.g. at startup in production).

    SAFETY GUARDS:
    - Rejects registering DevIdentityProvider when APP_ENV is not 'development'.
    - Prevents accidental provider reuse, misconfiguration, or test leakage.
    """
    global _custom_identity_provider
    if (
        provider is not None
        and isinstance(provider, DevIdentityProvider)
        and not settings.is_development
    ):
        raise ValueError(
            f"Cannot register DevIdentityProvider when APP_ENV='{settings.app_env}'. "
            "DevIdentityProvider is strictly restricted to development environments."
        )
    _custom_identity_provider = provider


def reset_identity_provider() -> None:
    """Reset any registered custom identity provider back to None."""
    global _custom_identity_provider
    _custom_identity_provider = None


def get_identity_provider() -> IdentityProvider:
    """Return the active identity provider instance.

    SECURITY BOUNDARIES:
    - In development (APP_ENV='development'), returns DevIdentityProvider unless
      a custom provider has been registered.
    - In production and all non-development environments, DevIdentityProvider is
      NEVER silently selected.
    - If APP_ENV != 'development' and no production provider has been configured,
      raises HTTPException (500) to prevent unauthenticated access or fallback to
      insecure dev authentication.
    - Provider selection is purely server-configured and CANNOT be influenced or
      activated by arbitrary client request headers.

    Configurable or replaceable via:
        set_identity_provider(CustomProductionProvider())
    or via FastAPI dependency overrides:
        app.dependency_overrides[get_identity_provider] = lambda: CustomProductionProvider()
    """
    if settings.is_development:
        return _custom_identity_provider or _dev_identity_provider

    if _custom_identity_provider is not None:
        if isinstance(_custom_identity_provider, DevIdentityProvider):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "DevIdentityProvider is strictly forbidden in non-development "
                    "environments."
                ),
            )
        return _custom_identity_provider

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            "Production identity provider is not configured. "
            "DevIdentityProvider is strictly disabled in non-development environments."
        ),
    )


async def get_organization_context(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[IdentityProvider, Depends(get_identity_provider)],
) -> OrganizationContext:
    """FastAPI dependency that resolves and validates the tenant OrganizationContext."""
    return await provider.resolve_context(request, db)


def require_roles(*allowed_roles: str) -> Callable[..., OrganizationContext]:
    """Dependency factory enforcing that the authenticated tenant member holds an authorized role."""

    async def role_checker(
        context: Annotated[OrganizationContext, Depends(get_organization_context)],
    ) -> OrganizationContext:
        if not context.has_role(*allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient role permissions. Required one of: {list(allowed_roles)}.",
            )
        return context

    return role_checker


# Convenient role shortcut dependencies
require_owner = require_roles("owner")
require_admin_or_owner = require_roles("owner", "admin")
require_reviewer = require_roles("owner", "admin", "reviewer")
