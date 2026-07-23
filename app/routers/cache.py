from fastapi import APIRouter, Depends, HTTPException, status
import httpx

from app.helpers import admin_guard
from app.leaderboard_cache import reset_leaderboard_cache
from app.routers.logging import LogFile, write_log, write_log_exception, write_log_title

router = APIRouter()


@router.post(
    "/reset",
    status_code=status.HTTP_200_OK,
    description="Trigger a full data-cache reset on the leaderboard (member) app.",
)
def reset_cache(credentials=Depends(admin_guard)):
    with LogFile("reset cache") as log:
        try:
            write_log_title("Resetting leaderboard app data cache")
            result = reset_leaderboard_cache()
            write_log(f"Leaderboard cache reset succeeded: {result}")
            return {"success": True, "message": "Leaderboard cache reset successfully", "result": result}
        except httpx.HTTPStatusError as e:
            write_log_exception(e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Leaderboard app returned error: {e.response.status_code}",
            )
        except httpx.RequestError as e:
            write_log_exception(e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Failed to connect to leaderboard app"
            )
