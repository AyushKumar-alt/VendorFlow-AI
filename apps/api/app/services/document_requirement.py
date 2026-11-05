from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.models.audit_event import AuditEvent
from app.models.document_requirement import DocumentRequirement
from app.repositories.document_requirement import DocumentRequirementRepository
from app.repositories.document_type import DocumentTypeRepository
from app.repositories.supplier import SupplierRepository
from app.schemas.document_requirement import (
    DocumentRequirementCreate,
    DocumentRequirementListResponse,
    DocumentRequirementResponse,
    DocumentRequirementUpdate,
    SupplierRequirementItem,
    SupplierRequirementsEvaluationResponse,
)


class DocumentRequirementService:
    """Business service orchestrating organization document requirements, validation, and evaluation."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = DocumentRequirementRepository(db)
        self.doc_type_repo = DocumentTypeRepository(db)
        self.supplier_repo = SupplierRepository(db)

    async def create_requirement(
        self,
        context: OrganizationContext,
        data: DocumentRequirementCreate,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> DocumentRequirement:
        """Create a new requirement ensuring document_type belongs strictly to caller's organization."""
        # 1. Verify document_type belongs to caller's organization
        doc_type = await self.doc_type_repo.get_by_id(
            data.document_type_id, context.organization_id
        )
        if not doc_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document type not found or belongs to another organization.",
            )

        # 2. Prevent duplicate requirement mappings within the tenant
        existing = await self.repo.find_existing(
            organization_id=context.organization_id,
            document_type_id=data.document_type_id,
            supplier_category=data.supplier_category,
            supplier_tier=data.supplier_tier,
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A document requirement for this document type, category, and tier already exists in this organization.",
            )

        # 3. Instantiate model binding organization_id strictly from OrganizationContext
        req_data = data.model_dump()
        requirement = DocumentRequirement(
            organization_id=context.organization_id,
            **req_data,
        )

        try:
            created = await self.repo.create(requirement)

            # 4. Create audit event atomically within the same transaction
            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="document_requirement",
                entity_id=created.id,
                action="document_requirement.created",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=None,
                new_state={
                    "name": created.name,
                    "document_type_id": str(created.document_type_id),
                    "supplier_category": created.supplier_category,
                    "supplier_tier": created.supplier_tier,
                    "is_mandatory": created.is_mandatory,
                    "is_active": created.is_active,
                },
                metadata_={
                    "document_type_code": doc_type.code,
                },
            )
            self.db.add(audit)

            await self.db.commit()
            refreshed = await self.repo.get_by_id(created.id, context.organization_id)
            return refreshed or created

        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Database integrity error while creating document requirement.",
            ) from exc

    async def get_requirement(
        self, context: OrganizationContext, requirement_id: UUID
    ) -> DocumentRequirement:
        """Fetch a single requirement strictly within the caller's organization."""
        req = await self.repo.get_by_id(requirement_id, context.organization_id)
        if not req:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document requirement not found.",
            )
        return req

    async def list_requirements(
        self,
        context: OrganizationContext,
        document_type_id: UUID | None = None,
        supplier_category: str | None = None,
        supplier_tier: str | None = None,
        is_active: bool | None = None,
        is_mandatory: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> DocumentRequirementListResponse:
        """Fetch paginated document requirements for the caller's organization."""
        items, total = await self.repo.list_requirements(
            organization_id=context.organization_id,
            document_type_id=document_type_id,
            supplier_category=supplier_category,
            supplier_tier=supplier_tier,
            is_active=is_active,
            is_mandatory=is_mandatory,
            page=page,
            page_size=page_size,
        )
        response_items = [
            DocumentRequirementResponse.model_validate(item) for item in items
        ]
        return DocumentRequirementListResponse.create(
            items=response_items,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def update_requirement(
        self,
        context: OrganizationContext,
        requirement_id: UUID,
        data: DocumentRequirementUpdate,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> DocumentRequirement:
        """Partially update a document requirement within the caller's organization."""
        req = await self.get_requirement(context, requirement_id)

        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return req

        # Check for duplicate if category or tier is modified
        new_category = update_data.get("supplier_category", req.supplier_category)
        new_tier = update_data.get("supplier_tier", req.supplier_tier)
        if new_category != req.supplier_category or new_tier != req.supplier_tier:
            existing = await self.repo.find_existing(
                organization_id=context.organization_id,
                document_type_id=req.document_type_id,
                supplier_category=new_category,
                supplier_tier=new_tier,
            )
            if existing and existing.id != req.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A document requirement for this document type, category, and tier already exists in this organization.",
                )

        previous_state = {
            "name": req.name,
            "supplier_category": req.supplier_category,
            "supplier_tier": req.supplier_tier,
            "is_mandatory": req.is_mandatory,
            "validity_period_days": req.validity_period_days,
            "expiry_lead_notice_days": req.expiry_lead_notice_days,
            "is_active": req.is_active,
            "validation_rules": req.validation_rules,
        }

        for field, value in update_data.items():
            setattr(req, field, value)

        new_state = {
            "name": req.name,
            "supplier_category": req.supplier_category,
            "supplier_tier": req.supplier_tier,
            "is_mandatory": req.is_mandatory,
            "validity_period_days": req.validity_period_days,
            "expiry_lead_notice_days": req.expiry_lead_notice_days,
            "is_active": req.is_active,
            "validation_rules": req.validation_rules,
        }

        try:
            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="document_requirement",
                entity_id=req.id,
                action="document_requirement.updated",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=previous_state,
                new_state=new_state,
                metadata_={
                    "updated_fields": list(update_data.keys()),
                },
            )
            self.db.add(audit)

            await self.db.commit()
            refreshed = await self.repo.get_by_id(req.id, context.organization_id)
            return refreshed or req

        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Database integrity error while updating document requirement.",
            ) from exc

    async def set_requirement_status(
        self,
        context: OrganizationContext,
        requirement_id: UUID,
        is_active: bool,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> DocumentRequirement:
        """Enable or disable a document requirement within the caller's organization."""
        req = await self.get_requirement(context, requirement_id)
        if req.is_active == is_active:
            return req

        previous_state = {"is_active": req.is_active}
        req.is_active = is_active
        new_state = {"is_active": is_active}

        action = (
            "document_requirement.enabled"
            if is_active
            else "document_requirement.disabled"
        )

        try:
            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="document_requirement",
                entity_id=req.id,
                action=action,
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=previous_state,
                new_state=new_state,
                metadata_={},
            )
            self.db.add(audit)

            await self.db.commit()
            refreshed = await self.repo.get_by_id(req.id, context.organization_id)
            return refreshed or req

        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Database integrity error while updating requirement status.",
            ) from exc

    async def evaluate_supplier_requirements(
        self, context: OrganizationContext, supplier_id: UUID
    ) -> SupplierRequirementsEvaluationResponse:
        """Evaluate applicable document requirements for a supplier within the tenant."""
        # 1. Fetch supplier strictly within caller's organization
        supplier = await self.supplier_repo.get_by_id(
            supplier_id, context.organization_id
        )
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Supplier not found.",
            )

        # 2. Fetch all active requirements matching supplier category and tier
        applicable = await self.repo.get_applicable_for_supplier(
            organization_id=context.organization_id,
            supplier_category=supplier.category,
            supplier_tier=supplier.tier,
        )

        mandatory_items: list[SupplierRequirementItem] = []
        optional_items: list[SupplierRequirementItem] = []

        for req in applicable:
            doc_type = req.document_type
            item = SupplierRequirementItem(
                requirement_id=req.id,
                document_type_id=req.document_type_id,
                name=req.name,
                is_mandatory=req.is_mandatory,
                validity_period_days=req.validity_period_days,
                expiry_lead_notice_days=req.expiry_lead_notice_days,
                supplier_category=req.supplier_category,
                supplier_tier=req.supplier_tier,
                validation_rules=req.validation_rules,
                document_type_code=doc_type.code if doc_type else "",
                document_type_name=doc_type.name if doc_type else "",
                document_type_category=doc_type.category if doc_type else "",
                has_expiry=doc_type.has_expiry if doc_type else False,
                expected_fields=doc_type.expected_fields if doc_type else [],
            )

            if req.is_mandatory:
                mandatory_items.append(item)
            else:
                optional_items.append(item)

        return SupplierRequirementsEvaluationResponse(
            supplier_id=supplier.id,
            supplier_name=supplier.legal_name,
            supplier_category=supplier.category,
            supplier_tier=supplier.tier,
            total_applicable=len(applicable),
            total_mandatory=len(mandatory_items),
            total_optional=len(optional_items),
            mandatory_requirements=mandatory_items,
            optional_requirements=optional_items,
        )
