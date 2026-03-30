"""
Nexora CV Server — MediaPipe WebSocket
=======================================
Runs on ws://localhost:8001

Browser sends: base64-encoded JPEG frames
Server returns: JSON with real CV metrics

Metrics extracted:
  - eye_openness    (Eye Aspect Ratio → 0-100)
  - blink_rate      (blinks per minute, rolling 30s window)
  - head_motion     (frame-to-frame landmark movement → 0-100)
  - head_pose       (pitch, yaw, roll in degrees)
  - inactivity_sec  (seconds with no significant head movement)
  - face_detected   (bool)
"""

import asyncio
import base64
import json
import math
import time
import traceback
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
import websockets

# ─── MediaPipe Setup ──────────────────────────────────────
mp_face_mesh = mp.solutions.face_mesh
face_mesh    = mp_face_mesh.FaceMesh(
    static_image_mode        = False,
    max_num_faces            = 1,
    refine_landmarks         = True,   # enables iris landmarks
    min_detection_confidence = 0.5,
    min_tracking_confidence  = 0.5,
)

# ─── Landmark Indices ─────────────────────────────────────
# Eye Aspect Ratio landmarks (MediaPipe 468-point model)
# Left eye
LEFT_EYE  = [362, 385, 387, 263, 373, 380]
# Right eye
RIGHT_EYE = [33,  160, 158, 133, 153, 144]

# Iris landmarks (for gaze)
LEFT_IRIS  = [474, 475, 476, 477]
RIGHT_IRIS = [469, 470, 471, 472]

# Head pose reference points
NOSE_TIP     = 1
CHIN         = 152
LEFT_EYE_L   = 263
RIGHT_EYE_R  = 33
LEFT_MOUTH   = 287
RIGHT_MOUTH  = 57


# ─── Eye Aspect Ratio ─────────────────────────────────────

def eye_aspect_ratio(landmarks, eye_indices, w, h):
    """
    EAR = (|p2-p6| + |p3-p5|) / (2 × |p1-p4|)
    
    Standard formula from Soukupová & Čech (2016).
    EAR ≈ 0.3 when eye open, drops sharply to ~0 on blink.
    """
    pts = []
    for idx in eye_indices:
        lm = landmarks[idx]
        pts.append((lm.x * w, lm.y * h))

    # Vertical distances
    v1 = math.dist(pts[1], pts[5])
    v2 = math.dist(pts[2], pts[4])
    # Horizontal distance
    h1 = math.dist(pts[0], pts[3])

    if h1 == 0:
        return 0.0
    return (v1 + v2) / (2.0 * h1)


def ear_to_openness(ear: float) -> float:
    """
    Map EAR (typically 0.0–0.4) to openness percentage 0–100.
    EAR < 0.20 → eye closing/closed
    EAR > 0.30 → eye fully open
    """
    # Clamp and normalise to 0–100
    normalized = (ear - 0.15) / (0.35 - 0.15)   # 0.15=closed, 0.35=open
    normalized = max(0.0, min(1.0, normalized))
    return round(normalized * 100, 1)


# ─── Head Pose ────────────────────────────────────────────

def estimate_head_pose(landmarks, w, h):
    """
    Estimate pitch, yaw, roll using solvePnP with 6 reference points.
    Returns (pitch, yaw, roll) in degrees.
      pitch > 0 → looking down
      yaw   > 0 → looking right
      roll  > 0 → tilting right
    """
    # 3D model points (generic face model, metres scale doesn't matter)
    model_points = np.array([
        [0.0,      0.0,      0.0],       # Nose tip
        [0.0,     -330.0,   -65.0],      # Chin
        [-225.0,   170.0,  -135.0],      # Left eye left corner
        [225.0,    170.0,  -135.0],      # Right eye right corner
        [-150.0,  -150.0,  -125.0],      # Left mouth
        [150.0,   -150.0,  -125.0],      # Right mouth
    ], dtype=np.float64)

    # 2D image points
    def pt(idx):
        lm = landmarks[idx]
        return [lm.x * w, lm.y * h]

    image_points = np.array([
        pt(NOSE_TIP), pt(CHIN),
        pt(LEFT_EYE_L), pt(RIGHT_EYE_R),
        pt(LEFT_MOUTH), pt(RIGHT_MOUTH),
    ], dtype=np.float64)

    # Camera internals (approximation)
    focal_length = w
    center       = (w / 2, h / 2)
    cam_matrix   = np.array([
        [focal_length, 0,            center[0]],
        [0,            focal_length, center[1]],
        [0,            0,            1        ],
    ], dtype=np.float64)
    dist_coeffs = np.zeros((4, 1))

    ok, rvec, tvec = cv2.solvePnP(
        model_points, image_points,
        cam_matrix, dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        return 0.0, 0.0, 0.0

    rmat, _ = cv2.Rodrigues(rvec)
    # Decompose rotation matrix to Euler angles
    sy = math.sqrt(rmat[0,0]**2 + rmat[1,0]**2)
    singular = sy < 1e-6

    if not singular:
        pitch = math.degrees(math.atan2( rmat[2,1], rmat[2,2]))
        yaw   = math.degrees(math.atan2(-rmat[2,0], sy))
        roll  = math.degrees(math.atan2( rmat[1,0], rmat[0,0]))
    else:
        pitch = math.degrees(math.atan2(-rmat[1,2], rmat[1,1]))
        yaw   = math.degrees(math.atan2(-rmat[2,0], sy))
        roll  = 0.0

    return round(pitch, 1), round(yaw, 1), round(roll, 1)


# ─── Per-Client State ─────────────────────────────────────

class ClientState:
    """Tracks rolling state per WebSocket connection."""

    def __init__(self):
        # Blink tracking
        self.ear_history      = deque(maxlen=3)   # smooth EAR
        self.blink_timestamps = deque()            # timestamps of detected blinks
        self.in_blink         = False
        self.EAR_BLINK_THRESH = 0.25            # below this = blink

        # Head motion tracking
        self.prev_nose        = None               # (x, y) of nose tip last frame
        self.motion_history   = deque(maxlen=10)  # rolling motion scores
        self.last_motion_time = time.time()
        self.still_start      = time.time()
        self.MOTION_THRESH    = 0.008             # normalised landmark movement

        # Frame dimensions (set on first frame)
        self.w = 640
        self.h = 480

    def update_blink(self, ear: float) -> bool:
        """
        Detects a blink using EAR threshold + hysteresis.
        Returns True if a new blink was just completed.
        """
        self.ear_history.append(ear)
        avg_ear = sum(self.ear_history) / len(self.ear_history)

        new_blink = False
        if avg_ear < self.EAR_BLINK_THRESH:
            self.in_blink = True
        elif self.in_blink:
            # Eye reopened → completed blink
            self.in_blink = False
            new_blink = True
            self.blink_timestamps.append(time.time())

        # Remove blinks older than 30 seconds
        cutoff = time.time() - 30
        while self.blink_timestamps and self.blink_timestamps[0] < cutoff:
            self.blink_timestamps.popleft()

        return new_blink

    def blink_rate(self) -> float:
        """Blinks per minute over the last 30 seconds."""
        cutoff = time.time() - 30
        recent = [t for t in self.blink_timestamps if t > cutoff]
        window = min(30, time.time() - (recent[0] if recent else time.time()))
        if window < 2:
            return 0.0  # not enough data yet
        return round((len(recent) / window) * 60, 1)

    def update_motion(self, nose_x: float, nose_y: float) -> float:
        """
        Computes head motion from nose tip movement between frames.
        Returns normalised motion score 0–100.
        """
        if self.prev_nose is None:
            self.prev_nose = (nose_x, nose_y)
            return 0.0

        dx = nose_x - self.prev_nose[0]
        dy = nose_y - self.prev_nose[1]
        dist = math.sqrt(dx*dx + dy*dy)   # normalised (0–1 range)
        self.prev_nose = (nose_x, nose_y)

        # Scale to 0–100
        motion = min(100.0, dist / self.MOTION_THRESH * 10)
        self.motion_history.append(motion)

        avg_motion = sum(self.motion_history) / len(self.motion_history)

        # Update still time
        if avg_motion > 5:
            self.last_motion_time = time.time()

        return round(avg_motion, 1)

    def still_seconds(self) -> float:
        return round(time.time() - self.last_motion_time, 1)


# ─── Frame Processor ──────────────────────────────────────

def process_frame(frame_bytes: bytes, state: ClientState) -> dict:
    """
    Decode JPEG, run MediaPipe, extract all metrics.
    Returns dict ready to JSON-serialize and send back.
    """
    # Decode JPEG bytes → numpy array → BGR image
    nparr = np.frombuffer(frame_bytes, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if frame is None:
        return {"face_detected": False, "error": "Could not decode frame"}

    h, w = frame.shape[:2]
    state.w, state.h = w, h

    # Convert BGR → RGB for MediaPipe
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(rgb)

    if not results.multi_face_landmarks:
        return {
            "face_detected":  False,
            "eye_openness":   0.0,
            "blink_rate":     state.blink_rate(),
            "head_motion":    0.0,
            "inactivity_sec": state.still_seconds(),
            "head_pose":      {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        }

    landmarks = results.multi_face_landmarks[0].landmark

    # ── Eye Aspect Ratio ───────────────────────────
    left_ear  = eye_aspect_ratio(landmarks, LEFT_EYE,  w, h)
    right_ear = eye_aspect_ratio(landmarks, RIGHT_EYE, w, h)
    avg_ear   = (left_ear + right_ear) / 2.0

    # Blink detection
    state.update_blink(avg_ear)

    # Map EAR to openness %
    eye_openness = ear_to_openness(avg_ear)

    # ── Head Pose ──────────────────────────────────
    pitch, yaw, roll = estimate_head_pose(landmarks, w, h)

    # ── Head Motion ────────────────────────────────
    nose = landmarks[NOSE_TIP]
    motion_score = state.update_motion(nose.x, nose.y)

    return {
        "face_detected":  True,
        "eye_openness":   eye_openness,
        "blink_rate":     state.blink_rate(),
        "head_motion":    motion_score,
        "inactivity_sec": state.still_seconds(),
        "head_pose": {
            "pitch": pitch,   # nodding up/down
            "yaw":   yaw,     # turning left/right
            "roll":  roll,    # tilting head
        },
        # Raw EAR for debugging
        "ear": round(avg_ear, 3),
    }


# ─── WebSocket Handler ────────────────────────────────────

async def handle_client(websocket):
    """One state object per connected client."""
    state   = ClientState()
    addr    = websocket.remote_address
    print(f"[CV] Client connected: {addr}")

    try:
        async for message in websocket:
            try:
                # Message format: "data:image/jpeg;base64,/9j/4AAQ..."
                # Strip the data URL prefix if present
                if isinstance(message, str):
                    if "," in message:
                        b64_data = message.split(",", 1)[1]
                    else:
                        b64_data = message
                    frame_bytes = base64.b64decode(b64_data)
                else:
                    # Raw bytes
                    frame_bytes = message

                result = process_frame(frame_bytes, state)
                await websocket.send(json.dumps(result))

            except Exception as e:
                print(f"[CV] Frame error: {e}")
                await websocket.send(json.dumps({
                    "face_detected": False,
                    "error": str(e)
                }))

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"[CV] Connection closed with error: {e}")
    finally:
        print(f"[CV] Client disconnected: {addr}")


# ─── Main ─────────────────────────────────────────────────

async def main():
    print("=" * 50)
    print("Nexora CV Server")
    print("WebSocket: ws://localhost:8001")
    print("=" * 50)

    async with websockets.serve(
        handle_client,
        "localhost",
        8001,
        max_size            = 10 * 1024 * 1024,   # 10MB max frame
        ping_interval       = 20,
        ping_timeout        = 10,
    ):
        print("[CV] Ready. Waiting for browser connections...")
        await asyncio.Future()   # run forever


if __name__ == "__main__":
    asyncio.run(main())