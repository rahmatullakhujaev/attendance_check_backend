"""
Pydantic schemas — request bodies and response shapes for all endpoints.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr
from app.models import ParticipationStatus


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshRequest(BaseModel):
    refresh_token: str


# ── Instructor ────────────────────────────────────────────────────────────────

class InstructorCreate(BaseModel):
    first_name: str
    last_name: str
    university: str
    field: str
    email: EmailStr
    password: str

class InstructorResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    university: str
    field: str
    email: str

    model_config = {"from_attributes": True}


# ── Group ─────────────────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    name: str
    course: Optional[str] = None

class GroupUpdate(BaseModel):
    name: Optional[str] = None
    course: Optional[str] = None

class GroupResponse(BaseModel):
    id: int
    name: str
    course: Optional[str]
    instructor_id: int

    model_config = {"from_attributes": True}


# ── Student ───────────────────────────────────────────────────────────────────

class StudentCreate(BaseModel):
    student_id: int           # university student ID
    first_name: str
    last_name: str
    photo_url: Optional[str] = None   # URL to download photo from

class StudentUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class StudentResponse(BaseModel):
    id: int
    student_id: int
    first_name: str
    last_name: str
    photo: Optional[str]
    has_embedding: bool       # True if face embedding is ready for recognition

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_embedding(cls, student):
        return cls(
            id=student.id,
            student_id=student.student_id,
            first_name=student.first_name,
            last_name=student.last_name,
            photo=student.photo,
            has_embedding=student.embedding is not None,
        )


# ── Participant ───────────────────────────────────────────────────────────────

class ParticipantResponse(BaseModel):
    id: int
    group_id: int
    student_id: int
    student: StudentResponse

    model_config = {"from_attributes": True}


# ── Lecture ───────────────────────────────────────────────────────────────────

class LectureCreate(BaseModel):
    group_id: int
    # time is auto-set to now() on creation, no need to pass it

class LectureResponse(BaseModel):
    id: int
    group_id: int
    time: datetime

    model_config = {"from_attributes": True}


# ── Participation / Attendance ────────────────────────────────────────────────

class ParticipationResponse(BaseModel):
    id: int
    lecture_id: int
    participant_id: int
    status: ParticipationStatus
    confidence: Optional[float]
    recognized_at: Optional[datetime]

    model_config = {"from_attributes": True}

class StatusUpdateRequest(BaseModel):
    """Instructor manually changes a status (only late is exclusive to instructor)."""
    status: ParticipationStatus

class AttendanceResult(BaseModel):
    """Returned after face recognition scan."""
    face_index: int
    student_id: Optional[int]        # None if unknown
    student_name: Optional[str]
    status: Optional[ParticipationStatus]
    confidence: Optional[float]
    matched: bool


class AttendanceScanResponse(BaseModel):
    lecture_id: int
    faces_detected: int
    results: list[AttendanceResult]
    unmatched_faces: int
    processing_time_seconds: float
