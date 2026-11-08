import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.models.audit_event import AuditEvent
from app.models.supplier_site import SupplierSite
from app.repositories.supplier import SupplierRepository
from app.repositories.supplier_site import SupplierSiteRepository
from app.schemas.supplier_site import SupplierSiteUpdate

_GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")
_COUNTRY_REGEX = re.compile(r"^[A-Z]{2}$")


class SupplierSiteService:
    """Domain service managing supplier physical and operational sites."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.site_repo = SupplierSiteRepository(db)
        self.supplier_repo = SupplierRepository(db)

    async def create_site(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        site_code: str,
        site_name: str,
        address_line1: str | None = None,
        address_line2: str | None = None,
        city: str | None = None,
        state: str | None = None,
        postal_code: str | None = None,
        country_code: str = "IN",
        gstin: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> SupplierSite:
        """Create a new site for a supplier within the tenant boundary."""
        # 1. Verify supplier exists within tenant
        supplier = await self.supplier_repo.get_by_id(
            supplier_id, context.organization_id
        )
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Supplier {supplier_id} not found in this organization.",
            )

        # 2. Format validations
        clean_country = country_code.strip().upper()
        if not _COUNTRY_REGEX.match(clean_country):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Country code must be exactly 2 uppercase letters (ISO 3166-1 alpha-2).",
            )

        clean_gstin = gstin.strip().upper() if gstin else None
        if clean_gstin and not _GSTIN_REGEX.match(clean_gstin):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid GSTIN format: '{gstin}'.",
            )

        clean_code = site_code.strip()
        clean_name = site_name.strip()
        if not clean_code or not clean_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Site code and site name must not be empty.",
            )

        # 3. Verify uniqueness of site_code for this supplier in this organization
        existing_site = await self.site_repo.get_by_code(
            supplier_id, clean_code, context.organization_id
        )
        if existing_site:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Site with code '{clean_code}' already exists for this supplier.",
            )

        site = SupplierSite(
            organization_id=context.organization_id,
            supplier_id=supplier_id,
            site_code=clean_code,
            site_name=clean_name,
            address_line1=address_line1,
            address_line2=address_line2,
            city=city,
            state=state,
            postal_code=postal_code,
            country_code=clean_country,
            gstin=clean_gstin,
            is_active=True,
        )

        try:
            created_site = await self.site_repo.create(site)

            # Atomic audit event emission
            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="supplier_site",
                entity_id=created_site.id,
                action="supplier_site.created",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=None,
                new_state={
                    "site_code": created_site.site_code,
                    "site_name": created_site.site_name,
                    "supplier_id": str(supplier_id),
                    "country_code": created_site.country_code,
                    "is_active": created_site.is_active,
                },
                metadata_={
                    "supplier_id": str(supplier_id),
                    "site_code": created_site.site_code,
                },
            )
            self.db.add(audit)

            await self.db.commit()
            await self.db.refresh(created_site)
            return created_site

        except IntegrityError as exc:
            await self.db.rollback()
            err_text = str(exc.orig) if hasattr(exc, "orig") else str(exc)
            if "uq_supplier_sites_org_code" in err_text:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Site with code '{clean_code}' already exists for this supplier.",
                ) from exc
            raise
        except Exception:
            await self.db.rollback()
            raise

    async def get_site(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        site_id: UUID,
    ) -> SupplierSite:
        """Fetch a single site for a supplier within the tenant boundary."""
        supplier = await self.supplier_repo.get_by_id(
            supplier_id, context.organization_id
        )
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Supplier {supplier_id} not found in this organization.",
            )

        site = await self.site_repo.get_by_id(
            site_id, context.organization_id, supplier_id=supplier_id
        )
        if not site or site.supplier_id != supplier_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Site {site_id} not found for supplier {supplier_id}.",
            )
        return site

    async def list_sites(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        is_active: bool | None = None,
    ) -> list[SupplierSite]:
        """List all sites for a supplier within the tenant boundary."""
        supplier = await self.supplier_repo.get_by_id(
            supplier_id, context.organization_id
        )
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Supplier {supplier_id} not found in this organization.",
            )
        return await self.site_repo.list_by_supplier(
            supplier_id, context.organization_id, is_active=is_active
        )

    async def update_site(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        site_id: UUID,
        data: SupplierSiteUpdate,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> SupplierSite:
        """Update site details using strict PATCH semantics."""
        site = await self.get_site(context, supplier_id, site_id)

        if not data.model_fields_set:
            return site

        previous_state: dict[str, Any] = {}
        new_state: dict[str, Any] = {}

        # 1. Non-nullable updates (explicit null already rejected by schema validator)
        if "site_name" in data.model_fields_set:
            assert data.site_name is not None
            previous_state["site_name"] = site.site_name
            site.site_name = data.site_name.strip()
            new_state["site_name"] = site.site_name

        if "country_code" in data.model_fields_set:
            assert data.country_code is not None
            clean_country = data.country_code.strip().upper()
            if not _COUNTRY_REGEX.match(clean_country):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Country code must be exactly 2 uppercase letters (ISO 3166-1 alpha-2).",
                )
            previous_state["country_code"] = site.country_code
            site.country_code = clean_country
            new_state["country_code"] = site.country_code

        if "is_active" in data.model_fields_set:
            assert data.is_active is not None
            previous_state["is_active"] = site.is_active
            site.is_active = data.is_active
            new_state["is_active"] = site.is_active

        # 2. Clearable nullable updates
        clearable_fields = (
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "gstin",
        )
        for field in clearable_fields:
            if field in data.model_fields_set:
                raw_val = getattr(data, field)
                val: str | None = (
                    raw_val.strip() if isinstance(raw_val, str) else raw_val
                )
                if field == "gstin" and val:
                    val = val.upper()
                    if not _GSTIN_REGEX.match(val):
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Invalid GSTIN format: '{raw_val}'.",
                        )
                previous_state[field] = getattr(site, field)
                setattr(site, field, val)
                new_state[field] = val

        try:
            await self.site_repo.update(site)

            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="supplier_site",
                entity_id=site.id,
                action="supplier_site.updated",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=previous_state,
                new_state=new_state,
                metadata_={
                    "supplier_id": str(supplier_id),
                    "site_code": site.site_code,
                    "updated_fields": sorted(data.model_fields_set),
                },
            )
            self.db.add(audit)

            await self.db.commit()
            await self.db.refresh(site)
            return site
        except Exception:
            await self.db.rollback()
            raise
