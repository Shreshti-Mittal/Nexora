"""
Chat Router
===========
POST /chat/coach   — focus coach during study session
POST /chat/explain — explain a quiz question differently
"""

import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from groq import Groq
from app.db.auth import get_current_user

router = APIRouter(prefix="/chat", tags=["Chat"])
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL  = "llama-3.3-70b-versatile"


# ─────────────────────────────────────────
#  Focus Coach — Session Context Aware
# ─────────────────────────────────────────

class CoachRequest(BaseModel):
    message:       str
    topic:         Optional[str]  = "General Study"
    focus_score:   Optional[float]= 70.0
    fatigue_index: Optional[float]= 20.0
    elapsed_min:   Optional[int]  = 0
    history:       Optional[list] = []   # [{role, content}]


@router.post("/coach")
def focus_coach(body: CoachRequest, current_user: dict = Depends(get_current_user)):
    """
    Study session focus coach.
    Knows current focus score, fatigue, topic, and elapsed time.
    Gives specific, actionable study advice.
    """
    focus_level = (
        "very high (deep focus)"    if body.focus_score >= 80 else
        "good"                       if body.focus_score >= 65 else
        "moderate"                   if body.focus_score >= 50 else
        "low — student may be struggling"
    )
    fatigue_level = (
        "low (fresh)"    if body.fatigue_index < 25 else
        "building up"    if body.fatigue_index < 50 else
        "high — student is tired"
    )

    system = f"""You are Nexora's study coach — an expert at helping students stay focused and learn effectively.

Current session context:
- Topic: {body.topic}
- Time studied: {body.elapsed_min} minutes
- Focus level: {focus_level} ({body.focus_score:.0f}/100)
- Fatigue: {fatigue_level} ({body.fatigue_index:.0f}/100)

Your role:
- Give specific, practical advice based on the student's current state
- If focus is low, suggest concrete techniques (Pomodoro, active recall, etc.)
- If fatigue is high, recommend breaks or energy management
- Keep responses SHORT — 2-4 sentences max unless explaining a concept
- Be encouraging but honest
- Never give generic advice — always tie it to their current topic and state
- If asked about the topic itself, help them understand it"""

    messages = [{"role": "system", "content": system}]

    # Add conversation history (last 6 messages to save tokens)
    for h in (body.history or [])[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})

    messages.append({"role": "user", "content": body.message})

    try:
        response = client.chat.completions.create(
            model       = MODEL,
            messages    = messages,
            max_tokens  = 300,
            temperature = 0.7,
        )
        reply = response.choices[0].message.content.strip()
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  Quiz Explainer — "I still don't get it"
# ─────────────────────────────────────────

class ExplainRequest(BaseModel):
    question:    str
    options:     list          # ["A) ...", "B) ...", ...]
    correct:     str           # "A"
    explanation: str           # original explanation
    user_answer: str           # what the student picked
    topic:       Optional[str] = ""
    message:     Optional[str] = "Can you explain this differently?"
    history:     Optional[list]= []


@router.post("/explain")
def explain_question(body: ExplainRequest, current_user: dict = Depends(get_current_user)):
    """
    Explains a quiz question the student got wrong.
    Uses a different approach than the original explanation.
    """
    options_str = "\n".join(body.options)
    correct_option = next(
        (o for o in body.options if o.startswith(body.correct + ")")),
        body.correct
    )

    system = f"""You are a patient tutor helping a student understand why they got a question wrong.

Question: {body.question}
Options:
{options_str}
Correct answer: {correct_option}
Student chose: {body.user_answer}
Original explanation: {body.explanation}
Topic: {body.topic}

Your job:
- The student said: "{body.message}"
- Explain WHY the correct answer is right using a DIFFERENT approach than the original explanation
- Use an analogy, real-world example, or simpler breakdown
- Explain WHY their chosen answer is wrong (not just that it is)
- Be warm and encouraging — getting things wrong is how we learn
- Keep it under 5 sentences unless they ask for more detail"""

    messages = [{"role": "system", "content": system}]
    for h in (body.history or [])[-4:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": body.message})

    try:
        response = client.chat.completions.create(
            model       = MODEL,
            messages    = messages,
            max_tokens  = 400,
            temperature = 0.7,
        )
        reply = response.choices[0].message.content.strip()
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))