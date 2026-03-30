"""
Quiz Service — Groq API version
================================
Uses Groq's free API with Llama 3 model.
Same logic as before, just different client.
"""

import os
import json
import re
from datetime import datetime
from typing import Optional
from groq import Groq

from app.db.database import get_db

# ── Groq client ───────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL  = "llama-3.3-70b-versatile"
   # best free model on Groq

# ── Collection helpers ────────────────────────────────────
def quiz_col():
    return get_db()["quizzes"]

def quiz_results_col():
    return get_db()["quiz_results"]


# ─────────────────────────────────────────
#  Question Generation — Topic Based
# ─────────────────────────────────────────

def generate_from_topic(
    topic:      str,
    count:      int  = 5,
    difficulty: str  = "medium",
    user_id:    str  = None,
) -> dict:
    count = max(3, min(count, 15))

    prompt = f"""Generate exactly {count} multiple choice questions about: {topic}
Difficulty: {difficulty}

Return ONLY a JSON array, no other text, no markdown, no explanation:
[
  {{
    "question": "Question text here?",
    "options": ["A) Option 1", "B) Option 2", "C) Option 3", "D) Option 4"],
    "correct": "A",
    "explanation": "Brief explanation of why A is correct."
  }}
]

Rules:
- correct field must be just the letter: A, B, C, or D
- All 4 options must be plausible
- Questions must test real understanding
- Explanation must be 1-2 sentences only
- Return ONLY the JSON array, nothing else"""

    response = client.chat.completions.create(
        model    = MODEL,
        messages = [{"role": "user", "content": prompt}],
        temperature = 0.7,
        max_tokens  = 2000,
    )

    raw       = response.choices[0].message.content.strip()
    questions = _parse_questions(raw)

    doc = {
        "user_id":    user_id,
        "type":       "topic",
        "topic":      topic,
        "difficulty": difficulty,
        "questions":  questions,
        "created_at": datetime.utcnow(),
    }
    result = quiz_col().insert_one(doc)
    doc["quiz_id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


# ─────────────────────────────────────────
#  Question Generation — Document Based
# ─────────────────────────────────────────

def generate_from_document(
    text:       str,
    count:      int  = 5,
    difficulty: str  = "medium",
    user_id:    str  = None,
    filename:   str  = "document",
) -> dict:
    count = max(3, min(count, 15))

    # Trim to avoid token limits (~3000 words)
    words = text.split()
    if len(words) > 3000:
        text = " ".join(words[:3000]) + "..."

    prompt = f"""Based on this document, generate exactly {count} multiple choice questions.
Difficulty: {difficulty}

DOCUMENT:
{text}

Return ONLY a JSON array, no other text:
[
  {{
    "question": "Question text here?",
    "options": ["A) Option 1", "B) Option 2", "C) Option 3", "D) Option 4"],
    "correct": "A",
    "explanation": "Brief explanation citing the document."
  }}
]

Rules:
- Questions must be answerable ONLY from the document
- correct field must be just: A, B, C, or D
- Return ONLY the JSON array, nothing else"""

    response = client.chat.completions.create(
        model    = MODEL,
        messages = [{"role": "user", "content": prompt}],
        temperature = 0.7,
        max_tokens  = 2000,
    )

    raw       = response.choices[0].message.content.strip()
    questions = _parse_questions(raw)

    doc = {
        "user_id":    user_id,
        "type":       "document",
        "topic":      filename,
        "difficulty": difficulty,
        "questions":  questions,
        "created_at": datetime.utcnow(),
    }
    result = quiz_col().insert_one(doc)
    doc["quiz_id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


# ─────────────────────────────────────────
#  PDF Text Extraction
# ─────────────────────────────────────────

def extract_pdf_text(file_bytes: bytes) -> str:
    try:
        import io
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        text   = ""
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"

        text = text.strip()
        if not text:
            raise ValueError("No text could be extracted from this PDF.")
        return text

    except ImportError:
        raise RuntimeError("pypdf not installed. Run: pip install pypdf")
    except Exception as e:
        raise RuntimeError(f"PDF extraction failed: {str(e)}")


# ─────────────────────────────────────────
#  Answer Evaluation
# ─────────────────────────────────────────

def evaluate_answers(quiz_id: str, answers: list) -> dict:
    from bson import ObjectId

    quiz = quiz_col().find_one({"_id": ObjectId(quiz_id)})
    if not quiz:
        raise ValueError("Quiz not found.")

    questions   = quiz["questions"]
    total       = len(questions)
    correct_cnt = 0
    breakdown   = []

    answer_map = {a["question_index"]: a["selected"].upper() for a in answers}

    for i, q in enumerate(questions):
        selected   = answer_map.get(i, "")
        is_correct = selected == q["correct"].upper()
        if is_correct:
            correct_cnt += 1
        breakdown.append({
            "question_index": i,
            "question":       q["question"],
            "selected":       selected,
            "correct":        q["correct"],
            "is_correct":     is_correct,
            "explanation":    q.get("explanation", ""),
        })

    score_pct = round((correct_cnt / total) * 100, 1) if total > 0 else 0

    return {
        "quiz_id":       quiz_id,
        "total":         total,
        "correct_count": correct_cnt,
        "score_pct":     score_pct,
        "breakdown":     breakdown,
        "topic":         quiz.get("topic", ""),
        "grade":         _grade(score_pct),
        "insight":       _insight(score_pct, quiz.get("topic", "")),
    }


# ─────────────────────────────────────────
#  Save Quiz Result
# ─────────────────────────────────────────

def save_quiz_result(
    user_id:    str,
    quiz_id:    str,
    session_id: Optional[str],
    score_pct:  float,
    correct:    int,
    total:      int,
    topic:      str,
) -> dict:
    doc = {
        "user_id":    user_id,
        "quiz_id":    quiz_id,
        "session_id": session_id,
        "topic":      topic,
        "score_pct":  score_pct,
        "correct":    correct,
        "total":      total,
        "grade":      _grade(score_pct),
        "taken_at":   datetime.utcnow(),
    }
    result = quiz_results_col().insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


# ─────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────

def _parse_questions(raw: str) -> list:
    """Parse Groq's response into questions list."""
    # Strip markdown fences
    raw = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()

    # Find JSON array
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        raise ValueError("Could not parse questions from AI response.")

    questions = json.loads(match.group())

    for i, q in enumerate(questions):
        if not all(k in q for k in ["question", "options", "correct"]):
            raise ValueError(f"Question {i} is missing required fields.")
        if len(q["options"]) != 4:
            raise ValueError(f"Question {i} must have exactly 4 options.")

    return questions


def _grade(score_pct: float) -> str:
    if score_pct >= 80: return "strong"
    if score_pct >= 60: return "mid"
    return "weak"


def _insight(score_pct: float, topic: str) -> str:
    if score_pct >= 80:
        return f"Excellent work on {topic}! You have a strong understanding of this topic."
    if score_pct >= 60:
        return f"Good effort on {topic}. Review the questions you got wrong to solidify your understanding."
    return f"You may need to revisit {topic}. Focus on the explanations for incorrect answers."