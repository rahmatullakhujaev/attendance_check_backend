from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_instructor
from app.models import Lecture, Group, Participant, Participation, ParticipationStatus
from app.schemas import LectureResponse, ParticipationResponse, StatusUpdateRequest

router = APIRouter(prefix="/lectures", tags=["lectures"])


@router.get("/", response_model=list[LectureResponse])
def list_lectures(
    group_id: int,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    """List all lectures for a group."""
    group = _get_group_or_404(group_id, instructor.id, db)
    return db.query(Lecture).filter(Lecture.group_id == group.id).all()


@router.post("/", response_model=LectureResponse, status_code=201)
def create_lecture(
    group_id: int,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    """
    Start a new lecture for a group.
    Time is auto-set to now.
    Also auto-creates participation records for all students in the group
    with status=absent by default.
    """
    group = _get_group_or_404(group_id, instructor.id, db)

    # Create the lecture
    lecture = Lecture(
        group_id=group.id,
        time=datetime.now(timezone.utc),
    )
    db.add(lecture)
    db.commit()
    db.refresh(lecture)

    # Auto-create absent participation records for every student in group
    participants = db.query(Participant).filter(Participant.group_id == group.id).all()
    for p in participants:
        db.add(Participation(
            lecture_id     = lecture.id,
            participant_id = p.id,
            status         = ParticipationStatus.absent,
        ))
    db.commit()

    return lecture


@router.get("/{lecture_id}", response_model=LectureResponse)
def get_lecture(
    lecture_id: int,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    return _get_lecture_or_404(lecture_id, instructor.id, db)


@router.get("/{lecture_id}/attendance")
def get_attendance(
    lecture_id: int,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    _get_lecture_or_404(lecture_id, instructor.id, db)
    participations = db.query(Participation).filter(
        Participation.lecture_id == lecture_id
    ).all()

    result = []
    for p in participations:
        student = p.participant.student
        result.append({
            "id":             p.id,
            "lecture_id":     p.lecture_id,
            "participant_id": p.participant_id,
            "status":         p.status.value,
            "confidence":     p.confidence,
            "recognized_at":  p.recognized_at.isoformat() if p.recognized_at else None,
            "student_name":   f"{student.first_name} {student.last_name}",
            "student_id":     student.student_id,
        })
    return result


@router.patch("/{lecture_id}/attendance/{participation_id}", response_model=ParticipationResponse)
def update_attendance_status(
    lecture_id: int,
    participation_id: int,
    payload: StatusUpdateRequest,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    """
    Manually update a student's attendance status.
    Only instructor can set status to 'late'.
    """
    _get_lecture_or_404(lecture_id, instructor.id, db)

    participation = db.query(Participation).filter(
        Participation.id         == participation_id,
        Participation.lecture_id == lecture_id,
    ).first()
    if not participation:
        raise HTTPException(status_code=404, detail="Participation record not found")

    participation.status = payload.status
    db.commit()
    db.refresh(participation)
    return participation


@router.delete("/{lecture_id}", status_code=204)
def delete_lecture(
    lecture_id: int,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    lecture = _get_lecture_or_404(lecture_id, instructor.id, db)
    db.delete(lecture)
    db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_group_or_404(group_id: int, instructor_id: int, db: Session) -> Group:
    group = db.query(Group).filter(
        Group.id == group_id,
        Group.instructor_id == instructor_id,
    ).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


def _get_lecture_or_404(lecture_id: int, instructor_id: int, db: Session) -> Lecture:
    lecture = db.query(Lecture).join(Group).filter(
        Lecture.id == lecture_id,
        Group.instructor_id == instructor_id,
    ).first()
    if not lecture:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return lecture