"""
Tests for the scoring engine.
Run with: pytest tests/test_scoring.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.scoring import (
    compute_focus_score,
    compute_fatigue_index,
    compute_inactivity_ratio,
    generate_alert,
    compute_pattern_tags,
)


class TestFocusScore:

    def test_perfect_conditions(self):
        """Eyes open, normal blink rate, gentle motion, not still → high score"""
        score = compute_focus_score(
            eye_openness=85, blink_rate=13, head_motion=20, inactivity_sec=0
        )
        assert score >= 80, f"Expected >= 80, got {score}"

    def test_drowsy_eyes(self):
        """Very low eye openness should tank focus"""
        score = compute_focus_score(
            eye_openness=20, blink_rate=13, head_motion=20, inactivity_sec=0
        )
        assert score < 50, f"Expected < 50 for drowsy eyes, got {score}"

    def test_high_blink_rate_penalty(self):
        """High blink rate (eye strain) should reduce focus"""
        score_normal = compute_focus_score(eye_openness=80, blink_rate=13, head_motion=20, inactivity_sec=0)
        score_strain = compute_focus_score(eye_openness=80, blink_rate=28, head_motion=20, inactivity_sec=0)
        assert score_strain < score_normal, "High blink rate should lower focus"

    def test_long_inactivity_tanks_focus(self):
        """150 seconds still should score low"""
        score = compute_focus_score(
            eye_openness=75, blink_rate=13, head_motion=18, inactivity_sec=150
        )
        assert score < 60, f"Expected < 60 for very still, got {score}"

    def test_score_bounded_0_100(self):
        """Score must always be between 0 and 100"""
        for eye in [0, 50, 100]:
            for blink in [0, 15, 50]:
                for motion in [0, 30, 100]:
                    for inact in [0, 60, 200]:
                        s = compute_focus_score(eye, blink, motion, inact)
                        assert 0 <= s <= 100, f"Score out of range: {s}"

    def test_absent_motion(self):
        """Very low head motion = possibly left desk → lower score"""
        score_present = compute_focus_score(eye_openness=75, blink_rate=13, head_motion=25, inactivity_sec=0)
        score_absent  = compute_focus_score(eye_openness=75, blink_rate=13, head_motion=2,  inactivity_sec=0)
        assert score_absent < score_present, "No motion should score lower than present"


class TestFatigueIndex:

    def test_fresh_student(self):
        """Good eye openness, normal blink, active → low fatigue"""
        fi = compute_fatigue_index(
            eye_openness=80, blink_rate=13, head_motion=25, inactivity_sec=0
        )
        assert fi < 30, f"Expected low fatigue, got {fi}"

    def test_drooping_eyes_high_fatigue(self):
        """Low eye openness = primary fatigue signal"""
        fi = compute_fatigue_index(
            eye_openness=20, blink_rate=13, head_motion=20, inactivity_sec=0
        )
        assert fi >= 50, f"Expected high fatigue for drooping eyes, got {fi}"

    def test_fatigue_bounded(self):
        """Fatigue index must be 0–100"""
        for eye in [0, 50, 100]:
            for blink in [5, 20, 40]:
                fi = compute_fatigue_index(eye, blink, 15, 0)
                assert 0 <= fi <= 100, f"Fatigue out of range: {fi}"

    def test_high_blink_adds_fatigue(self):
        """Excessive blinking should increase fatigue"""
        fi_normal = compute_fatigue_index(eye_openness=75, blink_rate=13, head_motion=20, inactivity_sec=0)
        fi_strain = compute_fatigue_index(eye_openness=75, blink_rate=30, head_motion=20, inactivity_sec=0)
        assert fi_strain > fi_normal, "High blink rate should increase fatigue"


class TestInactivityRatio:

    def test_fully_still(self):
        """2 seconds still / 2 second tick = 1.0"""
        assert compute_inactivity_ratio(2.0) == 1.0

    def test_not_still(self):
        assert compute_inactivity_ratio(0.0) == 0.0

    def test_capped_at_1(self):
        """Should never exceed 1.0"""
        assert compute_inactivity_ratio(999) == 1.0

    def test_partial(self):
        ratio = compute_inactivity_ratio(1.0)
        assert ratio == 0.5


class TestAlerts:

    def test_break_alert_at_25_min(self):
        """Should trigger break alert at exactly 25 minutes"""
        alert = generate_alert(
            focus_score=80, fatigue_index=20, eye_openness=75,
            blink_rate=13, inactivity_sec=0, elapsed_seconds=25*60
        )
        assert alert is not None and "break" in alert

    def test_drowsy_alert(self):
        alert = generate_alert(
            focus_score=40, fatigue_index=30, eye_openness=25,
            blink_rate=13, inactivity_sec=0, elapsed_seconds=100
        )
        assert alert == "drowsy"

    def test_inactivity_alert(self):
        alert = generate_alert(
            focus_score=70, fatigue_index=20, eye_openness=75,
            blink_rate=13, inactivity_sec=100, elapsed_seconds=200
        )
        assert alert == "inactivity"

    def test_no_alert_on_good_signals(self):
        alert = generate_alert(
            focus_score=85, fatigue_index=15, eye_openness=80,
            blink_rate=13, inactivity_sec=5, elapsed_seconds=300
        )
        assert alert is None


class TestPatternTags:

    def test_high_focus_tag(self):
        tags = compute_pattern_tags(avg_focus=80, avg_fatigue=20, inactivity_ratio=0.05, focus_drops=1, total_ticks=50)
        assert "high_focus" in tags

    def test_fatigued_tag(self):
        tags = compute_pattern_tags(avg_focus=60, avg_fatigue=70, inactivity_ratio=0.1, focus_drops=2, total_ticks=30)
        assert "fatigued" in tags

    def test_frequent_drops_tag(self):
        tags = compute_pattern_tags(avg_focus=45, avg_fatigue=30, inactivity_ratio=0.1, focus_drops=20, total_ticks=30)
        assert "frequent_drops" in tags