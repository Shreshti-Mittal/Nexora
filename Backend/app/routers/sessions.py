"""Sessions Router — MongoDB version"""

from fastapi import APIRouter, Depends, status
from app.db.auth import get_current_user
from app.schemas.schemas import (
    SessionStartRequest, SessionEndRequest, MetricTickRequest,
    SessionStartResponse, MetricTickResponse,
)
from app.services import session_service
from app.services.session_service import get_elapsed_seconds

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.post("/start", status_code=201)
def start_session(
    body:         SessionStartRequest,
    current_user: dict = Depends(get_current_user),
):
    session = session_service.start_session(current_user["id"], body)
    return {
        "session_id": session["id"],
        "status":     session["status"],
        "started_at": session["started_at"],
        "message":    f"Session started: {session['topic']}",
    }


@router.post("/{session_id}/pause")
def pause_session(
    session_id:   str,
    current_user: dict = Depends(get_current_user),
):
    session = session_service.pause_session(session_id, current_user["id"])
    return {
        "session_id": session["id"],
        "status":     session["status"],
        "paused_at":  session.get("paused_at"),
        "message":    "Session paused.",
    }


@router.post("/{session_id}/resume")
def resume_session(
    session_id:   str,
    current_user: dict = Depends(get_current_user),
):
    session = session_service.resume_session(session_id, current_user["id"])
    return {
        "session_id":           session["id"],
        "status":               session["status"],
        "total_paused_seconds": session.get("total_paused_seconds"),
        "message":              "Session resumed.",
    }


@router.post("/{session_id}/end")
def end_session(
    session_id:   str,
    body:         SessionEndRequest = SessionEndRequest(),
    current_user: dict = Depends(get_current_user),
):
    summary = session_service.end_session(session_id, current_user["id"], body)
    return {
        "session_id":        session_id,
        "status":            "ended",
        "duration_seconds":  summary.get("duration_seconds"),
        "avg_focus_score":   summary.get("avg_focus_score"),
        "avg_fatigue_index": summary.get("avg_fatigue_index"),
        "inactivity_ratio":  summary.get("inactivity_ratio"),
        "pattern_tags":      summary.get("pattern_tags", []),
        "message":           "Session ended and summary saved.",
    }


@router.post("/{session_id}/metrics", response_model=MetricTickResponse)
def post_metric(
    session_id:   str,
    body:         MetricTickRequest,
    current_user: dict = Depends(get_current_user),
):
    result = session_service.record_metric(session_id, current_user["id"], body)
    return MetricTickResponse(**result)


@router.get("/{session_id}")
def get_session_status(
    session_id:   str,
    current_user: dict = Depends(get_current_user),
):
    from app.services.session_service import _get_owned_session
    session = _get_owned_session(session_id, current_user["id"])
    return {
        "session_id":           session["id"],
        "status":               session["status"],
        "topic":                session.get("topic"),
        "started_at":           session.get("started_at"),
        "paused_at":            session.get("paused_at"),
        "ended_at":             session.get("ended_at"),
        "total_paused_seconds": session.get("total_paused_seconds", 0),
        "elapsed_seconds":      get_elapsed_seconds(session),
    }