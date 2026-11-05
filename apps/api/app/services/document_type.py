from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.models.audit_event import AuditEvent
from app.models.document_type import DocumentType
from app.repositories.document_type import DocumentTypeRepository
from app.schemas.document_type import (
    DocumentTypeCreate,
    DocumentTypeListResponse,
    DocumentTypeResponse,
    DocumentTypeUpdate,
)


class DocumentTypeService:
    """Business service orchestrating document type operations, tenant validation, and audit logging."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = DocumentTypeRepository(db)

    async def create_document_type(
        self,
        context: OrganizationContext,
        data: DocumentTypeCreate,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> DocumentType:
        """Create a new document type within the caller's organization boundary and emit an audit event."""
        # 1. Proactive uniqueness check for code within tenant
        existing = await self.repo.get_by_code(data.code, context.organization_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Document type with code '{data.code}' already exists in this organization.",
            )

        # 2. Instantiate model binding organization_id strictly from OrganizationContext
        doc_type_data = data.model_dump()
        doc_type = DocumentType(
            organization_id=context.organization_id,
            is_system_preset=False,
            **doc_type_data,
        )

        try:
            created = await self.repo.create(doc_type)

            # 3. Create audit event atomically within the same transaction
            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="document_type",
                entity_id=created.id,
                action="document_type.created",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=None,
                new_state={
                    "code": created.code,
                    "name": created.name,
                    "category": created.category,
                    "has_expiry": created.has_expiry,
                },
                metadata_={
                    "code": created.code,
                    "is_system_preset": created.is_system_preset,
                },
            )
            self.db.add(audit)

            await self.db.commit()
            await self.db.refresh(created)
            return created

        except IntegrityError as exc:
            await self.db.rollback()
            err_text = str(exc.orig) if hasattr(exc, "orig") else str(exc)
            if "uq_document_types_org_code" in err_text:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Document type with code '{data.code}' already exists in this organization.",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Database integrity error while creating document type.",
            ) from exc

    async def get_document_type(
        self, context: OrganizationContext, document_type_id: UUID
    ) -> DocumentType:
        """Fetch a single document type strictly within the caller's organization."""
        doc_type = await self.repo.get_by_id(document_type_id, context.organization_id)
        if not doc_type:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document type not found.",
            )
        return doc_type

    async def list_document_types(
        self,
        context: OrganizationContext,
        category: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> DocumentTypeListResponse:
        """Fetch paginated document types for the caller's organization."""
        items, total = await self.repo.list_document_types(
            organization_id=context.organization_id,
            category=category,
            search=search,
            page=page,
            page_size=page_size,
        )
        response_items = [DocumentTypeResponse.model_validate(item) for item in items]
        return DocumentTypeListResponse.create(
            items=response_items,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def update_document_type(
        self,
        context: OrganizationContext,
        document_type_id: UUID,
        data: DocumentTypeUpdate,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> DocumentType:
        """Partially update a document type within the caller's organization."""
        doc_type = await self.get_document_type(context, document_type_id)

        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return doc_type

        previous_state = {
            "name": doc_type.name,
            "description": doc_type.description,
            "category": doc_type.category,
            "has_expiry": doc_type.has_expiry,
            "expected_fields": doc_type.expected_fields,
        }

        for field, value in update_data.items():
            setattr(doc_type, field, value)

        new_state = {
            "name": doc_type.name,
            "description": doc_type.description,
            "category": doc_type.category,
            "has_expiry": doc_type.has_expiry,
            "expected_fields": doc_type.expected_fields,
        }

        try:
            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="document_type",
                entity_id=doc_type.id,
                action="document_type.updated",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=previous_state,
                new_state=new_state,
                metadata_={
                    "code": doc_type.code,
                    "updated_fields": list(update_data.keys()),
                },
            )
            self.db.add(audit)

            await self.db.commit()
            await self.db.refresh(doc_type)
            return doc_type

        except IntegrityError as exc:
            await self.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Database integrity error while updating document type.",
            ) from exc
