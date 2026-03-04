import os
import shutil
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_instructor
from app.models import Lecture, Group, Participant, Participation, ParticipationStatus
from app.schemas import AttendanceScanResponse, AttendanceResult
from app.services.face_service import recognize_faces
from app.core.config import get_settings

router = APIRouter(prefix="/attendance", tags=["attendance"])
settings = get_settings()

TMP_PATH = "/tmp/attendance_scans"


@router.post("/scan/{lecture_id}", response_model=AttendanceScanResponse)
async def scan_attendance(
    lecture_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    instructor=Depends(get_current_instructor),
):
    """
    Main attendance endpoint.

    1. Instructor uploads a classroom photo
    2. Detect all faces in the photo
    3. Match each face against students in the group
    4. Update participation records: absent -> present
    5. Return full scan result

    Already-present students are not downgraded.
    Late status is never touched by scan — only instructor can set that.
    """
    # Verify lecture belongs to instructor
    lecture = db.query(Lecture).join(Group).filter(
        Lecture.id == lecture_id,
        Group.instructor_id == instructor.id,
    ).first()
    if not lecture:
        raise HTTPException(status_code=404, detail="Lecture not found")

    # Save uploaded photo temporarily
    os.makedirs(TMP_PATH, exist_ok=True)
    tmp_file = os.path.join(TMP_PATH, f"scan_{lecture_id}_{int(datetime.now().timestamp())}.jpg")
    with open(tmp_file, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        # Get all participant student DB IDs for this group
        participants = db.query(Participant).filter(
            Participant.group_id == lecture.group_id
        ).all()

        # Map student.id -> participant.id (needed to update participation records)
        student_to_participant = {p.student_id: p.id for p in participants}
        group_student_ids = list(student_to_participant.keys())

        if not group_student_ids:
            raise HTTPException(status_code=400, detail="No students in this group")

        # Run face recognition
        recognition = recognize_faces(tmp_file, group_student_ids)

        if "error" in recognition:
            raise HTTPException(status_code=422, detail=recognition["error"])

        # Process results — update participation records
        results = []
        for face in recognition["results"]:
            if face["matched"]:
                student_db_id  = face["student_db_id"]
                participant_id = student_to_participant.get(student_db_id)

                if participant_id:
                    participation = db.query(Participation).filter(
                        Participation.lecture_id     == lecture_id,
                        Participation.participant_id == participant_id,
                    ).first()

                    if participation:
                        # Only upgrade absent -> present
                        # Never touch 'late' — that's instructor-only
                        if participation.status == ParticipationStatus.absent:
                            participation.status        = ParticipationStatus.present
                            participation.confidence    = face["confidence"]
                            participation.recognized_at = datetime.now(timezone.utc)
                            db.commit()

                results.append(AttendanceResult(
                    face_index   = face["face_index"],
                    student_id   = face.get("student_id"),
                    student_name = face.get("name"),
                    status       = ParticipationStatus.present if face["matched"] else None,
                    confidence   = face.get("confidence"),
                    matched      = face["matched"],
                ))
            else:
                results.append(AttendanceResult(
                    face_index   = face["face_index"],
                    student_id   = None,
                    student_name = None,
                    status       = None,
                    confidence   = face.get("confidence"),
                    matched      = False,
                ))

        return AttendanceScanResponse(
            lecture_id              = lecture_id,
            faces_detected          = recognition["faces_detected"],
            results                 = results,
            unmatched_faces         = sum(1 for r in results if not r.matched),
            processing_time_seconds = recognition["processing_time"],
        )

    finally:
        # Always clean up temp file
        if os.path.exists(tmp_file):
            os.remove(tmp_file)