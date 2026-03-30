"""Analytics Router — MongoDB version"""

from fastapi import APIRouter, Depends, Query, HTTPException, status
from app.db.auth import get_current_user
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/session/{session_id}")
def get_session_analytics(
    session_id:   str,
    current_user: dict = Depends(get_current_user),
):
    return analytics_service.get_session_analytics(session_id, current_user["id"])


@router.get("/user/{user_id}")
def get_user_analytics(
    user_id:    str,
    range_days: int  = Query(default=30, ge=1, le=365),
    current_user: dict = Depends(get_current_user),
):
    if user_id != current_user["id"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return analytics_service.get_user_analytics(user_id, range_days)