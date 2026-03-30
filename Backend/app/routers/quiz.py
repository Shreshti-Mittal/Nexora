"""
Quiz Router
===========
POST /quiz/generate          — generate from topic
POST /quiz/upload            — upload PDF + generate
POST /quiz/evaluate          — submit answers + get score
POST /quiz/result            — save result linked to session
GET  /quiz/results/{user_id} — get user's quiz history
"""

import io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId

from app.db.auth import get_current_user

router = APIRouter(prefix="/quiz", tags=["Quiz"])

# ── Lazy import quiz_service so missing anthropic key
#    doesn't crash the whole app on startup
def get_quiz_service():
    try:
        from app.services import quiz_service
        return quiz_service
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Quiz service unavailable: {str(e)}"
        )


# ─────────────────────────────────────────
#  Request / Response Models
# ─────────────────────────────────────────

class GenerateRequest(BaseModel):
    topic:      str
    count:      int         = 5
    difficulty: str         = "medium"   # easy / medium / hard


class AnswerItem(BaseModel):
    question_index: int
    selected:       str     # "A", "B", "C", or "D"


class EvaluateRequest(BaseModel):
    quiz_id: str
    answers: List[AnswerItem]


class SaveResultRequest(BaseModel):
    quiz_id:    str
    session_id: Optional[str] = None
    score_pct:  float
    correct:    int
    total:      int
    topic:      str


# ─────────────────────────────────────────
#  POST /quiz/generate  (topic-based)
# ─────────────────────────────────────────

@router.post("/generate")
def generate_quiz(
    body:         GenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Generate MCQs for a given topic using Claude API.
    Returns quiz_id + questions (without revealing correct answers).
    """
    qs = get_quiz_service()
    try:
        quiz = qs.generate_from_topic(
            topic      = body.topic,
            count      = body.count,
            difficulty = body.difficulty,
            user_id    = current_user["id"],
        )
        # Return questions WITHOUT correct answers (frontend shouldn't know yet)
        safe_questions = [
            {
                "question_index": i,
                "question":       q["question"],
                "options":        q["options"],
            }
            for i, q in enumerate(quiz["questions"])
        ]
        return {
            "quiz_id":    quiz["quiz_id"],
            "topic":      quiz["topic"],
            "difficulty": quiz["difficulty"],
            "count":      len(safe_questions),
            "questions":  safe_questions,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /quiz/upload  (PDF-based)
# ─────────────────────────────────────────

@router.post("/upload")
async def upload_and_generate(
    file:         UploadFile     = File(...),
    count:        int            = Form(default=5),
    difficulty:   str            = Form(default="medium"),
    current_user: dict           = Depends(get_current_user),
):
    """
    Accept a PDF upload, extract text, generate MCQs from content.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported."
        )

    qs = get_quiz_service()

    try:
        file_bytes = await file.read()
        text       = qs.extract_pdf_text(file_bytes)

        quiz = qs.generate_from_document(
            text       = text,
            count      = count,
            difficulty = difficulty,
            user_id    = current_user["id"],
            filename   = file.filename,
        )

        safe_questions = [
            {
                "question_index": i,
                "question":       q["question"],
                "options":        q["options"],
            }
            for i, q in enumerate(quiz["questions"])
        ]
        return {
            "quiz_id":    quiz["quiz_id"],
            "topic":      quiz["topic"],
            "difficulty": quiz["difficulty"],
            "count":      len(safe_questions),
            "questions":  safe_questions,
            "source":     "document",
        }
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /quiz/evaluate
# ─────────────────────────────────────────

@router.post("/evaluate")
def evaluate_quiz(
    body:         EvaluateRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Submit answers for a quiz and get score + detailed breakdown.
    This reveals correct answers and explanations.
    """
    qs = get_quiz_service()
    try:
        result = qs.evaluate_answers(
            quiz_id = body.quiz_id,
            answers = [a.dict() for a in body.answers],
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  POST /quiz/result  (save to DB)
# ─────────────────────────────────────────

@router.post("/result")
def save_result(
    body:         SaveResultRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Save a quiz result to the database.
    Links to session_id for dashboard analytics.
    Call this AFTER /evaluate.
    """
    qs = get_quiz_service()
    try:
        saved = qs.save_quiz_result(
            user_id    = current_user["id"],
            quiz_id    = body.quiz_id,
            session_id = body.session_id,
            score_pct  = body.score_pct,
            correct    = body.correct,
            total      = body.total,
            topic      = body.topic,
        )
        return {"message": "Result saved.", "result_id": saved["id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
#  GET /quiz/results/{user_id}
# ─────────────────────────────────────────

@router.get("/results/{user_id}")
def get_user_results(
    user_id:      str,
    current_user: dict = Depends(get_current_user),
):
    """Get all quiz results for a user (for dashboard history)."""
    if user_id != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied.")

    qs = get_quiz_service()
    results = list(
        qs.quiz_results_col()
        .find({"user_id": user_id})
        .sort("taken_at", -1)
        .limit(20)
    )
    for r in results:
        r["id"] = str(r.pop("_id"))
    return {"results": results}