"""
Nexora Scoring Engine
━━━━━━━━━━━━━━━━━━━━
This is the core logic of the project.

All metric inputs arrive on a 0–100 scale from the frontend
canvas analysis (eye_openness, head_motion) or raw units
(blink_rate in blinks/min, inactivity_sec in seconds).

FOCUS SCORE (0–100)
    Measures how engaged the student is RIGHT NOW.
    Weighted composite:
        eye_openness  40%  — eyes open = alert, drooping = tired/zoned out
        head_motion   20%  — some motion = engaged, zero = frozen/absent
        blink_normal  20%  — normal rate (8–18/min) = good; too high = strain
        inactivity    20%  — penalty when still for too long

FATIGUE INDEX (0–100)
    Measures how tired/worn-down the student appears.
    Derived from:
        low eye openness          (heavy weighting)
        very high blink rate      (eye strain = fatigue signal)
        sustained low focus       (rolling window fatigue accumulation)
        long inactivity periods   (slumped/checked-out)

INACTIVITY RATIO (0.0–1.0)
    Proportion of the tick's window that was "still" (no head motion).
    inactivity_sec / TICK_INTERVAL_SEC
    Capped at 1.0.

ALERT GENERATION
    Thresholds trigger gentle nudge messages returned to frontend.
    Frontend decides whether to show toast or popup.
"""

from typing import Optional

# ─── Constants ───────────────────────────────────────────

TICK_INTERVAL_SEC = 2.0      # frontend posts every 2 seconds

# Blink rate healthy range (blinks/minute)
BLINK_LOW_THRESHOLD  = 7.0   # under this = staring hard, possible strain
BLINK_HIGH_THRESHOLD = 22.0  # over this = eye strain / fatigue
BLINK_OPTIMAL_LOW    = 10.0
BLINK_OPTIMAL_HIGH   = 16.0

# Eye openness thresholds (0–100 brightness proxy)
EYE_DROWSY_THRESHOLD  = 35.0  # below this = likely drowsy
EYE_LOW_THRESHOLD     = 50.0  # below this = possible tiredness

# Head motion thresholds (0–100 from frame diff)
MOTION_ABSENT_LOW    = 5.0    # below this = likely left desk
MOTION_FIDGET_HIGH   = 75.0   # above this = highly distracted/fidgeting

# Focus drop threshold
FOCUS_LOW_THRESHOLD  = 40.0   # below this counts as a "focus drop"

# Inactivity alert threshold
INACT_ALERT_SEC      = 90.0   # more than this still = alert


# ─── Focus Score ─────────────────────────────────────────

def compute_focus_score(
    eye_openness:   float,
    blink_rate:     float,
    head_motion:    float,
    inactivity_sec: float,
) -> float:
    """
    Returns focus score 0–100.

    Component breakdown:
      eye_component   (55%) — primary alertness signal; dominant weight
      blink_component (25%) — penalty outside healthy range
      motion_component(20%) — some motion = engaged; none/too-much penalised
      inactivity_penalty    — direct point deduction for long stillness (up to 45pts)

    Hard gate: eye_openness < 35 caps score at 42 (unmistakably drowsy).
    """

    # 1. Eye openness (0–100)
    eye_component = min(100.0, eye_openness)

    # 2. Blink rate — optimal 10–16 blinks/min
    if BLINK_OPTIMAL_LOW <= blink_rate <= BLINK_OPTIMAL_HIGH:
        blink_component = 100.0
    elif blink_rate < BLINK_OPTIMAL_LOW:
        blink_component = max(40.0, 100.0 - (BLINK_OPTIMAL_LOW - blink_rate) * 5)
    else:
        blink_component = max(0.0, 100.0 - (blink_rate - BLINK_OPTIMAL_HIGH) * 6)

    # 3. Head motion — ideal 5–40; below 5 = possibly absent; above 75 = fidgeting
    if MOTION_ABSENT_LOW <= head_motion <= 75:
        if head_motion <= 40:
            motion_component = min(100.0, 60.0 + (head_motion - MOTION_ABSENT_LOW) * 1.0)
        else:
            motion_component = max(50.0, 100.0 - (head_motion - 40) * 1.3)
    elif head_motion < MOTION_ABSENT_LOW:
        motion_component = 20.0
    else:
        motion_component = max(0.0, 50.0 - (head_motion - MOTION_FIDGET_HIGH))

    # 4. Inactivity — direct penalty subtracted AFTER weighted sum
    #    Reading/thinking still OK up to 30s; penalty ramps hard after that
    if inactivity_sec <= 30:
        inact_penalty = 0.0
    elif inactivity_sec <= 120:
        inact_penalty = ((inactivity_sec - 30) / 90.0) * 45.0   # max 45-pt deduction
    else:
        inact_penalty = 45.0

    # Weighted composite (eye is dominant)
    score = (
        eye_component    * 0.55 +
        blink_component  * 0.25 +
        motion_component * 0.20
    ) - inact_penalty

    # Hard gate for severe drowsiness
    if eye_openness < 35:
        score = min(score, 42.0)

    return round(min(100.0, max(0.0, score)), 2)


# ─── Fatigue Index ───────────────────────────────────────

def compute_fatigue_index(
    eye_openness:   float,
    blink_rate:     float,
    head_motion:    float,
    inactivity_sec: float,
    recent_focus_avg: Optional[float] = None,
) -> float:
    """
    Returns fatigue index 0–100. Higher = more fatigued.

    Eye openness is the dominant fatigue signal (65% weight).
    Curve is aggressive below 55 — drooping eyes = clear fatigue.
    """

    # 1. Eye fatigue — multi-segment curve
    if eye_openness >= 70:
        eye_fatigue = 0.0
    elif eye_openness >= 55:
        eye_fatigue = (70.0 - eye_openness) / 15.0 * 30.0         # 0–30
    elif eye_openness >= 35:
        eye_fatigue = 30.0 + (55.0 - eye_openness) / 20.0 * 40.0  # 30–70
    else:
        eye_fatigue = 70.0 + (35.0 - eye_openness) / 35.0 * 30.0  # 70–100
    eye_fatigue = min(100.0, eye_fatigue)

    # 2. Blink strain — only triggers above 22 blinks/min
    blink_fatigue = min(100.0, max(0.0, (blink_rate - BLINK_HIGH_THRESHOLD) * 5.0)) \
                    if blink_rate > BLINK_HIGH_THRESHOLD else 0.0

    # 3. Slump / no motion
    motion_fatigue = min(60.0, max(0.0, (15.0 - head_motion) * 3.5)) \
                     if head_motion < 15 else 0.0

    # 4. Sustained low focus history
    focus_fatigue = min(100.0, (60.0 - recent_focus_avg) * 1.5) \
                    if recent_focus_avg is not None and recent_focus_avg < 60 else 0.0

    fatigue = (
        eye_fatigue    * 0.65 +
        blink_fatigue  * 0.20 +
        motion_fatigue * 0.10 +
        focus_fatigue  * 0.05
    )
    return round(min(100.0, max(0.0, fatigue)), 2)


# ─── Inactivity Ratio ────────────────────────────────────

def compute_inactivity_ratio(inactivity_sec: float) -> float:
    """
    Ratio of this 2-second tick that was 'still'.
    1.0 = completely still, 0.0 = moving the whole tick.
    Capped at 1.0.
    """
    ratio = inactivity_sec / TICK_INTERVAL_SEC
    return round(min(1.0, max(0.0, ratio)), 4)


# ─── Alert Generation ────────────────────────────────────

def generate_alert(
    focus_score:      float,
    fatigue_index:    float,
    eye_openness:     float,
    blink_rate:       float,
    inactivity_sec:   float,
    elapsed_seconds:  int,
) -> Optional[str]:
    
    elapsed_min = elapsed_seconds / 60

    # ── Warmup period: no alerts for first 3 minutes ──
    # Give the student time to settle, camera to calibrate,
    # and MediaPipe to get accurate readings.
    if elapsed_seconds < 180:
        return None

    # ── Break reminders: only after 25+ min ──
    if elapsed_min >= 25 and int(elapsed_min) % 25 == 0 and elapsed_seconds % 60 < 3:
        return f"break_{int(elapsed_min)}"

    # ── Drowsiness: only after 5 min AND sustained ──
    # Don't fire on a single droopy reading
    if elapsed_seconds > 300 and eye_openness < EYE_DROWSY_THRESHOLD:
        return "drowsy"

    # ── High fatigue: only after 10 min ──
    if elapsed_seconds > 600 and fatigue_index > 75:
        return f"fatigue_high_{int(elapsed_min)}"

    # ── Eye strain ──
    if blink_rate > 25:
        return "eye_strain"

    # ── Inactivity: only after 5 min ──
    if elapsed_seconds > 300 and inactivity_sec > INACT_ALERT_SEC:
        return f"inactivity_{int(inactivity_sec)}"

    # ── Low focus: only after 8 min ──
    if elapsed_seconds > 480 and focus_score < FOCUS_LOW_THRESHOLD:
        return f"low_focus_{int(elapsed_min)}"

    return None


def alert_to_message(alert_key: Optional[str]) -> Optional[dict]:
    """
    Converts alert key into a rich message object:
    {
      title:   short headline
      body:    specific actionable guidance
      action:  what to do right now
      type:    "break" | "warning" | "info"
      duration_hint: suggested break duration in minutes (optional)
    }
    """
    if not alert_key:
        return None

    # ── Scheduled break ──────────────────────────────────
    if alert_key.startswith("break_"):
        mins = int(alert_key.split("_")[1])
        if mins <= 25:
            body   = "Your brain needs rest to consolidate what you just learned. A short break now makes the next session more effective."
            action = "Stand up, drink water, and look out a window for 5 minutes. Set a timer so you come back."
            dur    = 5
        elif mins <= 50:
            body   = f"You have been studying for {mins} minutes straight. Memory research shows breaks every 25–30 minutes significantly improve retention."
            action = "Take a 10-minute break. Walk around, stretch your neck and shoulders, hydrate."
            dur    = 10
        else:
            body   = f"{mins} minutes of continuous study is a long stretch. Your focus quality drops sharply after 45 minutes without a break."
            action = "Take a proper 15-minute break. Step away from the screen completely — no phone either."
            dur    = 15
        return {
            "title":         f"{mins}-Minute Milestone — Take a Break",
            "body":          body,
            "action":        action,
            "type":          "break",
            "duration_hint": dur,
        }

    # ── Drowsiness ───────────────────────────────────────
    if alert_key == "drowsy":
        return {
            "title":  "You Look Drowsy",
            "body":   "Your eye openness has dropped significantly — a common early sign of fatigue. Studying while drowsy creates false memories and reduces retention by up to 40%.",
            "action": "Splash cold water on your face, stand up for 2 minutes, or do 10 jumping jacks. If it persists, a 20-minute nap is more valuable than continuing.",
            "type":   "warning",
        }

    # ── High fatigue ─────────────────────────────────────
    if alert_key.startswith("fatigue_high"):
        parts    = alert_key.split("_")
        mins     = int(parts[2]) if len(parts) > 2 else 0
        mins_str = f" after {mins} minutes of study" if mins > 0 else ""
        return {
            "title":  "Fatigue Building Up",
            "body":   f"High fatigue detected{mins_str}. Your signals — eye openness, motion, focus — are all showing strain. Pushing through fatigue embeds mistakes, not knowledge.",
            "action": "Roll your shoulders back, sit up straight, and take 5 slow deep breaths. Then decide: 10-minute break or switch to a lighter topic.",
            "type":   "warning",
        }

    # ── Eye strain ───────────────────────────────────────
    if alert_key == "eye_strain":
        return {
            "title":  "Eye Strain Detected",
            "body":   "Your blink rate is unusually high — a sign your eyes are under strain. Screen glare, dry air, or sustained close focus without breaks causes this.",
            "action": "Try the 20-20-20 rule right now: look at something 20 feet away for 20 seconds. Blink deliberately 10 times. Adjust your screen brightness if needed.",
            "type":   "warning",
        }

    # ── Inactivity ───────────────────────────────────────
    if alert_key.startswith("inactivity"):
        parts    = alert_key.split("_")
        secs     = int(parts[1]) if len(parts) > 1 else 90
        mins_str = f"{secs // 60} minute{'s' if secs >= 120 else ''}" if secs >= 60 else f"{secs} seconds"
        return {
            "title":  "You Have Not Moved",
            "body":   f"No head movement detected for {mins_str}. Poor posture sustained for long periods reduces blood flow to the brain and increases fatigue.",
            "action": "Sit up straight, roll your shoulders back, tilt your head side to side. Physical posture directly affects cognitive performance.",
            "type":   "info",
        }

    # ── Low focus ────────────────────────────────────────
    if alert_key.startswith("low_focus"):
        parts = alert_key.split("_")
        mins  = int(parts[2]) if len(parts) > 2 else 0
        if mins < 15:
            tip = "You may still be warming up — the first 10–15 minutes of a session are often lower focus. Try writing down your goal for this session in one sentence."
        elif mins < 35:
            tip = "Mid-session dip is common. Try the 2-minute rule: pick the single smallest task in your material and do just that. Momentum builds focus."
        else:
            tip = f"After {mins} minutes, low focus often means mental fatigue. Consider ending this session in the next 10 minutes and resuming after a proper break."
        return {
            "title":  "Focus Has Dropped",
            "body":   tip,
            "action": "Close any unrelated tabs, put your phone face-down, and re-read your study goal at the top of the setup panel.",
            "type":   "warning",
        }

    return None


def compute_pattern_tags(
    avg_focus:        float,
    avg_fatigue:      float,
    inactivity_ratio: float,
    focus_drops:      int,
    total_ticks:      int,
) -> list[str]:
    """
    Labels the session with readable pattern tags for the insights panel.
    Used in session_summary.pattern_tags.
    """
    tags = []

    if avg_focus >= 75:
        tags.append("high_focus")
    elif avg_focus >= 55:
        tags.append("moderate_focus")
    else:
        tags.append("low_focus")

    if avg_fatigue >= 60:
        tags.append("fatigued")
    elif avg_fatigue >= 35:
        tags.append("mild_fatigue")

    if inactivity_ratio >= 0.4:
        tags.append("high_inactivity")
    elif inactivity_ratio <= 0.05:
        tags.append("consistently_present")

    if total_ticks > 0 and (focus_drops / total_ticks) > 0.3:
        tags.append("frequent_drops")

    return tags