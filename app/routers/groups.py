from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_instructor
from app.models import Group, Participant
from app.schemas import GroupCreate, GroupUpdate, GroupResponse

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("/", response_model=list[GroupResponse])
def list_groups(
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    """List all groups belonging to the current instructor."""
    return db.query(Group).filter(Group.instructor_id == instructor.id).all()


@router.post("/", response_model=GroupResponse, status_code=201)
def create_group(
    payload: GroupCreate,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    group = Group(
        instructor_id=instructor.id,
        name=payload.name,
        course=payload.course,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.get("/{group_id}", response_model=GroupResponse)
def get_group(
    group_id: int,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    group = _get_group_or_404(group_id, instructor.id, db)
    return group


@router.patch("/{group_id}", response_model=GroupResponse)
def update_group(
    group_id: int,
    payload: GroupUpdate,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    group = _get_group_or_404(group_id, instructor.id, db)
    if payload.name is not None:
        group.name = payload.name
    if payload.course is not None:
        group.course = payload.course
    db.commit()
    db.refresh(group)
    return group


@router.delete("/{group_id}", status_code=204)
def delete_group(
    group_id: int,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    group = _get_group_or_404(group_id, instructor.id, db)
    db.delete(group)
    db.commit()


@router.get("/{group_id}/participants", response_model=list)
def list_participants(
    group_id: int,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    """List all students in a group."""
    _get_group_or_404(group_id, instructor.id, db)
    participants = (
        db.query(Participant)
        .filter(Participant.group_id == group_id)
        .all()
    )
    return [
        {
            "id":         p.id,
            "group_id":   p.group_id,
            "student_id": p.student.student_id,
            "first_name": p.student.first_name,
            "last_name":  p.student.last_name,
            "photo":      p.student.photo,
            "has_embedding": p.student.embedding is not None,
        }
        for p in participants
    ]


def _get_group_or_404(group_id: int, instructor_id: int, db: Session) -> Group:
    group = db.query(Group).filter(
        Group.id == group_id,
        Group.instructor_id == instructor_id,
    ).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group