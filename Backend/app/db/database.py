"""
MongoDB connection.
All collections live in one database (default: "nexora").
Collections used:
  - users
  - study_sessions
  - engagement_metrics
  - session_summary
"""

import os
from pymongo import MongoClient
from pymongo.collection import Collection
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME     = os.getenv("DB_NAME", "nexora")

# Single client — reused across the app
_client = MongoClient(MONGODB_URL)
_db     = _client[DB_NAME]


def get_db():
    """Return the database instance."""
    return _db


# ── Typed collection accessors ─────────────────────
def users_col()   -> Collection: return _db["users"]
def sessions_col()-> Collection: return _db["study_sessions"]
def metrics_col() -> Collection: return _db["engagement_metrics"]
def summary_col() -> Collection: return _db["session_summary"]


# ── Indexes (called once on startup) ───────────────
def create_indexes():
    users_col().create_index("email", unique=True)
    sessions_col().create_index("user_id")
    sessions_col().create_index("status")
    metrics_col().create_index("session_id")
    metrics_col().create_index("recorded_at")
    summary_col().create_index("session_id", unique=True)
    print("MongoDB indexes created.")