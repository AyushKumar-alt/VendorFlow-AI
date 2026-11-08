from fastapi import FastAPI

from app.api.routes.document_requirements import (
    router as document_requirements_router,
)
from app.api.routes.document_types import router as document_types_router
from app.api.routes.health import router as health_router
from app.api.routes.organization import router as organization_router
from app.api.routes.supplier_document_groups import (
    router as supplier_document_groups_router,
)
from app.api.routes.supplier_documents import router as supplier_documents_router
from app.api.routes.supplier_sites import router as supplier_sites_router
from app.api.routes.suppliers import router as suppliers_router

app = FastAPI(
    title="VendorFlow AI API",
    version="0.1.0",
)

app.include_router(health_router, prefix="/api/v1")
app.include_router(organization_router, prefix="/api/v1")
app.include_router(suppliers_router, prefix="/api/v1")
app.include_router(document_types_router, prefix="/api/v1")
app.include_router(document_requirements_router, prefix="/api/v1")
app.include_router(supplier_sites_router, prefix="/api/v1")
app.include_router(supplier_document_groups_router, prefix="/api/v1")
app.include_router(supplier_documents_router, prefix="/api/v1")


@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    return {
        "service": "vendorflow-api",
        "version": "0.1.0",
    }
