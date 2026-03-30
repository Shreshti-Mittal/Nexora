"""
Pydantic schemas — request bodies and response models.
Keeps the API layer clean and validated.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field


# ─────────────────────────────────────────
#  Auth
# ─────────────────────────────────────────

class RegisterRequest(BaseModel):
    name:            str          = Field(..., min_length=1, max_length=100)
    email:           EmailStr
    password:        str          = Field(..., min_length=6)
    camera_consent:  bool         = True


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user_id:      str
    name:         str


# ─────────────────────────────────────────
#  Sessions — Requests
# ─────────────────────────────────────────

class SessionStartRequest(BaseModel):
    topic:                str          = Field(default="General Study", max_length=200)
    goal:                 Optional[str]= None
    planned_duration_min: Optional[int]= None   # 25, 45, 60, 90, or None


class SessionEndRequest(BaseModel):
    """
    Frontend sends final aggregated values it computed locally
    as a sanity check. Backend recomputes from stored metrics anyway.
    """
    client_focus_avg:    Optional[float] = None
    client_duration_sec: Optional[int]   = None


# ─────────────────────────────────────────
#  Metrics — Request (posted every 2 seconds)
# ─────────────────────────────────────────

class MetricTickRequest(BaseModel):
    """
    All values on 0–100 scale (matching what the
    frontend canvas analysis already produces).
    """
    eye_openness:   float = Field(..., ge=0, le=100)
    blink_rate:     float = Field(..., ge=0, le=60)    # blinks/minute
    head_motion:    float = Field(..., ge=0, le=100)
    inactivity_sec: float = Field(default=0.0, ge=0)  # seconds still at this tick


# ─────────────────────────────────────────
#  Sessions — Responses
# ─────────────────────────────────────────

class SessionStartResponse(BaseModel):
    session_id:  int
    status:      str
    started_at:  datetime
    message:     str

    class Config:
        from_attributes = True


class SessionStatusResponse(BaseModel):
    session_id:           int
    status:               str
    topic:                str
    started_at:           datetime
    paused_at:            Optional[datetime]
    ended_at:             Optional[datetime]
    total_paused_seconds: int
    elapsed_seconds:      int    # live calculated

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
#  Metrics — Response
# ─────────────────────────────────────────

class MetricTickResponse(BaseModel):
    """
    Server returns computed scores back so frontend
    can use server-side values for consistency.
    Alert is now a rich dict with title, body, action, type.
    """
    focus_score:      float
    fatigue_index:    float
    inactivity_ratio: float
    alert:            Optional[dict] = None

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
#  Session Analytics — Single Session
# ─────────────────────────────────────────

class SessionAnalyticsResponse(BaseModel):
    session_id:             int
    topic:                  str
    duration_seconds:       Optional[int]
    started_at:             datetime
    ended_at:               Optional[datetime]

    avg_focus_score:        Optional[float]
    peak_focus_score:       Optional[float]
    min_focus_score:        Optional[float]
    avg_fatigue_index:      Optional[float]
    peak_fatigue_index:     Optional[float]
    avg_eye_openness:       Optional[float]
    avg_blink_rate:         Optional[float]
    total_inactivity_sec:   Optional[float]
    inactivity_ratio:       Optional[float]
    focus_drops:            Optional[int]
    pattern_tags:           Optional[List[str]]

    # Focus timeline (list of [timestamp, score] for charting)
    focus_timeline:         List[dict]

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
#  User Analytics — Aggregated Dashboard
# ─────────────────────────────────────────

class UserAnalyticsSummary(BaseModel):
    user_id:              int
    range_days:           int

    total_sessions:       int
    total_study_hours:    float     # total_seconds / 3600
    avg_session_min:      float     # average session length in minutes

    avg_focus_score:      float
    avg_fatigue_index:    float
    avg_inactivity_ratio: float

    # Focus trend — list of {date, avg_focus} for line chart
    focus_trend:          List[dict]

    # Per-topic aggregation
    topic_performance:    List[dict]

    # Weekly pattern — {Mon:120, Tue:45, ...} (minutes)
    weekly_distribution:  dict

    # Pattern insight tags across all sessions
    common_patterns:      List[str]

    # Recent sessions list
    recent_sessions:      List[dict]

    class Config:
        from_attributes = True