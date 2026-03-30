"""
Analytics Service — MongoDB version
Powers GET /analytics/session/{id} and GET /analytics/user/{id}
"""

from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional
from bson import ObjectId
from fastapi import HTTPException

from app.db.database import sessions_col, metrics_col, summary_col


# ─────────────────────────────────────────
#  Single Session Analytics
# ─────────────────────────────────────────

def get_session_analytics(session_id: str, user_id: str) -> dict:
    try:
        oid = ObjectId(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid session ID.")

    session = sessions_col().find_one({"_id": oid, "user_id": user_id})
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    summary = summary_col().find_one({"session_id": session_id})

    # Focus timeline for charting
    raw_metrics = list(
        metrics_col()
        .find({"session_id": session_id})
        .sort("recorded_at", 1)
    )

    focus_timeline = []
    for m in raw_metrics:
        elapsed = int((m["recorded_at"] - session["started_at"]).total_seconds())
        focus_timeline.append({
            "elapsed_sec":   elapsed,
            "focus_score":   m.get("focus_score"),
            "fatigue_index": m.get("fatigue_index"),
            "eye_openness":  m.get("eye_openness"),
        })

    tags = summary.get("pattern_tags", []) if summary else []

    return {
        "session_id":           str(session["_id"]),
        "topic":                session.get("topic"),
        "duration_seconds":     session.get("duration_seconds"),
        "started_at":           session.get("started_at"),
        "ended_at":             session.get("ended_at"),

        "avg_focus_score":      summary.get("avg_focus_score")      if summary else None,
        "peak_focus_score":     summary.get("peak_focus_score")     if summary else None,
        "min_focus_score":      summary.get("min_focus_score")      if summary else None,
        "avg_fatigue_index":    summary.get("avg_fatigue_index")    if summary else None,
        "peak_fatigue_index":   summary.get("peak_fatigue_index")   if summary else None,
        "avg_eye_openness":     summary.get("avg_eye_openness")     if summary else None,
        "avg_blink_rate":       summary.get("avg_blink_rate")       if summary else None,
        "total_inactivity_sec": summary.get("total_inactivity_sec") if summary else None,
        "inactivity_ratio":     summary.get("inactivity_ratio")     if summary else None,
        "focus_drops":          summary.get("focus_drops")          if summary else None,
        "pattern_tags":         tags,
        "focus_timeline":       focus_timeline,
    }


# ─────────────────────────────────────────
#  User Dashboard Analytics
# ─────────────────────────────────────────

def get_user_analytics(user_id: str, range_days: int = 30) -> dict:
    cutoff = datetime.utcnow() - timedelta(days=range_days)

    sessions = list(
        sessions_col().find({
            "user_id":   user_id,
            "status":    "ended",
            "ended_at":  {"$gte": cutoff},
        }).sort("ended_at", -1)
    )

    if not sessions:
        return _empty_analytics(user_id, range_days)

    session_ids = [str(s["_id"]) for s in sessions]

    # Batch fetch all summaries
    summaries    = list(summary_col().find({"session_id": {"$in": session_ids}}))
    summary_map  = {s["session_id"]: s for s in summaries}

    # ── Overall stats ──────────────────
    total_sessions  = len(sessions)
    durations       = [s["duration_seconds"] for s in sessions if s.get("duration_seconds")]
    total_seconds   = sum(durations)
    total_hours     = round(total_seconds / 3600, 2)
    avg_session_min = round((total_seconds / total_sessions) / 60, 1) if total_sessions else 0

    focus_avgs  = [sm["avg_focus_score"]   for sm in summaries if sm.get("avg_focus_score")   is not None]
    fatigue_avgs= [sm["avg_fatigue_index"]  for sm in summaries if sm.get("avg_fatigue_index") is not None]
    inact_avgs  = [sm["inactivity_ratio"]   for sm in summaries if sm.get("inactivity_ratio")  is not None]

    avg_focus   = _safe_avg(focus_avgs)   or 0
    avg_fatigue = _safe_avg(fatigue_avgs) or 0
    avg_inact   = _safe_avg(inact_avgs)   or 0

    # ── Focus trend (daily) ────────────
    focus_by_date = defaultdict(list)
    for s in sessions:
        sm = summary_map.get(str(s["_id"]))
        if sm and sm.get("avg_focus_score") is not None:
            day = s["ended_at"].strftime("%b %d")
            focus_by_date[day].append(sm["avg_focus_score"])

    focus_trend = [
        {"date": day, "avg_focus": round(sum(scores) / len(scores), 1)}
        for day, scores in sorted(focus_by_date.items())
    ]

    # ── Topic performance ──────────────
    topic_data = defaultdict(lambda: {"sessions": 0, "focus_scores": []})
    for s in sessions:
        sm = summary_map.get(str(s["_id"]))
        topic = s.get("topic", "General Study")
        topic_data[topic]["sessions"] += 1
        if sm and sm.get("avg_focus_score") is not None:
            topic_data[topic]["focus_scores"].append(sm["avg_focus_score"])

    topic_performance = []
    for topic, data in topic_data.items():
        avg = _safe_avg(data["focus_scores"])
        score = round(avg, 1) if avg else 0
        grade = "strong" if score >= 70 else "mid" if score >= 50 else "weak"
        topic_performance.append({
            "topic":    topic,
            "sessions": data["sessions"],
            "avg_focus": score,
            "grade":    grade,
        })
    topic_performance.sort(key=lambda x: x["avg_focus"], reverse=True)

    # ── Weekly distribution ────────────
    day_names  = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekly_min = defaultdict(int)
    for s in sessions:
        if s.get("duration_seconds") and s.get("ended_at"):
            weekday = day_names[s["ended_at"].weekday()]
            weekly_min[weekday] += s["duration_seconds"] // 60
    weekly_distribution = {d: weekly_min.get(d, 0) for d in day_names}

    # ── Common pattern tags ────────────
    all_tags = []
    for sm in summaries:
        all_tags.extend(sm.get("pattern_tags", []))
    tag_counts = defaultdict(int)
    for t in all_tags:
        tag_counts[t] += 1
    common_patterns = sorted(tag_counts, key=tag_counts.get, reverse=True)[:5]

    # ── Recent sessions ────────────────
    recent_sessions = []
    for s in sessions[:10]:
        sm = summary_map.get(str(s["_id"]))
        recent_sessions.append({
            "session_id":    str(s["_id"]),
            "topic":         s.get("topic"),
            "ended_at":      s["ended_at"].strftime("%b %d") if s.get("ended_at") else "—",
            "duration_min":  round((s.get("duration_seconds") or 0) / 60),
            "avg_focus":     round(sm["avg_focus_score"], 1)   if sm and sm.get("avg_focus_score")  else None,
            "avg_fatigue":   round(sm["avg_fatigue_index"], 1) if sm and sm.get("avg_fatigue_index") else None,
            "inactivity_pct":round((sm.get("inactivity_ratio") or 0) * 100, 1) if sm else None,
            "pattern_tags":  sm.get("pattern_tags", []) if sm else [],
        })

    # ── Insights ──────────────────────
    insights = _generate_insights(
        avg_focus           = avg_focus,
        avg_fatigue         = avg_fatigue,
        avg_inact           = avg_inact,
        focus_trend         = focus_trend,
        topic_performance   = topic_performance,
        weekly_distribution = weekly_distribution,
        total_sessions      = total_sessions,
    )

    return {
        "user_id":              user_id,
        "range_days":           range_days,
        "total_sessions":       total_sessions,
        "total_study_hours":    total_hours,
        "avg_session_min":      avg_session_min,
        "avg_focus_score":      avg_focus,
        "avg_fatigue_index":    avg_fatigue,
        "avg_inactivity_ratio": avg_inact,
        "focus_trend":          focus_trend,
        "topic_performance":    topic_performance,
        "weekly_distribution":  weekly_distribution,
        "common_patterns":      common_patterns,
        "recent_sessions":      recent_sessions,
        "insights":             insights,
    }


# ─────────────────────────────────────────
#  Insight Engine
# ─────────────────────────────────────────

def _generate_insights(avg_focus, avg_fatigue, avg_inact,
                       focus_trend, topic_performance,
                       weekly_distribution, total_sessions) -> list:
    insights = []

    # Focus trend direction
    if len(focus_trend) >= 6:
        recent = [x["avg_focus"] for x in focus_trend[-3:]]
        prev   = [x["avg_focus"] for x in focus_trend[-6:-3]]
        delta  = sum(recent)/len(recent) - sum(prev)/len(prev)
        if delta >= 5:
            insights.append({"type":"positive","title":"Focus is improving",
                "body":f"Up {delta:.0f}% over recent sessions. Keep the consistency.",
                "severity":"green"})
        elif delta <= -8:
            insights.append({"type":"warning","title":"Focus is declining",
                "body":f"Down {abs(delta):.0f}% vs earlier. Check sleep and session timing.",
                "severity":"orange"})

    # Topic gaps
    for t in [x for x in topic_performance if x["grade"] == "weak"][:2]:
        insights.append({"type":"gap","title":f"{t['topic']} needs work",
            "body":f"Avg focus {t['avg_focus']}% across {t['sessions']} session(s). Try shorter, more targeted sessions.",
            "severity":"red"})

    for t in [x for x in topic_performance if x["grade"] == "strong"][:1]:
        insights.append({"type":"positive","title":f"{t['topic']} is solid",
            "body":f"Consistent focus above {t['avg_focus']}% across {t['sessions']} session(s).",
            "severity":"green"})

    if avg_fatigue >= 55:
        insights.append({"type":"fatigue","title":"High fatigue across sessions",
            "body":"Average fatigue above 55%. Consider shorter sessions with more breaks.",
            "severity":"orange"})

    if avg_inact >= 0.35:
        insights.append({"type":"inactivity","title":"High inactivity detected",
            "body":f"{avg_inact*100:.0f}% of session time inactive on average.",
            "severity":"orange"})

    if weekly_distribution:
        best_day  = max(weekly_distribution, key=weekly_distribution.get)
        best_mins = weekly_distribution[best_day]
        if best_mins > 0:
            insights.append({"type":"pattern","title":f"{best_day} is your best study day",
                "body":f"{best_mins} minutes on average. Schedule hard topics here.",
                "severity":"blue"})

    if total_sessions < 3:
        insights.append({"type":"habit","title":"Build your study habit",
            "body":"Only a few sessions so far. Even 20 minutes daily adds up.",
            "severity":"blue"})

    return insights[:5]


# ─────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────

def _safe_avg(values: list) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _empty_analytics(user_id: str, range_days: int) -> dict:
    return {
        "user_id":              user_id,
        "range_days":           range_days,
        "total_sessions":       0,
        "total_study_hours":    0,
        "avg_session_min":      0,
        "avg_focus_score":      0,
        "avg_fatigue_index":    0,
        "avg_inactivity_ratio": 0,
        "focus_trend":          [],
        "topic_performance":    [],
        "weekly_distribution":  {"Mon":0,"Tue":0,"Wed":0,"Thu":0,"Fri":0,"Sat":0,"Sun":0},
        "common_patterns":      [],
        "recent_sessions":      [],
        "insights":             [{"type":"habit","title":"No sessions yet",
            "body":"Start your first session to see analytics here.",
            "severity":"blue"}],
    }