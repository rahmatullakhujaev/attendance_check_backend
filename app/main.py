from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import engine
from app.models import Base  # noqa: F401 — ensures all models are registered
from app.routers import auth, groups, students, lectures, attendance
from app.services.face_service import embedding_cache

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs on startup and shutdown.
    On startup: load all student face embeddings into RAM for fast recognition.
    """
    print("🚀 Starting up...")
    embedding_cache.load()
    print(f"✅ Loaded {len(embedding_cache.cache)} student embeddings into memory")
    yield
    print("👋 Shutting down...")


app = FastAPI(
    title="Attendance System API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
# More routers added here as we build features:
app.include_router(groups.router)
app.include_router(students.router)
"""
The Excel file format expected:

student_id | first_name | last_name | photo_url
12345      | John       | Doe       | https://...  (optional)
"""
app.include_router(lectures.router)
app.include_router(attendance.router)


@app.get("/health")
def health():
    return {"status": "ok", "embeddings_loaded": len(embedding_cache.cache)}
