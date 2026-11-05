from fastapi import FastAPI

from app.api.routes.health import router as health_router

app = FastAPI(
    title="VendorFlow AI API",
    version="0.1.0",
)

app.include_router(health_router, prefix="/api/v1")


@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    return {
        "service": "vendorflow-api",
        "version": "0.1.0",
    }
