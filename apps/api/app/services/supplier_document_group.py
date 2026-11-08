from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.models.audit_event import AuditEvent
from app.models.document_group_requirement_link import (
    DocumentGroupRequirementLink,
)
from app.models.supplier_document_group import SupplierDocumentGroup
from app.repositories.document_group_requirement_link import (
    DocumentGroupRequirementLinkRepository,
)
from app.repositories.document_requirement import DocumentRequirementRepository
from app.repositories.document_type import DocumentTypeRepository
from app.repositories.supplier import SupplierRepository
from app.repositories.supplier_document_group import (
    SupplierDocumentGroupRepository,
)
from app.repositories.supplier_site import SupplierSiteRepository


class SupplierDocumentGroupService:
    """Domain service managing supplier document groups, tenant validation, identity immutability, and requirement links."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.group_repo = SupplierDocumentGroupRepository(db)
        self.link_repo = DocumentGroupRequirementLinkRepository(db)
        self.supplier_repo = SupplierRepository(db)
        self.doc_type_repo = DocumentTypeRepository(db)
        self.site_repo = SupplierSiteRepository(db)
        self.req_repo = DocumentRequirementRepository(db)

    async def get_group(
        self,
        group_id: UUID,
        context: OrganizationContext,
        supplier_id: UUID | None = None,
    ) -> SupplierDocumentGroup:
        """Fetch a single document group strictly within tenant boundaries."""
        group = await self.group_repo.get_by_id(
            group_id, context.organization_id, supplier_id=supplier_id
        )
        if not group or (supplier_id is not None and group.supplier_id != supplier_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document group {group_id} not found in this organization.",
            )
        return group

    async def list_groups(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        document_type_id: UUID | None = None,
        site_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SupplierDocumentGroup], int]:
        """Fetch paginated document groups for a supplier within the tenant boundary."""
        if page < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="page must be greater than or equal to 1.",
            )
        if page_size < 1 or page_size > 100:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="page_size must be between 1 and 100.",
            )

        # Validate supplier exists in tenant
        supplier = await self.supplier_repo.get_by_id(
            supplier_id, context.organization_id
        )
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Supplier {supplier_id} not found in this organization.",
            )

        if site_id is not None:
            site = await self.site_repo.get_by_id(
                site_id, context.organization_id, supplier_id=supplier_id
            )
            if not site:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Site {site_id} not found for supplier {supplier_id}.",
                )

        if document_type_id is not None:
            doc_type = await self.doc_type_repo.get_by_id(
                document_type_id, context.organization_id
            )
            if not doc_type:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Document type {document_type_id} not found in this organization.",
                )

        return await self.group_repo.list_by_supplier_paginated(
            supplier_id=supplier_id,
            organization_id=context.organization_id,
            document_type_id=document_type_id,
            site_id=site_id,
            page=page,
            page_size=page_size,
        )

    async def list_requirement_links(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        group_id: UUID,
    ) -> list[DocumentGroupRequirementLink]:
        """Fetch all requirement links for a document group within tenant boundaries."""
        group = await self.get_group(group_id, context, supplier_id=supplier_id)
        return await self.link_repo.list_by_group(group.id, context.organization_id)

    async def create_group(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        document_type_id: UUID,
        coverage_scope: str = "supplier",
        site_id: UUID | None = None,
        document_reference: str | None = None,
        display_name: str | None = None,
        issuing_authority: str | None = None,
        requirement_ids: list[UUID] | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> SupplierDocumentGroup:
        """Create a supplier document group and optional requirement links within tenant boundaries."""
        # 1. Validate supplier exists in tenant
        supplier = await self.supplier_repo.get_by_id(
            supplier_id, context.organization_id
        )
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Supplier {supplier_id} not found in this organization.",
            )

        # 2. Validate document type exists in tenant
        doc_type = await self.doc_type_repo.get_by_id(
            document_type_id, context.organization_id
        )
        if not doc_type:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document type {document_type_id} not found in this organization.",
            )

        # 3. Validate coverage scope & site
        clean_scope = coverage_scope.strip().lower()
        if clean_scope not in ("supplier", "site"):
            raise HTTPException(
                status_code=422,
                detail="Coverage scope must be either 'supplier' or 'site'.",
            )

        if clean_scope == "supplier":
            if site_id is not None:
                raise HTTPException(
                    status_code=422,
                    detail="site_id must be null for supplier-scoped document groups.",
                )
        else:
            if site_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="site_id is required for site-scoped document groups.",
                )
            site = await self.site_repo.get_by_id(
                site_id, context.organization_id, supplier_id=supplier_id
            )
            if not site:
                other_site = await self.site_repo.get_by_id(
                    site_id, context.organization_id
                )
                if other_site:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Site {site_id} belongs to a different supplier.",
                    )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Site {site_id} not found in this organization.",
                )
            if site.supplier_id != supplier_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"Site {site_id} belongs to a different supplier.",
                )

        # 4. Validate document reference uniqueness if provided
        clean_ref = document_reference.strip() if document_reference else None
        if clean_ref:
            if clean_scope == "supplier":
                existing_ref = await self.group_repo.get_by_supplier_ref(
                    supplier_id=supplier_id,
                    document_type_id=document_type_id,
                    document_reference=clean_ref,
                    organization_id=context.organization_id,
                )
            else:
                assert site_id is not None
                existing_ref = await self.group_repo.get_by_site_ref(
                    supplier_id=supplier_id,
                    document_type_id=document_type_id,
                    site_id=site_id,
                    document_reference=clean_ref,
                    organization_id=context.organization_id,
                )
            if existing_ref:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Document group with reference '{clean_ref}' already exists for this scope.",
                )

        # 5. Determine display name
        resolved_display_name = display_name.strip() if display_name else doc_type.name

        # 6. Instantiate document group
        group = SupplierDocumentGroup(
            organization_id=context.organization_id,
            supplier_id=supplier_id,
            document_type_id=document_type_id,
            site_id=site_id,
            coverage_scope=clean_scope,
            document_reference=clean_ref,
            display_name=resolved_display_name,
            issuing_authority=issuing_authority.strip() if issuing_authority else None,
        )

        try:
            created_group = await self.group_repo.create(group)

            # 7. Add initial requirement links if requested
            if requirement_ids:
                seen_reqs: set[UUID] = set()
                for req_id in requirement_ids:
                    if req_id in seen_reqs:
                        continue
                    seen_reqs.add(req_id)
                    req = await self.req_repo.get_by_id(req_id, context.organization_id)
                    if not req:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Requirement {req_id} not found in this organization.",
                        )
                    if req.document_type_id != document_type_id:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Requirement {req_id} document_type_id does not match group document_type_id.",
                        )
                    link = DocumentGroupRequirementLink(
                        organization_id=context.organization_id,
                        document_group_id=created_group.id,
                        requirement_id=req_id,
                        link_origin="manual",
                    )
                    await self.link_repo.create(link)

            # 8. Emit audit event
            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="supplier_document_group",
                entity_id=created_group.id,
                action="supplier_document_group.created",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=None,
                new_state={
                    "supplier_id": str(supplier_id),
                    "document_type_id": str(document_type_id),
                    "coverage_scope": clean_scope,
                    "site_id": str(site_id) if site_id else None,
                    "document_reference": clean_ref,
                    "display_name": resolved_display_name,
                },
                metadata_={
                    "supplier_id": str(supplier_id),
                    "document_type_id": str(document_type_id),
                    "coverage_scope": clean_scope,
                },
            )
            self.db.add(audit)

            await self.db.commit()
            await self.db.refresh(created_group)
            return created_group

        except IntegrityError as exc:
            await self.db.rollback()
            err_text = str(exc.orig) if hasattr(exc, "orig") else str(exc)
            if (
                "uq_supplier_doc_groups_site_ref" in err_text
                or "uq_supplier_doc_groups_supplier_ref" in err_text
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Document group with reference '{clean_ref}' already exists for this scope.",
                ) from exc
            raise
        except Exception:
            await self.db.rollback()
            raise

    async def add_requirement_link(
        self,
        context: OrganizationContext,
        group_id: UUID,
        requirement_id: UUID,
        link_origin: str = "manual",
        supplier_id: UUID | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> DocumentGroupRequirementLink:
        """Add a requirement link to a document group within tenant boundaries."""
        group = await self.get_group(group_id, context, supplier_id=supplier_id)

        # Validate requirement exists in tenant
        req = await self.req_repo.get_by_id(requirement_id, context.organization_id)
        if not req:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Requirement {requirement_id} not found in this organization.",
            )

        # Validate matching document type
        if req.document_type_id != group.document_type_id:
            raise HTTPException(
                status_code=422,
                detail=f"Requirement {requirement_id} document_type_id does not match group document_type_id.",
            )

        # Validate duplicate link
        existing = await self.link_repo.get_by_group_and_requirement(
            group_id, requirement_id, context.organization_id
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Requirement {requirement_id} is already linked to this document group.",
            )

        clean_origin = link_origin.strip().lower()
        if clean_origin not in ("manual", "policy_engine"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="link_origin must be 'manual' or 'policy_engine'. 'legacy_backfill' is migration-controlled.",
            )

        link = DocumentGroupRequirementLink(
            organization_id=context.organization_id,
            document_group_id=group_id,
            requirement_id=requirement_id,
            link_origin=clean_origin,
        )

        try:
            created_link = await self.link_repo.create(link)

            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="document_group_requirement_link",
                entity_id=created_link.id,
                action="document_group_requirement_link.added",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=None,
                new_state={
                    "document_group_id": str(group_id),
                    "requirement_id": str(requirement_id),
                    "link_origin": clean_origin,
                },
                metadata_={
                    "document_group_id": str(group_id),
                    "requirement_id": str(requirement_id),
                },
            )
            self.db.add(audit)

            await self.db.commit()
            await self.db.refresh(created_link)
            return created_link
        except IntegrityError as exc:
            await self.db.rollback()
            orig = getattr(exc, "orig", None)
            sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
            constraint_name = getattr(orig, "constraint_name", None)
            err_text = str(orig) if orig else str(exc)

            is_duplicate_link = (
                sqlstate == "23505"
                and constraint_name == "uq_group_req_links_group_req"
            ) or "uq_group_req_links_group_req" in err_text

            if is_duplicate_link:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Requirement {requirement_id} is already linked to this document group.",
                ) from exc
            raise
        except Exception:
            await self.db.rollback()
            raise

    async def remove_requirement_link(
        self,
        context: OrganizationContext,
        group_id: UUID,
        requirement_id: UUID,
        supplier_id: UUID | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Remove a requirement link from a document group within tenant boundaries."""
        await self.get_group(group_id, context, supplier_id=supplier_id)

        link = await self.link_repo.get_by_group_and_requirement(
            group_id, requirement_id, context.organization_id
        )
        if not link:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Requirement link between group {group_id} and requirement {requirement_id} not found.",
            )

        try:
            link_id = link.id
            await self.link_repo.delete(link)

            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="document_group_requirement_link",
                entity_id=link_id,
                action="document_group_requirement_link.removed",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state={
                    "document_group_id": str(group_id),
                    "requirement_id": str(requirement_id),
                    "link_origin": link.link_origin,
                },
                new_state=None,
                metadata_={
                    "document_group_id": str(group_id),
                    "requirement_id": str(requirement_id),
                },
            )
            self.db.add(audit)

            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
