"""
Session Service — MongoDB version
Full session lifecycle: start → pause ↔ resume → end
Metric ingestion + session summary builder.

MongoDB documents use string IDs throughout.
_id is ObjectId in DB, converted to string when returned.
"""

from datetime import datetime
from typing import Optional
from bson import ObjectId
from fastapi import HTTPException, status

from app.db.database import sessions_col, metrics_col, summary_col
from app.schemas.schemas import SessionStartRequest, SessionEndRequest, MetricTickRequest
from app.services.scoring import (
    compute_focus_score,
    compute_fatigue_index,
    compute_inactivity_ratio,
    generate_alert,
    alert_to_message,
    compute_pattern_tags,
    FOCUS_LOW_THRESHOLD,
)


# ─────────────────────────────────────────
#  Session Lifecycle
# ─────────────────────────────────────────

def start_session(user_id: str, data: SessionStartRequest) -> dict:
    """Create and insert a new active session document."""
    doc = {
        "user_id":              user_id,
        "topic":                data.topic or "General Study",
        "goal":                 data.goal,
        "planned_duration_min": data.planned_duration_min,
        "status":               "active",
        "started_at":           datetime.utcnow(),
        "paused_at":            None,
        "ended_at":             None,
        "total_paused_seconds": 0,
        "duration_seconds":     None,
    }
    result = sessions_col().insert_one(doc)
    doc["id"] = str(result.inserted_id)
    return doc


def pause_session(session_id: str, user_id: str) -> dict:
    session = _get_owned_session(session_id, user_id)

    if session["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is '{session['status']}', not active."
        )

    now = datetime.utcnow()
    sessions_col().update_one(
        {"_id": ObjectId(session_id)},
        {"$set": {"status": "paused", "paused_at": now}}
    )
    session["status"]    = "paused"
    session["paused_at"] = now
    return session


def resume_session(session_id: str, user_id: str) -> dict:
    session = _get_owned_session(session_id, user_id)

    if session["status"] != "paused":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is '{session['status']}', not paused."
        )

    # Accumulate pause duration
    paused_for = 0
    if session.get("paused_at"):
        paused_for = int((datetime.utcnow() - session["paused_at"]).total_seconds())

    new_paused_total = (session.get("total_paused_seconds") or 0) + paused_for

    sessions_col().update_one(
        {"_id": ObjectId(session_id)},
        {"$set": {
            "status":               "active",
            "paused_at":            None,
            "total_paused_seconds": new_paused_total,
        }}
    )
    session["status"]               = "active"
    session["paused_at"]            = None
    session["total_paused_seconds"] = new_paused_total
    return session


def end_session(session_id: str, user_id: str, data: Optional[SessionEndRequest] = None) -> dict:
    """End session, compute true duration, build and store summary."""
    session = _get_owned_session(session_id, user_id)

    if session["status"] == "ended":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already ended.")

    # If still paused, accumulate final pause time
    total_paused = session.get("total_paused_seconds") or 0
    if session["status"] == "paused" and session.get("paused_at"):
        total_paused += int((datetime.utcnow() - session["paused_at"]).total_seconds())

    now          = datetime.utcnow()
    raw_elapsed  = (now - session["started_at"]).total_seconds()
    duration_sec = max(0, int(raw_elapsed - total_paused))

    sessions_col().update_one(
        {"_id": ObjectId(session_id)},
        {"$set": {
            "status":               "ended",
            "ended_at":             now,
            "total_paused_seconds": total_paused,
            "duration_seconds":     duration_sec,
        }}
    )

    # Build and store summary
    summary = _compute_and_store_summary(session_id, duration_sec)
    return summary


# ─────────────────────────────────────────
#  Metric Ingestion
# ─────────────────────────────────────────

def record_metric(session_id: str, user_id: str, data: MetricTickRequest) -> dict:
    """
    Store one metric tick and return server-computed scores + optional alert.
    Called every 2 seconds from the frontend.
    """
    session = _get_owned_session(session_id, user_id)

    if session["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot record metrics for a non-active session."
        )

    # Rolling focus average from last 15 ticks
    recent_avg = _get_recent_focus_avg(session_id, last_n=15)

    # Server-side scoring
    focus_score = compute_focus_score(
        eye_openness   = data.eye_openness,
        blink_rate     = data.blink_rate,
        head_motion    = data.head_motion,
        inactivity_sec = data.inactivity_sec,
    )
    fatigue_index = compute_fatigue_index(
        eye_openness     = data.eye_openness,
        blink_rate       = data.blink_rate,
        head_motion      = data.head_motion,
        inactivity_sec   = data.inactivity_sec,
        recent_focus_avg = recent_avg,
    )
    inactivity_ratio = compute_inactivity_ratio(data.inactivity_sec)

    # Persist metric document
    metrics_col().insert_one({
        "session_id":       session_id,
        "recorded_at":      datetime.utcnow(),
        "eye_openness":     data.eye_openness,
        "blink_rate":       data.blink_rate,
        "head_motion":      data.head_motion,
        "inactivity_sec":   data.inactivity_sec,
        "focus_score":      focus_score,
        "fatigue_index":    fatigue_index,
        "inactivity_ratio": inactivity_ratio,
    })

    # Alert check
    elapsed = get_elapsed_seconds(session)
    alert_key = generate_alert(
        focus_score     = focus_score,
        fatigue_index   = fatigue_index,
        eye_openness    = data.eye_openness,
        blink_rate      = data.blink_rate,
        inactivity_sec  = data.inactivity_sec,
        elapsed_seconds = elapsed,
    )

    return {
        "focus_score":      focus_score,
        "fatigue_index":    fatigue_index,
        "inactivity_ratio": inactivity_ratio,
        "alert":            alert_to_message(alert_key),
    }


# ─────────────────────────────────────────
#  Summary Builder
# ─────────────────────────────────────────

def _compute_and_store_summary(session_id: str, duration_sec: int) -> dict:
    """Aggregate all metrics for this session into one summary document."""

    metrics = list(metrics_col().find({"session_id": session_id}))

    if not metrics:
        summary = {
            "session_id":      session_id,
            "duration_seconds": duration_sec,
            "computed_at":     datetime.utcnow(),
        }
        summary_col().insert_one(summary)
        summary["id"] = str(summary.pop("_id", ""))
        return summary

    focus_scores = [m["focus_score"]    for m in metrics if m.get("focus_score")    is not None]
    fatigue_vals = [m["fatigue_index"]   for m in metrics if m.get("fatigue_index")  is not None]
    eye_vals     = [m["eye_openness"]    for m in metrics]
    blink_vals   = [m["blink_rate"]      for m in metrics]
    inact_vals   = [m["inactivity_sec"]  for m in metrics]

    total_inactivity = sum(inact_vals)
    inact_ratio      = round(total_inactivity / duration_sec, 4) if duration_sec else 0
    focus_drops      = sum(1 for s in focus_scores if s < FOCUS_LOW_THRESHOLD)

    avg_focus   = _safe_avg(focus_scores)
    avg_fatigue = _safe_avg(fatigue_vals)

    tags = compute_pattern_tags(
        avg_focus        = avg_focus or 0,
        avg_fatigue      = avg_fatigue or 0,
        inactivity_ratio = inact_ratio,
        focus_drops      = focus_drops,
        total_ticks      = len(metrics),
    )

    summary = {
        "session_id":           session_id,
        "duration_seconds":     duration_sec,
        "computed_at":          datetime.utcnow(),
        "avg_focus_score":      avg_focus,
        "peak_focus_score":     max(focus_scores) if focus_scores else None,
        "min_focus_score":      min(focus_scores) if focus_scores else None,
        "avg_fatigue_index":    avg_fatigue,
        "peak_fatigue_index":   max(fatigue_vals) if fatigue_vals else None,
        "avg_eye_openness":     _safe_avg(eye_vals),
        "avg_blink_rate":       _safe_avg(blink_vals),
        "total_inactivity_sec": round(total_inactivity, 2),
        "inactivity_ratio":     inact_ratio,
        "focus_drops":          focus_drops,
        "pattern_tags":         tags,   # stored as a list, not comma-string
    }

    summary_col().replace_one(
        {"session_id": session_id},
        summary,
        upsert=True
    )
    return summary


# ─────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────

def _get_owned_session(session_id: str, user_id: str) -> dict:
    try:
        oid = ObjectId(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid session ID.")

    session = sessions_col().find_one({"_id": oid, "user_id": user_id})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    session["id"] = str(session["_id"])
    return session


def _get_recent_focus_avg(session_id: str, last_n: int = 15) -> Optional[float]:
    recent = list(
        metrics_col()
        .find({"session_id": session_id, "focus_score": {"$exists": True}})
        .sort("recorded_at", -1)
        .limit(last_n)
    )
    scores = [r["focus_score"] for r in recent if r.get("focus_score") is not None]
    return _safe_avg(scores)


def _safe_avg(values: list) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def get_elapsed_seconds(session: dict) -> int:
    """Live elapsed seconds, accounting for pauses."""
    if session["status"] == "ended":
        return session.get("duration_seconds") or 0

    raw     = (datetime.utcnow() - session["started_at"]).total_seconds()
    paused  = session.get("total_paused_seconds") or 0

    if session["status"] == "paused" and session.get("paused_at"):
        raw -= (datetime.utcnow() - session["paused_at"]).total_seconds()

    return max(0, int(raw - paused))