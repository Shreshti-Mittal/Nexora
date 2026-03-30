from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.database import create_indexes
from app.routers import auth, sessions, analytics, quiz, chat


app = FastAPI(
    title       = "Nexora API",
    description = "Study engagement tracking — MongoDB backend.",
    version     = "2.0.0",
    docs_url    = "/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["http://localhost:8080", "http://127.0.0.1:8080", "*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(analytics.router)
app.include_router(quiz.router)
app.include_router(chat.router)

@app.on_event("startup")
def on_startup():
    create_indexes()
    print("Nexora API (MongoDB) started.")

@app.get("/")
def root():
    return {"service": "Nexora API", "db": "MongoDB", "status": "running", "docs": "/docs"}

@app.get("/health")
def health():
    return {"status": "ok"}