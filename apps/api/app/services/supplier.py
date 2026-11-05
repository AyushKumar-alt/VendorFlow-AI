from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import OrganizationContext
from app.models.audit_event import AuditEvent
from app.models.supplier import Supplier
from app.repositories.supplier import SupplierRepository
from app.schemas.supplier import SupplierCreate, SupplierUpdate


class SupplierService:
    """Business service orchestrating supplier domain logic, transactions, and audit records."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = SupplierRepository(db)

    async def create_supplier(
        self,
        context: OrganizationContext,
        data: SupplierCreate,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> Supplier:
        """Create a new supplier within the caller's organization boundary and emit an audit event."""
        # 1. Proactive uniqueness validation within this tenant
        if data.vendor_code:
            existing_vc = await self.repo.get_by_vendor_code(
                data.vendor_code, context.organization_id
            )
            if existing_vc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Supplier with vendor code '{data.vendor_code}' already exists in this organization.",
                )

        if data.gstin:
            existing_gstin = await self.repo.get_by_gstin(
                data.gstin, context.organization_id
            )
            if existing_gstin:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Supplier with GSTIN '{data.gstin}' already exists in this organization.",
                )

        # 2. Instantiate model binding organization_id strictly from OrganizationContext
        supplier_data = data.model_dump()
        supplier = Supplier(
            organization_id=context.organization_id,
            **supplier_data,
        )

        try:
            created_supplier = await self.repo.create(supplier)

            # 3. Create audit event atomically within the same transaction
            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="supplier",
                entity_id=created_supplier.id,
                action="supplier.created",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=None,
                new_state={
                    "legal_name": created_supplier.legal_name,
                    "category": created_supplier.category,
                    "tier": created_supplier.tier,
                    "status": created_supplier.status,
                },
                metadata_={
                    "vendor_code": created_supplier.vendor_code,
                },
            )
            self.db.add(audit)

            # Commit mutation and audit record together
            await self.db.commit()
            await self.db.refresh(created_supplier)
            return created_supplier

        except IntegrityError as exc:
            await self.db.rollback()
            err_text = str(exc.orig) if hasattr(exc, "orig") else str(exc)
            if "uq_suppliers_org_vendor_code" in err_text:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A supplier with this vendor code already exists in this organization.",
                ) from exc
            if "uq_suppliers_org_gstin" in err_text:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A supplier with this GSTIN already exists in this organization.",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Database integrity constraint violation during supplier creation.",
            ) from exc
        except Exception:
            await self.db.rollback()
            raise

    async def get_supplier(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
    ) -> Supplier:
        """Fetch a single supplier, rejecting any resource outside the caller's organization boundary."""
        supplier = await self.repo.get_by_id(supplier_id, context.organization_id)
        if not supplier:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Supplier not found.",
            )
        return supplier

    async def list_suppliers(
        self,
        context: OrganizationContext,
        status_filter: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Supplier], int]:
        """Fetch a paginated list of suppliers belonging to caller's organization."""
        return await self.repo.list_suppliers(
            organization_id=context.organization_id,
            status=status_filter,
            search=search,
            page=page,
            page_size=page_size,
        )

    async def update_supplier(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        data: SupplierUpdate,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> Supplier:
        """Update permitted editable fields on an existing supplier and record an audit event."""
        supplier = await self.get_supplier(context, supplier_id)

        # Business rule: Do not allow modifications to archived suppliers
        if supplier.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot modify an archived supplier.",
            )

        update_dict = data.model_dump(exclude_unset=True)
        if not update_dict:
            return supplier

        # Check vendor code uniqueness if updating vendor_code
        if (
            "vendor_code" in update_dict
            and update_dict["vendor_code"] != supplier.vendor_code
        ):
            new_vc = update_dict["vendor_code"]
            if new_vc is not None:
                existing_vc = await self.repo.get_by_vendor_code(
                    new_vc, context.organization_id
                )
                if existing_vc and existing_vc.id != supplier.id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Supplier with vendor code '{new_vc}' already exists.",
                    )

        # Check GSTIN uniqueness if updating gstin
        if "gstin" in update_dict and update_dict["gstin"] != supplier.gstin:
            new_gstin = update_dict["gstin"]
            if new_gstin is not None:
                existing_gstin = await self.repo.get_by_gstin(
                    new_gstin, context.organization_id
                )
                if existing_gstin and existing_gstin.id != supplier.id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Supplier with GSTIN '{new_gstin}' already exists.",
                    )

        # Track previous and new states for changed fields
        previous_state = {}
        new_state = {}
        for key, value in update_dict.items():
            prev_val = getattr(supplier, key)
            if prev_val != value:
                previous_state[key] = prev_val
                new_state[key] = value
                setattr(supplier, key, value)

        if not previous_state:
            return supplier

        try:
            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="supplier",
                entity_id=supplier.id,
                action="supplier.updated",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state=previous_state,
                new_state=new_state,
                metadata_={
                    "changed_fields": list(previous_state.keys()),
                },
            )
            self.db.add(audit)
            await self.db.commit()
            await self.db.refresh(supplier)
            return supplier

        except IntegrityError as exc:
            await self.db.rollback()
            err_text = str(exc.orig) if hasattr(exc, "orig") else str(exc)
            if "uq_suppliers_org_vendor_code" in err_text:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A supplier with this vendor code already exists in this organization.",
                ) from exc
            if "uq_suppliers_org_gstin" in err_text:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A supplier with this GSTIN already exists in this organization.",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Database integrity constraint violation during supplier update.",
            ) from exc
        except Exception:
            await self.db.rollback()
            raise

    async def archive_supplier(
        self,
        context: OrganizationContext,
        supplier_id: UUID,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> Supplier:
        """Soft-archive a supplier, preserving documents and historical records."""
        supplier = await self.get_supplier(context, supplier_id)

        if supplier.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Supplier is already archived.",
            )

        prev_status = supplier.status
        supplier.status = "archived"

        try:
            audit = AuditEvent(
                organization_id=context.organization_id,
                actor_id=context.user_id,
                actor_type="user",
                entity_type="supplier",
                entity_id=supplier.id,
                action="supplier.archived",
                ip_address=client_ip,
                user_agent=user_agent,
                previous_state={"status": prev_status},
                new_state={"status": "archived"},
                metadata_={"archived_by": str(context.user_id)},
            )
            self.db.add(audit)
            await self.db.commit()
            await self.db.refresh(supplier)
            return supplier

        except Exception:
            await self.db.rollback()
            raise
