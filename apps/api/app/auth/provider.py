import uuid
from typing import Protocol

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.core.config import settings
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User


class IdentityProvider(Protocol):
    """Protocol defining the interface for tenant and actor identity resolution.

    Production implementations (e.g. JWT Bearer / OAuth2 / OIDC / Session) must
    implement this method to parse cryptographic tokens, validate tenant membership,
    and construct the OrganizationContext.
    """

    async def resolve_context(
        self,
        request: Request,
        db: AsyncSession,
    ) -> OrganizationContext:
        """Resolve and validate the tenant and user context from the incoming request.

        Raises:
            HTTPException: With appropriate HTTP status code if the identity,
                           tenant, or membership is missing, invalid, or inactive.
        """
        ...


class DevIdentityProvider:
    """Development and testing identity provider.

    SECURITY ARCHITECTURE & BOUNDARIES:
    - This identity provider is STRICTLY permitted when APP_ENV=development.
    - It NEVER operates in production, staging, or other non-development environments.
    - Cannot be activated through arbitrary request headers (e.g. X-Dev-Mode, X-Env).
    - Resolves actor identity using development request headers ('X-Dev-User-Id' / 'X-User-Id')
      and tenant context ('X-Dev-Organization-Id' / 'X-Organization-Id' / 'X-Organization-Slug').
    - NEVER trusts client-supplied organization ID as proof of membership.
    - Always looks up user, organization, and membership in the database.
    - Strictly rejects missing, inactive, or mismatched identities.
    - NEVER creates users or organizations from request headers.
    - NEVER silently falls back to a default organization or user.
    - In production, this component must be replaced by a cryptographic token provider.
    """

    USER_ID_HEADERS: tuple[str, ...] = ("X-Dev-User-Id", "X-User-Id")
    ORG_ID_HEADERS: tuple[str, ...] = ("X-Dev-Organization-Id", "X-Organization-Id")
    ORG_SLUG_HEADERS: tuple[str, ...] = (
        "X-Dev-Organization-Slug",
        "X-Organization-Slug",
    )

    async def resolve_context(
        self,
        request: Request,
        db: AsyncSession,
    ) -> OrganizationContext:
        # 0. Environment guard: DevIdentityProvider can only operate when APP_ENV=development
        if not settings.is_development:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "DevIdentityProvider is only permitted when APP_ENV=development. "
                    f"It is disabled in '{settings.app_env}' environment."
                ),
            )

        # 1. Extract user identifier from headers
        user_identifier = self._get_first_header(request, self.USER_ID_HEADERS)
        if not user_identifier:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authentication credentials. Provide 'X-Dev-User-Id' header.",
            )

        # 2. Extract organization identifier from headers
        org_id_raw = self._get_first_header(request, self.ORG_ID_HEADERS)
        org_slug_raw = self._get_first_header(request, self.ORG_SLUG_HEADERS)

        if not org_id_raw and not org_slug_raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Missing organization context. Provide 'X-Dev-Organization-Id' "
                    "or 'X-Organization-Slug' header."
                ),
            )

        # 3. Resolve and validate user from database
        user = await self._resolve_user(db, user_identifier)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found.",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive.",
            )

        # 4. Resolve and validate organization from database
        org = await self._resolve_organization(db, org_id_raw, org_slug_raw)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found.",
            )
        if org.status != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Organization is not active (status: {org.status}).",
            )

        # 5. Resolve and verify membership (tenant authorization)
        membership = await self._resolve_membership(db, user_id=user.id, org_id=org.id)
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not a member of the requested organization.",
            )
        if not membership.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User membership in the requested organization is inactive.",
            )

        # 6. Build and return immutable context
        return OrganizationContext(
            user_id=user.id,
            organization_id=org.id,
            membership_id=membership.id,
            role=membership.role,
            user_email=user.email,
            user_full_name=user.full_name,
            is_superuser=user.is_superuser,
            organization_name=org.name,
            organization_slug=org.slug,
        )

    def _get_first_header(
        self, request: Request, header_names: tuple[str, ...]
    ) -> str | None:
        for name in header_names:
            value = request.headers.get(name)
            if value is not None and value.strip():
                return value.strip()
        return None

    async def _resolve_user(self, db: AsyncSession, identifier: str) -> User | None:
        try:
            user_uuid = uuid.UUID(identifier)
            stmt = select(User).where(User.id == user_uuid)
        except ValueError:
            stmt = select(User).where(User.email == identifier.lower())
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _resolve_organization(
        self,
        db: AsyncSession,
        org_id_raw: str | None,
        slug_raw: str | None,
    ) -> Organization | None:
        if org_id_raw:
            try:
                org_uuid = uuid.UUID(org_id_raw)
                stmt = select(Organization).where(Organization.id == org_uuid)
                result = await db.execute(stmt)
                org = result.scalar_one_or_none()
                if org:
                    return org
            except ValueError:
                pass
        slug_candidate = slug_raw or org_id_raw
        if slug_candidate:
            stmt = select(Organization).where(Organization.slug == slug_candidate)
            result = await db.execute(stmt)
            return result.scalar_one_or_none()
        return None

    async def _resolve_membership(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> Membership | None:
        stmt = select(Membership).where(
            Membership.user_id == user_id,
            Membership.organization_id == org_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
