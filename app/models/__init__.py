"""
All database models in one place.
Matches the schema from your diagram exactly.
"""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    BigInteger, String, DateTime, ForeignKey,
    Enum as SAEnum, LargeBinary, Float
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.core.database import Base


# ── Enums ─────────────────────────────────────────────────────────────────────

class ParticipationStatus(str, enum.Enum):
    present = "present"
    late    = "late"
    absent  = "absent"


# ── Instructor ────────────────────────────────────────────────────────────────

class Instructor(Base):
    __tablename__ = "instructor"

    id:         Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str]           = mapped_column(String(100))
    last_name:  Mapped[str]           = mapped_column(String(100))
    university: Mapped[str]           = mapped_column(String(200))
    field:      Mapped[str]           = mapped_column(String(100))
    email:      Mapped[str]           = mapped_column(String(200), unique=True, index=True)
    password:   Mapped[str]           = mapped_column(String(255))  # bcrypt hash

    groups: Mapped[list["Group"]] = relationship("Group", back_populates="instructor")

    def __str__(self):
        return f"Ins {self.first_name}{self.last_name}"


# ── Group ─────────────────────────────────────────────────────────────────────

class Group(Base):
    __tablename__ = "group"

    id:            Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    instructor_id: Mapped[int]           = mapped_column(BigInteger, ForeignKey("instructor.id"))
    name:          Mapped[str]           = mapped_column(String(100))
    course:        Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    instructor:   Mapped["Instructor"]        = relationship("Instructor", back_populates="groups")
    participants: Mapped[list["Participant"]] = relationship("Participant", back_populates="group")
    lectures:     Mapped[list["Lecture"]]     = relationship("Lecture", back_populates="group")

    def __str__(self):
        return f"{self.id}_{self.name} ({self.course or 'no course'})"


# ── Student ───────────────────────────────────────────────────────────────────

class Student(Base):
    __tablename__ = "student"

    id:         Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[int]           = mapped_column(BigInteger, unique=True, index=True)  # university student ID
    first_name: Mapped[str]           = mapped_column(String(100))
    last_name:  Mapped[str]           = mapped_column(String(100))

    # Path to photo file on disk e.g. /app/storage/photos/42.jpg
    photo:      Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Pre-computed ArcFace embedding stored as binary blob
    # 512 float32 values = 2048 bytes — loaded into RAM on server startup
    embedding:  Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    participants: Mapped[list["Participant"]] = relationship("Participant", back_populates="student")

    def __str__(self):
        return f"{self.first_name} {self.last_name} [{self.student_id}]"


# ── Participant (student in a group) ──────────────────────────────────────────

class Participant(Base):
    __tablename__ = "participants"

    id:         Mapped[int] = mapped_column(BigInteger, primary_key=True)
    group_id:   Mapped[int] = mapped_column(BigInteger, ForeignKey("group.id"))
    student_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("student.id"))

    group:   Mapped["Group"]   = relationship("Group", back_populates="participants")
    student: Mapped["Student"] = relationship("Student", back_populates="participants")

    def __str__(self):
        return f"Participant: {self.group_id}:{self.student_id}"


# ── Lecture ───────────────────────────────────────────────────────────────────

class Lecture(Base):
    __tablename__ = "lectures"

    id:       Mapped[int]      = mapped_column(BigInteger, primary_key=True)
    group_id: Mapped[int]      = mapped_column(BigInteger, ForeignKey("group.id"))
    time:     Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    group:         Mapped["Group"]              = relationship("Group", back_populates="lectures")
    participations: Mapped[list["Participation"]] = relationship("Participation", back_populates="lecture")

    def __str__(self):
        return f"Lecture #{self.group_id} — {self.time.strftime('%Y-%m-%d %H:%M')}"


# ── Participation (attendance record) ────────────────────────────────────────

class Participation(Base):
    __tablename__ = "participation"

    id:             Mapped[int]                  = mapped_column(BigInteger, primary_key=True)
    lecture_id:     Mapped[int]                  = mapped_column(BigInteger, ForeignKey("lectures.id"))
    participant_id: Mapped[int]                  = mapped_column(BigInteger, ForeignKey("participants.id"))

    # present / late / absent
    # late can only be set manually by instructor, not by face recognition
    status:         Mapped[ParticipationStatus]  = mapped_column(
        SAEnum(ParticipationStatus), default=ParticipationStatus.absent
    )

    # Face recognition metadata — useful for audit / dispute
    confidence:     Mapped[Optional[float]]      = mapped_column(Float, nullable=True)
    recognized_at:  Mapped[Optional[datetime]]   = mapped_column(DateTime(timezone=True), nullable=True)

    lecture:     Mapped["Lecture"]      = relationship("Lecture", back_populates="participations")
    participant: Mapped["Participant"]  = relationship("Participant")

    def __str__(self):
        return f"Participation #{self.id} — {self.status.value}"


class SuperUser(Base):
    __tablename__ = "superuser"

    id:       Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email:    Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password: Mapped[str] = mapped_column(String(255))

    def __str__(self):
        return self.email