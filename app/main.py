from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.core.security import verify_password
from app.core.config import get_settings
from app.core.database import engine
from app.models import Base, SuperUser, Instructor, Student, Group, Lecture, Participant, Participation # noqa: F401 — ensures all models are registered
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

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
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


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        from app.core.database import SessionLocal
        form = await request.form()
        email, password = form.get("username"), form.get("password")
        db = SessionLocal()
        try:
            user = db.query(SuperUser).filter(SuperUser.email == email).first()
            if user and verify_password(password, user.password):
                request.session.update({"token": str(user.id)})
                return True
            return False
        finally:
            db.close()

    async def authenticate(self, request: Request) -> bool:
        return "token" in request.session

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True


class SuperUserAdmin(ModelView, model=SuperUser):
    column_list = [SuperUser.id, SuperUser.email]
    can_delete = True


class InstructorAdmin(ModelView, model=Instructor):
    column_list = [Instructor.id, Instructor.first_name, Instructor.last_name, Instructor.email, Instructor.university]
    column_searchable_list = [Instructor.email, Instructor.last_name]
    can_delete = True


class StudentAdmin(ModelView, model=Student):
    column_list = [Student.id, Student.student_id, Student.first_name, Student.last_name, Student.photo]
    column_searchable_list = [Student.first_name, Student.last_name, Student.student_id]
    column_formatters = {Student.embedding: lambda m, a: "✅ Ready" if m.embedding else "❌ Missing"}
    can_delete = True


class GroupAdmin(ModelView, model=Group):
    column_list = [Group.id, Group.name, Group.course, Group.instructor_id]
    column_searchable_list = [Group.name, Group.course]


class LectureAdmin(ModelView, model=Lecture):
    column_list = [Lecture.id, Lecture.group_id, Lecture.time]


class ParticipantAdmin(ModelView, model=Participant):
    column_list = [Participant.id, Participant.group_id, Participant.student_id]


class ParticipationAdmin(ModelView, model=Participation):
    column_list = [Participation.id, Participation.lecture_id, Participation.participant_id, Participation.status, Participation.confidence, Participation.recognized_at]
    column_searchable_list = [Participation.status]


admin = Admin(
    app,
    engine,
    authentication_backend=AdminAuth(secret_key=settings.SECRET_KEY),
)
admin.add_view(SuperUserAdmin)
admin.add_view(InstructorAdmin)
admin.add_view(StudentAdmin)
admin.add_view(GroupAdmin)
admin.add_view(LectureAdmin)
admin.add_view(ParticipantAdmin)
admin.add_view(ParticipationAdmin)