import logging
from fastapi import APIRouter, Depends, HTTPException, status
import httpx

from app.helpers import admin_guard
from app.leaderboard_cache import reset_leaderboard_cache

from app.routers.responses import CacheResetResponse


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/cache", tags=["cache"], dependencies=[Depends(admin_guard)])


@router.post(
    "/reset",
    status_code=status.HTTP_200_OK,
    description="Trigger a full data-cache reset on the leaderboard (member) app.",
    response_model=CacheResetResponse,
)
def reset_cache():
    try:
        logger.info("Resetting leaderboard app data cache")
        result = reset_leaderboard_cache()
        logger.info(f"Leaderboard cache reset succeeded: {result}")
        return {"success": True, "message": "Leaderboard cache reset successfully", "result": result}
    except httpx.HTTPStatusError as e:
        logger.error(e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Leaderboard app returned error: {e.response.status_code}"
        )
    except httpx.RequestError as e:
        logger.error(e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Failed to connect to leaderboard app"
        )
