import os
import zipfile
import io
from pathlib import Path
import shutil
import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_instructor
from app.core.config import get_settings
from app.models import Student, Participant, Group
from app.schemas import StudentCreate, StudentUpdate, StudentResponse
from app.services.face_service import generate_embedding, embedding_to_bytes, embedding_cache

router = APIRouter(prefix="/students", tags=["students"])
settings = get_settings()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_student_or_404(student_id: int, db: Session) -> Student:
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


def _save_photo_from_upload(file: UploadFile, student_db_id: int) -> str:
    """Save uploaded photo file to storage, return path."""
    ext = os.path.splitext(file.filename)[1] or ".jpg"
    path = os.path.join(settings.STORAGE_PATH, f"{student_db_id}{ext}")
    os.makedirs(settings.STORAGE_PATH, exist_ok=True)
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return path


async def _save_photo_from_url(url: str, student_db_id: int) -> str:
    """Download photo from URL to storage, return path."""
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Could not download photo from URL: {url}")
    ext = ".jpg"
    path = os.path.join(settings.STORAGE_PATH, f"{student_db_id}{ext}")
    os.makedirs(settings.STORAGE_PATH, exist_ok=True)
    with open(path, "wb") as f:
        f.write(response.content)
    return path


def _generate_and_save_embedding(student: Student, db: Session):
    """Generate face embedding from student photo and save to DB + cache."""
    if not student.photo or not os.path.exists(student.photo):
        raise HTTPException(status_code=400, detail="Student has no photo to generate embedding from")
    try:
        embedding = generate_embedding(student.photo)
        student.embedding = embedding_to_bytes(embedding)
        db.commit()
        embedding_cache.add(
            student_id    = student.id,
            university_id = student.student_id,
            name          = f"{student.first_name} {student.last_name}",
            embedding     = embedding,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Face not detected in photo: {e}")


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("/", response_model=list[StudentResponse])
def list_students(
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    """List all students (globally — not per group)."""
    students = db.query(Student).all()
    return [StudentResponse.from_orm_with_embedding(s) for s in students]


@router.post("/", response_model=StudentResponse, status_code=201)
async def create_student(
    payload: StudentCreate,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    """Create a single student. Optionally provide photo_url to download photo."""
    existing = db.query(Student).filter(Student.student_id == payload.student_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Student with this ID already exists")

    student = Student(
        student_id = payload.student_id,
        first_name = payload.first_name,
        last_name  = payload.last_name,
    )
    db.add(student)
    db.commit()
    db.refresh(student)

    # Download photo from URL if provided
    if payload.photo_url:
        student.photo = await _save_photo_from_url(payload.photo_url, student.id)
        db.commit()
        _generate_and_save_embedding(student, db)
        db.refresh(student)

    return StudentResponse.from_orm_with_embedding(student)


@router.get("/{student_id}", response_model=StudentResponse)
def get_student(
    student_id: int,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    student = _get_student_or_404(student_id, db)
    return StudentResponse.from_orm_with_embedding(student)


@router.patch("/{student_id}", response_model=StudentResponse)
def update_student(
    student_id: int,
    payload: StudentUpdate,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    student = _get_student_or_404(student_id, db)
    if payload.first_name is not None:
        student.first_name = payload.first_name
    if payload.last_name is not None:
        student.last_name = payload.last_name
    db.commit()
    db.refresh(student)
    return StudentResponse.from_orm_with_embedding(student)


@router.delete("/{student_id}", status_code=204)
def delete_student(
    student_id: int,
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    student = _get_student_or_404(student_id, db)
    embedding_cache.remove(student.id)
    db.delete(student)
    db.commit()


# ── Photo upload ──────────────────────────────────────────────────────────────

@router.post("/{student_id}/photo", response_model=StudentResponse)
async def upload_photo(
    student_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    """Upload a photo for a student and auto-generate face embedding."""
    student = _get_student_or_404(student_id, db)
    student.photo = _save_photo_from_upload(file, student.id)
    db.commit()
    _generate_and_save_embedding(student, db)
    db.refresh(student)
    return StudentResponse.from_orm_with_embedding(student)


# ── Bulk import from Excel ────────────────────────────────────────────────────

@router.post("/import/excel", status_code=201)
async def import_from_excel(
    group_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    """
    Bulk import students from Excel file and add them to a group.

    Expected columns (case-insensitive):
      student_id | first_name | last_name | photo_url (optional)

    Students already in DB (by student_id) are skipped, not duplicated.
    They are still added to the group if not already in it.
    """
    import pandas as pd

    # Verify group belongs to instructor
    group = db.query(Group).filter(
        Group.id == group_id,
        Group.instructor_id == instructor.id,
    ).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Read Excel
    try:
        contents = await file.read()
        import io
        df = pd.read_excel(io.BytesIO(contents))
        df.columns = [c.strip().lower() for c in df.columns]  # normalize headers
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read Excel file: {e}")

    required = {"student_id", "first_name", "last_name"}
    if not required.issubset(set(df.columns)):
        raise HTTPException(
            status_code=400,
            detail=f"Excel must have columns: {required}. Found: {list(df.columns)}"
        )

    results = {
        "created":       [],
        "already_existed": [],
        "added_to_group":  [],
        "already_in_group": [],
        "photo_errors":  [],
        "total_rows":    len(df),
    }

    for _, row in df.iterrows():
        sid        = int(row["student_id"])
        first_name = str(row["first_name"]).strip()
        last_name  = str(row["last_name"]).strip()
        photo_url  = str(row.get("photo_url", "")).strip() or None

        # Create student if not exists
        student = db.query(Student).filter(Student.student_id == sid).first()
        if not student:
            student = Student(student_id=sid, first_name=first_name, last_name=last_name)
            db.add(student)
            db.commit()
            db.refresh(student)
            results["created"].append(sid)

            # Download photo if URL provided
            if photo_url:
                try:
                    student.photo = await _save_photo_from_url(photo_url, student.id)
                    db.commit()
                    _generate_and_save_embedding(student, db)
                    db.refresh(student)
                except Exception as e:
                    results["photo_errors"].append({"student_id": sid, "error": str(e)})
        else:
            results["already_existed"].append(sid)

        # Add to group if not already in it
        existing_participant = db.query(Participant).filter(
            Participant.group_id   == group_id,
            Participant.student_id == student.id,
        ).first()

        if not existing_participant:
            db.add(Participant(group_id=group_id, student_id=student.id))
            db.commit()
            results["added_to_group"].append(sid)
        else:
            results["already_in_group"].append(sid)

    return results

@router.post("/import/zip")
async def import_photos_zip(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    contents = await file.read()
    results = {"updated": [], "not_found": [], "face_errors": []}

    with zipfile.ZipFile(io.BytesIO(contents)) as z:
        for name in z.namelist():
            stem = Path(name).stem
            if not stem.isdigit():
                continue

            student_id = int(stem)
            student = db.query(Student).filter(
                Student.student_id == student_id
            ).first()

            if not student:
                results["not_found"].append(student_id)
                continue

            try:
                ext = Path(name).suffix or ".jpg"
                photo_path = os.path.join(
                    settings.STORAGE_PATH, f"{student.id}{ext}"
                )
                os.makedirs(settings.STORAGE_PATH, exist_ok=True)
                with open(photo_path, "wb") as f:
                    f.write(z.read(name))

                student.photo = photo_path
                db.commit()
                _generate_and_save_embedding(student, db)
                results["updated"].append(student_id)

            except Exception as e:
                results["face_errors"].append({
                    "student_id": student_id,
                    "error": str(e)
                })

    return results