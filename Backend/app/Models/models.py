"""
SQLAlchemy ORM models for Nexora.
Tables: users, study_sessions, engagement_metrics, session_summary
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Boolean,
    DateTime, ForeignKey, Enum, Text
)
from sqlalchemy.orm import relationship
import enum

from app.db.database import Base


# ─────────────────────────────────────────
#  Enums
# ─────────────────────────────────────────

class SessionStatus(str, enum.Enum):
    active   = "active"
    paused   = "paused"
    ended    = "ended"


# ─────────────────────────────────────────
#  Users
# ─────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String(100), nullable=False)
    email          = Column(String(150), unique=True, index=True, nullable=False)
    hashed_password= Column(String(255), nullable=False)
    camera_consent = Column(Boolean, default=False)
    created_at     = Column(DateTime, default=datetime.utcnow)

    sessions       = relationship("StudySession", back_populates="user", cascade="all, delete-orphan")


# ─────────────────────────────────────────
#  Study Sessions
# ─────────────────────────────────────────

class StudySession(Base):
    """
    Lifecycle: active → paused ↔ active → ended
    duration_seconds is calculated at end using started_at minus
    all paused intervals (tracked via pause_events).
    """
    __tablename__ = "study_sessions"

    id                   = Column(Integer, primary_key=True, index=True)
    user_id              = Column(Integer, ForeignKey("users.id"), nullable=False)
    topic                = Column(String(200), nullable=False, default="General Study")
    goal                 = Column(Text, nullable=True)
    planned_duration_min = Column(Integer, nullable=True)   # user-set target

    status               = Column(Enum(SessionStatus), default=SessionStatus.active)
    started_at           = Column(DateTime, default=datetime.utcnow)
    paused_at            = Column(DateTime, nullable=True)   # set when paused
    ended_at             = Column(DateTime, nullable=True)
    total_paused_seconds = Column(Integer, default=0)        # accumulated pause time
    duration_seconds     = Column(Integer, nullable=True)    # filled on end

    # Relationships
    user                 = relationship("User", back_populates="sessions")
    metrics              = relationship("EngagementMetric", back_populates="session", cascade="all, delete-orphan")
    summary              = relationship("SessionSummary", back_populates="session", uselist=False, cascade="all, delete-orphan")


# ─────────────────────────────────────────
#  Engagement Metrics (raw, per-tick)
# ─────────────────────────────────────────

class EngagementMetric(Base):
    """
    One row every 2 seconds from the frontend canvas analysis.
    Focus score is computed here server-side for consistency,
    but frontend also sends its own for comparison.
    """
    __tablename__ = "engagement_metrics"

    id              = Column(Integer, primary_key=True, index=True)
    session_id      = Column(Integer, ForeignKey("study_sessions.id"), nullable=False, index=True)
    recorded_at     = Column(DateTime, default=datetime.utcnow, index=True)

    # Raw signals (0–100 scale from frontend canvas analysis)
    eye_openness    = Column(Float, nullable=False)   # % brightness proxy
    blink_rate      = Column(Float, nullable=False)   # blinks/minute
    head_motion     = Column(Float, nullable=False)   # motion score 0–100
    inactivity_sec  = Column(Float, default=0.0)      # seconds still at this tick

    # Computed server-side
    focus_score     = Column(Float, nullable=True)    # 0–100
    fatigue_index   = Column(Float, nullable=True)    # 0–100
    inactivity_ratio= Column(Float, nullable=True)    # 0–1 (ratio of this tick still)

    session         = relationship("StudySession", back_populates="metrics")


# ─────────────────────────────────────────
#  Session Summary (one row per ended session)
# ─────────────────────────────────────────

class SessionSummary(Base):
    """
    Computed at session end. Aggregates all metric ticks into
    final scores used by the analytics dashboard.
    """
    __tablename__ = "session_summary"

    id                    = Column(Integer, primary_key=True, index=True)
    session_id            = Column(Integer, ForeignKey("study_sessions.id"), unique=True, nullable=False)
    computed_at           = Column(DateTime, default=datetime.utcnow)

    # Aggregated metric scores
    avg_focus_score       = Column(Float, nullable=True)   # mean focus over session
    peak_focus_score      = Column(Float, nullable=True)   # max focus tick
    min_focus_score       = Column(Float, nullable=True)   # min focus tick

    avg_fatigue_index     = Column(Float, nullable=True)
    peak_fatigue_index    = Column(Float, nullable=True)   # worst fatigue moment

    avg_eye_openness      = Column(Float, nullable=True)
    avg_blink_rate        = Column(Float, nullable=True)
    total_inactivity_sec  = Column(Float, nullable=True)   # total still seconds
    inactivity_ratio      = Column(Float, nullable=True)   # still_sec / duration

    total_alerts          = Column(Integer, default=0)     # how many nudges fired
    focus_drops           = Column(Integer, default=0)     # times score < 40

    # Session duration (mirrored from StudySession for faster joins)
    duration_seconds      = Column(Integer, nullable=True)

    # Pattern tags (comma-separated: "high_focus", "fatigue_late", "distracted", etc.)
    pattern_tags          = Column(String(300), nullable=True)

    session               = relationship("StudySession", back_populates="summary")