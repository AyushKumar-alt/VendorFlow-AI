from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.db.session import check_database

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="Liveness probe")
@router.get("/", summary="Liveness probe", include_in_schema=False)
async def health_liveness() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "vendorflow-api",
    }


@router.get("/ready", summary="Readiness probe")
@router.get("/ready/", summary="Readiness probe", include_in_schema=False)
async def health_readiness():
    try:
        is_ready = await check_database()
        if not is_ready:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "unavailable",
                    "database": "disconnected",
                },
            )
    except Exception:  # noqa: BLE001
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unavailable",
                "database": "disconnected",
            },
        )

    return {
        "status": "ready",
        "database": "connected",
    }
