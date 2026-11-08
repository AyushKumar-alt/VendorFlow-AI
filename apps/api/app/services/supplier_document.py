import datetime
import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.models.audit_event import AuditEvent
from app.models.document_review import DocumentReview
from app.models.supplier_document import SupplierDocument
from app.repositories.document_requirement import DocumentRequirementRepository
from app.repositories.document_review import DocumentReviewRepository
from app.repositories.supplier_document import SupplierDocumentRepository
from app.repositories.supplier_document_group import (
    SupplierDocumentGroupRepository,
)
from app.schemas.document_review import validate_verified_data_payload

_SHA256_REGEX = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
_MAX_FILE_SIZE_BYTES = 26_214_400  # 25 MB


class SupplierDocumentDomainService:
    """Domain service managing supplier document version lineage, membership, lifecycle transitions, and effective-version rules."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.doc_repo = SupplierDocumentRepository(db)
        self.group_repo = SupplierDocumentGroupRepository(db)
        self.req_repo = DocumentRequirementRepository(db)

    async def get_document(
        self,
        document_id: UUID,
        context: OrganizationContext,
        supplier_id: UUID | None = None,
    ) -> SupplierDocument:
        """Fetch a single supplier document strictly within tenant boundaries."""
        doc = await self.doc_repo.get_by_id(
            document_id, context.organization_id, supplier_id=supplier_id
        )
        if not doc or (supplier_id is not None and doc.supplier_id != supplier_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Supplier document {document_id} not found in this organization.",
            )
        return doc

    async def get_effective_version(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        document_group_id: UUID,
    ) -> SupplierDocument | None:
        """Fetch the effective document version for a group with hierarchical ownership validation."""
        group = await self.group_repo.get_by_id(
            document_group_id, context.organization_id, supplier_id=supplier_id
        )
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document group {document_group_id} not found for supplier {supplier_id}.",
            )

        doc = await self.doc_repo.get_effective_version(
            document_group_id, context.organization_id
        )
        if doc is not None:
            assert doc.document_group_id == document_group_id
            assert doc.supplier_id == supplier_id
            assert doc.organization_id == context.organization_id
            assert doc.is_effective_version is True
        return doc

    async def list_documents_in_group(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        document_group_id: UUID,
    ) -> list[SupplierDocument]:
        """Fetch all document versions for a group ordered by version asc."""
        group = await self.group_repo.get_by_id(
            document_group_id, context.organization_id, supplier_id=supplier_id
        )
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document group {document_group_id} not found for supplier {supplier_id}.",
            )

        return await self.doc_repo.list_by_group(
            document_group_id, context.organization_id
        )

    async def list_reviews_for_document(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        document_id: UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[DocumentReview], int]:
        """Fetch paginated review history for a document version with deterministic ordering."""
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

        # Validates document exists and belongs to supplier in tenant
        await self.get_document(document_id, context, supplier_id=supplier_id)

        review_repo = DocumentReviewRepository(self.db)
        return await review_repo.list_by_document(
            document_id, context.organization_id, page=page, page_size=page_size
        )

    async def create_document_version(
        self,
        context: OrganizationContext,
        document_group_id: UUID,
        file_path: str,
        file_name: str,
        file_size_bytes: int,
        file_mime_type: str,
        file_hash_sha256: str,
        previous_version_id: UUID | None = None,
        issued_date: datetime.date | None = None,
        expiry_date: datetime.date | None = None,
        legacy_requirement_id: UUID | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> SupplierDocument:
        """Create a new document version belonging to a document group with strict lineage and fork prevention."""
        # 1. Validate parent document group exists within tenant and acquire exclusive lock
        group = await self.group_repo.get_by_id_for_update(
            document_group_id, context.organization_id
        )
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document group {document_group_id} not found in this organization.",
            )

        # 2. File validations
        if file_size_bytes <= 0 or file_size_bytes > _MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=422,
                detail=f"File size must be between 1 byte and {_MAX_FILE_SIZE_BYTES} bytes.",
            )

        clean_mime = file_mime_type.strip().lower()
        if clean_mime not in _ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid MIME type '{file_mime_type}'. Allowed: {sorted(_ALLOWED_MIME_TYPES)}.",
            )

        clean_hash = file_hash_sha256.strip().lower()
        if not _SHA256_REGEX.match(clean_hash):
            raise HTTPException(
                status_code=422,
                detail="file_hash_sha256 must be a valid 64-character lowercase hex SHA-256 hash.",
            )

        # 3. Lineage validation & fork prevention
        if previous_version_id is None:
            # Must be the root version of the group
            existing_doc = await self.doc_repo.get_latest_version_in_group(
                document_group_id, context.organization_id
            )
            if existing_doc is not None:
                raise HTTPException(
                    status_code=422,
                    detail="Document group already contains documents; new version must specify previous_version_id to maintain linear lineage.",
                )
            version = 1
        else:
            # Verify predecessor
            predecessor = await self.doc_repo.get_by_id(
                previous_version_id,
                context.organization_id,
                supplier_id=group.supplier_id,
            )
            if not predecessor:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Predecessor document {previous_version_id} not found in this organization.",
                )

            # Membership consistency: predecessor must belong to same group
            if predecessor.document_group_id != document_group_id:
                raise HTTPException(
                    status_code=422,
                    detail="Predecessor document belongs to a different document group.",
                )

            # Fork prevention: predecessor must not already have a subsequent version
            children = await self.doc_repo.get_child_versions(
                previous_version_id, context.organization_id
            )
            if len(children) > 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Lineage fork detected: predecessor version {predecessor.version} already has a subsequent version.",
                )

            version = predecessor.version + 1

        # 4. Legacy requirement validation (if provided)
        if legacy_requirement_id is not None:
            req = await self.req_repo.get_by_id(
                legacy_requirement_id, context.organization_id
            )
            if not req:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Requirement {legacy_requirement_id} not found in this organization.",
                )
            if req.document_type_id != group.document_type_id:
                raise HTTPException(
                    status_code=422,
                    detail="legacy_requirement_id document_type_id does not match document group document_type_id.",
                )

        # 5. Instantiate new document inheriting supplier_id and document_type_id from group
        doc = SupplierDocument(
            organization_id=context.organization_id,
            supplier_id=group.supplier_id,
            document_type_id=group.document_type_id,
            document_group_id=document_group_id,
            previous_version_id=previous_version_id,
            version=version,
            file_path=file_path.strip(),
            file_name=file_name.strip(),
            file_size_bytes=file_size_bytes,
            file_mime_type=clean_mime,
            file_hash_sha256=clean_hash,
            issued_date=issued_date,
            expiry_date=expiry_date,
            legacy_requirement_id=legacy_requirement_id,
            processing_status="uploaded",
            legacy_status=None,
            approval_provenance=None,
            is_effective_version=False,
        )

        try:
            created_doc = await self.doc_repo.create(doc)

            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="supplier_document",
                entity_id=created_doc.id,
                action="supplier_document.uploaded",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=None,
                new_state={
                    "document_group_id": str(document_group_id),
                    "version": version,
                    "previous_version_id": str(previous_version_id)
                    if previous_version_id
                    else None,
                    "processing_status": "uploaded",
                    "legacy_status": None,
                    "is_effective_version": False,
                },
                metadata_={
                    "document_group_id": str(document_group_id),
                    "version": version,
                },
            )
            self.db.add(audit)

            await self.db.commit()
            await self.db.refresh(created_doc)
            return created_doc
        except IntegrityError as exc:
            await self.db.rollback()
            orig = getattr(exc, "orig", None)
            sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
            constraint_name = getattr(orig, "constraint_name", None)
            err_text = str(orig) if orig else str(exc)

            version_conflict_constraints = (
                "uq_supplier_documents_group_version",
                "uq_supplier_documents_version",
                "uq_supplier_docs_previous_version",
                "uq_supplier_documents_previous_version",
            )
            is_version_conflict = (
                sqlstate == "23505" and constraint_name in version_conflict_constraints
            ) or any(c in err_text for c in version_conflict_constraints)

            if is_version_conflict:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A document version {version} or successor already exists for this document group or predecessor.",
                ) from exc
            raise
        except Exception:
            await self.db.rollback()
            raise

    async def handle_processing_failure(
        self,
        context: OrganizationContext,
        document_id: UUID,
        error_message: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> SupplierDocument:
        """Handle background processing/OCR failure without silently demoting valid effective evidence."""
        doc = await self.get_document(document_id, context)

        prev_processing_status = doc.processing_status
        doc.processing_status = "failed"
        # CRITICAL RULE: Background processing failure must NOT demote is_effective_version
        # Any existing effective version remains effective.

        try:
            await self.doc_repo.update(doc)

            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="supplier_document",
                entity_id=doc.id,
                action="supplier_document.processing_failed",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state={"processing_status": prev_processing_status},
                new_state={
                    "processing_status": "failed",
                    "error_message": error_message,
                    "is_effective_version": doc.is_effective_version,
                },
                metadata_={
                    "document_id": str(doc.id),
                    "error_message": error_message,
                },
            )
            self.db.add(audit)

            await self.db.commit()
            await self.db.refresh(doc)
            return doc
        except Exception:
            await self.db.rollback()
            raise

    async def record_review_decision(
        self,
        context: OrganizationContext,
        document_id: UUID,
        decision: str,
        comments: str | None = None,
        rejection_reason_code: str | None = None,
        verified_data: dict[str, Any] | None = None,
        supplier_id: UUID | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> SupplierDocument:
        """Record human review decision and manage single-effective-version lifecycle transitions."""
        clean_decision = decision.strip().lower()
        if clean_decision not in ("approved", "rejected", "revision_requested"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Review decision must be one of 'approved', 'rejected', 'revision_requested'.",
            )

        if (
            clean_decision in ("rejected", "revision_requested")
            and not rejection_reason_code
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"rejection_reason_code is mandatory when decision is '{clean_decision}'.",
            )
        if clean_decision == "approved" and rejection_reason_code is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="rejection_reason_code must be null when decision is 'approved'.",
            )

        clean_verified_data = validate_verified_data_payload(verified_data or {})

        # Step 1: Preliminary fetch of document to discover parent group and validate initial ownership
        prelim_doc = await self.get_document(
            document_id, context, supplier_id=supplier_id
        )
        group_id = prelim_doc.document_group_id
        if not group_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Document {document_id} does not belong to a document group.",
            )

        try:
            # Step 2: Acquire exclusive group-level row lock before reading/mutating effective state
            group = await self.group_repo.get_by_id_for_update(
                group_id, context.organization_id, supplier_id=supplier_id
            )
            if not group:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Document group {group_id} not found in this organization.",
                )

            # Step 3: Fresh re-fetch of target document after acquiring group lock
            doc = await self.doc_repo.get_by_id(
                document_id, context.organization_id, supplier_id=supplier_id
            )
            if not doc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Supplier document {document_id} not found in this organization.",
                )

            # Step 4: Revalidate supplier and group ownership post-lock
            if supplier_id is not None and doc.supplier_id != supplier_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Supplier document {document_id} does not belong to supplier {supplier_id}.",
                )
            if doc.document_group_id != group_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Supplier document {document_id} group membership has diverged.",
                )

            # Step 5: Fresh read of latest version in group after acquiring group lock
            latest_doc = await self.doc_repo.get_latest_version_in_group(
                group_id, context.organization_id
            )
            if (
                clean_decision == "approved"
                and latest_doc is not None
                and doc.version < latest_doc.version
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Cannot approve older version {doc.version}; latest version in document group is {latest_doc.version}.",
                )

            # Step 6: Fresh read of current effective version & apply transitions
            current_effective = await self.doc_repo.get_effective_version(
                group_id, context.organization_id
            )
            doc_was_effective = doc.is_effective_version
            demoted_doc: SupplierDocument | None = None

            if clean_decision == "approved":
                if current_effective and current_effective.id != doc.id:
                    demoted_doc = current_effective
                    demoted_doc.is_effective_version = False
                    await self.doc_repo.update(demoted_doc)

                doc.is_effective_version = True
                await self.doc_repo.update(doc)

            elif clean_decision in ("rejected", "revision_requested"):
                if doc.is_effective_version:
                    doc.is_effective_version = False
                    await self.doc_repo.update(doc)
                # If doc was an unapproved replacement (is_effective_version == False),
                # earlier approved effective version remains effective (zero demotion)

            # Step 7: Insert DocumentReview & atomic AuditEvent in same transaction
            review = DocumentReview(
                organization_id=context.organization_id,
                document_id=doc.id,
                reviewer_id=context.user_id,
                decision=clean_decision,
                comments=comments.strip() if comments else None,
                rejection_reason_code=rejection_reason_code,
                verified_data=clean_verified_data,
            )
            self.db.add(review)

            # Capture mathematically sound pre/post states
            if clean_decision == "approved":
                previous_state = {
                    "promoted_document": {
                        "document_id": str(doc.id),
                        "version": doc.version,
                        "is_effective_version": doc_was_effective,
                    },
                    "demoted_document": {
                        "document_id": str(demoted_doc.id),
                        "version": demoted_doc.version,
                        "is_effective_version": True,
                    }
                    if demoted_doc
                    else None,
                }
                new_state = {
                    "promoted_document": {
                        "document_id": str(doc.id),
                        "version": doc.version,
                        "is_effective_version": True,
                    },
                    "demoted_document": {
                        "document_id": str(demoted_doc.id),
                        "version": demoted_doc.version,
                        "is_effective_version": False,
                    }
                    if demoted_doc
                    else None,
                }
                metadata_ = {
                    "document_id": str(doc.id),
                    "document_group_id": str(group.id),
                    "decision": "approved",
                    "promoted_version": doc.version,
                    "demoted_document_id": str(demoted_doc.id) if demoted_doc else None,
                    "demoted_version": demoted_doc.version if demoted_doc else None,
                }
            else:
                previous_state = {
                    "document_id": str(doc.id),
                    "version": doc.version,
                    "is_effective_version": doc_was_effective,
                }
                new_state = {
                    "document_id": str(doc.id),
                    "version": doc.version,
                    "is_effective_version": doc.is_effective_version,
                    "review_decision": clean_decision,
                }
                metadata_ = {
                    "document_id": str(doc.id),
                    "document_group_id": str(group.id),
                    "decision": clean_decision,
                    "rejection_reason_code": rejection_reason_code,
                }

            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="supplier_document",
                entity_id=doc.id,
                action=f"supplier_document.{clean_decision}",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=previous_state,
                new_state=new_state,
                metadata_=metadata_,
            )
            self.db.add(audit)

            # Step 8: Atomic commit
            await self.db.commit()
            await self.db.refresh(doc)
            return doc
        except HTTPException:
            await self.db.rollback()
            raise
        except IntegrityError as exc:
            await self.db.rollback()
            orig = getattr(exc, "orig", None)
            sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
            constraint_name = getattr(orig, "constraint_name", None)
            err_text = str(orig) if orig else str(exc)

            if (
                sqlstate == "23505"
                and constraint_name == "uq_supplier_docs_effective_group"
            ) or "uq_supplier_docs_effective_group" in err_text:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A concurrent review decision has already established an effective version for this document group.",
                ) from exc
            raise
        except Exception:
            await self.db.rollback()
            raise

    async def revoke_effective_version(
        self,
        context: OrganizationContext,
        document_group_id: UUID,
        reason: str,
        supplier_id: UUID | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> SupplierDocument:
        """Explicitly revoke effectiveness from the active document version in a group."""
        clean_reason = reason.strip()
        if len(clean_reason) < 3:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Revocation reason must be at least 3 characters.",
            )

        # 1. Acquire exclusive group-level row lock before reading/mutating effective state
        group = await self.group_repo.get_by_id_for_update(
            document_group_id, context.organization_id, supplier_id=supplier_id
        )
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document group {document_group_id} not found in this organization.",
            )

        # 2. Re-read effective version under lock
        effective_doc = await self.doc_repo.get_effective_version(
            document_group_id, context.organization_id
        )
        if not effective_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No effective document version found in document group {document_group_id}.",
            )

        effective_doc.is_effective_version = False

        try:
            await self.doc_repo.update(effective_doc)

            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="supplier_document",
                entity_id=effective_doc.id,
                action="supplier_document.effective_revoked",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state={
                    "document_id": str(effective_doc.id),
                    "version": effective_doc.version,
                    "is_effective_version": True,
                },
                new_state={
                    "document_id": str(effective_doc.id),
                    "version": effective_doc.version,
                    "is_effective_version": False,
                    "revocation_reason": clean_reason,
                },
                metadata_={
                    "document_id": str(effective_doc.id),
                    "document_group_id": str(document_group_id),
                    "reason": clean_reason,
                    "revoked_version": effective_doc.version,
                },
            )
            self.db.add(audit)

            await self.db.commit()
            await self.db.refresh(effective_doc)
            return effective_doc
        except Exception:
            await self.db.rollback()
            raise
