# Nexora — Study Engagement & Learning Analytics Platform

> *Know when you're actually learning.*

Nexora is a web-based study companion that uses real-time computer vision to track engagement during study sessions, validates learning through AI-generated quizzes, and correlates focus data with quiz performance on an analytics dashboard.

---

## Demo

| Page | Description |
|------|-------------|
| `index.html` | Landing page with animated neural network background |
| `login.html` | Register / Login with dark-light toggle |
| `session.html` | Live study session with MediaPipe CV tracking |
| `quiz.html` | AI-generated MCQs from topic or uploaded PDF |
| `dashboard.html` | Analytics — focus trends, topic performance, correlation |

---

## My Contribution (Study Session + Auth Backend)

This project was built as a team. My ownership covers:

### 1. Auth System
- `POST /auth/register` — Register with camera consent
- `POST /auth/login` — JWT token authentication
- `GET /auth/me` — Current user info
- Passwords hashed with bcrypt, tokens valid for 7 days

### 2. Study Session Lifecycle
- `POST /sessions/start` — Create a new session with topic, goal, duration
- `POST /sessions/{id}/pause` — Pause with timestamp recording
- `POST /sessions/{id}/resume` — Resume with accumulated pause time
- `POST /sessions/{id}/end` — End session, compute and store summary
- `GET /sessions/{id}` — Live status and elapsed time

### 3. Engagement Metrics API
Every 2 seconds the frontend posts raw camera signals. The backend computes:

```
POST /sessions/{id}/metrics
Body: { eye_openness, blink_rate, head_motion, inactivity_sec }
Returns: { focus_score, fatigue_index, inactivity_ratio, alert }
```

### 4. Scoring Engine (`app/services/scoring.py`)

**Focus Score (0–100)**
```
score = (eye_openness × 0.55) + (blink_score × 0.25) + (motion_score × 0.20)
score -= inactivity_penalty   # up to 45 points if still > 30 seconds
Hard gate: eye_openness < 35 caps score at 42
```

**Fatigue Index (0–100)**
```
fatigue = (eye_fatigue × 0.65) + (blink_strain × 0.20)
        + (motion_slump × 0.10) + (focus_history × 0.05)
Eye curve is aggressive — drooping eyes = clear fatigue signal
```

**Inactivity Ratio**
```
ratio = inactivity_sec / 2.0  (capped at 1.0)
```

### 5. Smart Alert System
Context-aware alerts with 3-minute warmup before first alert fires:
- Break reminders every 25 minutes with science-backed messaging
- Drowsiness detection with actionable recovery steps
- Low focus guidance based on elapsed session time
- Eye strain, inactivity, and fatigue alerts with specific advice

### 6. Session Summary
On session end, all metric ticks are aggregated:
- Average, peak, minimum focus score
- Total inactivity seconds and ratio
- Focus drop count
- Pattern tags: `high_focus`, `fatigued`, `frequent_drops`, etc.

### 7. Analytics Endpoints
```
GET /analytics/session/{id}   — deep-dive with focus timeline
GET /analytics/user/{id}      — dashboard: focus trend, topic performance,
                                 weekly distribution, rule-based insights
```

### 8. Real-Time CV Server (`cv_server.py`)
A separate WebSocket server (port 8001) using MediaPipe Face Mesh:
- **468 facial landmarks** detected per frame at 5fps
- **Eye Aspect Ratio (EAR)** — Soukupová & Čech (2016) formula for real eye openness
- **Blink detection** — EAR threshold + hysteresis, rolling 30s window
- **Head pose** — OpenCV solvePnP with 6 reference points → pitch/yaw/roll
- **Head motion** — normalised nose-tip movement between frames
- Falls back to canvas pixel analysis if CV server is not running

### 9. Picture-in-Picture Monitor
Session page supports browser PiP API — composites live camera feed with focus score, timer, and signals onto a floating window so students can monitor while using other apps.

### 10. Focus Coach Chatbot
```
POST /chat/coach
```
Context-aware AI coach using Groq (Llama 3.3) — knows current focus score,
fatigue level, topic, and elapsed time. Gives specific actionable study advice.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | HTML, CSS, JavaScript (vanilla) |
| Backend | Python, FastAPI |
| Database | MongoDB (PyMongo) |
| CV | MediaPipe Face Mesh, OpenCV |
| AI | Groq API (Llama 3.3-70b) |
| Auth | JWT (python-jose), bcrypt |

---

## Project Structure

```
Nexora/
├── Frontend/
│   ├── index.html          ← Landing page
│   ├── login.html          ← Auth 
│   ├── session.html        ← Study session 
│   ├── quiz.html           ← Quiz page
│   └── dashboard.html      ← Analytics dashboard 
│
└── Backend/
    ├── cv_server.py        ← MediaPipe WebSocket server 
    ├── requirements.txt
    ├── .env.example
    └── app/
        ├── main.py
        ├── db/
        │   ├── database.py ← MongoDB connection
        │   └── auth.py     ← JWT helpers 
        ├── routers/
        │   ├── auth.py     ← Auth endpoints 
        │   ├── sessions.py ← Session endpoints 
        │   ├── analytics.py← Analytics endpoints 
        │   ├── quiz.py     ← Quiz endpoints
        │   └── chat.py     ← Chatbot endpoints 
        └── services/
            ├── scoring.py          ← Focus/fatigue algorithm 
            ├── session_service.py  ← Session logic 
            ├── analytics_service.py← Dashboard data 
            ├── quiz_service.py     ← AI quiz generation
            └── chat_service.py     ← Coach chatbot 
```

---

## Running Locally

### Prerequisites
- Python 3.11
- MongoDB running locally
- Node.js (optional, for live reload)

### Setup

**1. Clone the repository**
```bash
git clone https://github.com/YOUR_USERNAME/nexora.git
cd nexora
```

**2. Create virtual environment**
```bash
cd Backend
python -m venv .venv
.venv\Scripts\activate       # Windows
source .venv/bin/activate    # Mac/Linux
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Configure environment**
```bash
cp .env.example .env
# Edit .env and add your keys
```

`.env` file:
```
MONGODB_URL=mongodb://localhost:27017
DB_NAME=nexora
SECRET_KEY=your-secret-key-here
GROQ_API_KEY=your-groq-key-here
```

**5. Start all three servers**

Terminal 1 — FastAPI backend:
```bash
uvicorn app.main:app --reload --port 8000
```

Terminal 2 — CV WebSocket server:
```bash
python cv_server.py
```

Terminal 3 — Frontend:
```bash
cd ../Frontend
python -m http.server 8080
```

**6. Open the app**
```
http://127.0.0.1:8080/index.html
```

API docs available at: `http://127.0.0.1:8000/docs`

---

## How the Focus Score Works

The scoring engine is rule-based, not ML. Each metric tick:

1. **Eye Openness (55% weight)** — EAR converted to 0–100. Primary alertness signal. Hard gate: EAR < 35 caps focus at 42.
2. **Blink Rate (25% weight)** — Optimal 10–16/min scores 100. Penalises strain (>22) and staring (<8).
3. **Head Motion (20% weight)** — Some motion = engaged. None = absent. Too much = distracted.
4. **Inactivity Penalty** — Direct point deduction: still > 30s subtracts up to 45 points.

Fatigue Index uses the same signals with eye openness at 65% weight — drooping eyes are the clearest fatigue indicator.

---

## API Reference

All protected endpoints require:
```
Authorization: Bearer <token>
```

Full interactive docs: `http://localhost:8000/docs`

---

## Acknowledgements

- Eye Aspect Ratio formula: Soukupová & Čech (2016) — *Real-Time Eye Blink Detection using Facial Landmarks*
- MediaPipe Face Mesh: Google LLC
- Quiz generation: Groq API (Llama 3.3-70b)
