"""
Face service — the core recognition engine.

Key design decisions:
  - Embeddings are loaded into RAM once on startup (embedding_cache)
  - Recognition is just cosine distance math — no disk reads during a scan
  - One-to-one matching prevents same student being matched to multiple faces
  - Embedding is stored as binary blob in DB (512 float32 = 2048 bytes per student)
"""

import io
import struct
import time
import numpy as np
from scipy.spatial.distance import cosine
from deepface import DeepFace
from sqlalchemy.orm import Session

from app.core.config import get_settings

settings = get_settings()

MODEL_NAME = "ArcFace"
DETECTOR   = "retinaface"
THRESHOLD  = 0.68   # ArcFace cosine distance threshold


# ── Embedding serialization ───────────────────────────────────────────────────

def embedding_to_bytes(embedding: list[float]) -> bytes:
    """Convert 512-float embedding to binary for DB storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def bytes_to_embedding(data: bytes) -> list[float]:
    """Convert binary blob back to float list."""
    count = len(data) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{count}f", data))


# ── In-memory embedding cache ─────────────────────────────────────────────────

class EmbeddingCache:
    """
    Holds all student embeddings in RAM.
    Loaded once on startup, updated when a student photo is added/changed.
    Structure: {student_db_id: {"name": str, "embedding": list[float]}}
    """

    def __init__(self):
        self.cache: dict = {}

    def load(self, db: Session = None):
        if db is None:
            from app.core.database import SessionLocal
            db = SessionLocal()
            close = True
        else:
            close = False

        try:
            from app.models import Student
            students = db.query(Student).filter(Student.embedding.isnot(None)).all()
            self.cache = {}
            for s in students:
                self.cache[s.id] = {
                    "student_id": s.student_id,
                    "name": f"{s.first_name} {s.last_name}",
                    "embedding": bytes_to_embedding(s.embedding),
                }
        except Exception as e:
            print(f"⚠️  Could not load embeddings (tables may not exist yet): {e}")
            self.cache = {}
        finally:
            if close:
                db.close()

    def add(self, student_id: int, university_id: int, name: str, embedding: list[float]):
        """Add or update a single student in the cache."""
        self.cache[student_id] = {
            "student_id": university_id,
            "name":       name,
            "embedding":  embedding,
        }

    def remove(self, student_id: int):
        self.cache.pop(student_id, None)


# Singleton — imported by main.py and routers
embedding_cache = EmbeddingCache()


# ── Generate embedding from image path ───────────────────────────────────────

def generate_embedding(image_path: str) -> list[float]:
    """
    Generate ArcFace embedding for a student photo.
    Called once when a student photo is uploaded.
    """
    obj = DeepFace.represent(
        img_path          = image_path,
        model_name        = MODEL_NAME,
        detector_backend  = DETECTOR,
        enforce_detection = True,
    )
    return obj[0]["embedding"]


# ── Recognize faces in classroom photo ───────────────────────────────────────

def recognize_faces(image_path: str, group_student_ids: list[int]) -> dict:
    """
    Full recognition pipeline for a classroom photo.

    Args:
        image_path:        path to the uploaded classroom photo
        group_student_ids: list of student DB IDs in this group
                           (we only match against students in this group)

    Returns:
        {
          faces_detected: int,
          results: [
            {face_index, matched, student_id, name, distance, confidence, facial_area}
          ],
          processing_time: float
        }
    """
    t_start = time.time()

    # Filter cache to only students in this group
    group_cache = {
        sid: data
        for sid, data in embedding_cache.cache.items()
        if sid in group_student_ids
    }

    if not group_cache:
        return {
            "faces_detected": 0,
            "results": [],
            "processing_time": 0,
            "error": "No embeddings found for students in this group",
        }

    # Step 1: Detect all faces
    try:
        faces = DeepFace.extract_faces(
            img_path          = image_path,
            detector_backend  = DETECTOR,
            enforce_detection = False,
            align             = True,
        )
    except Exception as e:
        return {"faces_detected": 0, "results": [], "processing_time": 0, "error": str(e)}

    # Step 2: Embed each valid face
    valid_faces     = []
    face_embeddings = []

    for i, face_obj in enumerate(faces):
        det_conf    = face_obj.get("confidence", 0)
        facial_area = face_obj.get("facial_area", {})
        face_img    = face_obj.get("face")

        if det_conf < 0.85:
            continue

        try:
            tmp    = f"/tmp/_face_{i}.jpg"
            import cv2
            pixels = (face_img * 255).astype(np.uint8) if face_img.max() <= 1.0 else face_img
            cv2.imwrite(tmp, pixels)

            emb = DeepFace.represent(
                img_path          = tmp,
                model_name        = MODEL_NAME,
                detector_backend  = "skip",
                enforce_detection = False,
            )
            import os
            os.remove(tmp)

            face_embeddings.append(emb[0]["embedding"])
            valid_faces.append({
                "face_index":  len(valid_faces) + 1,
                "facial_area": facial_area,
                "det_conf":    round(det_conf, 3),
            })
        except Exception:
            continue

    # Step 3: One-to-one matching
    results = _one_to_one_match(face_embeddings, valid_faces, group_cache)

    return {
        "faces_detected":  len(faces),
        "results":         results,
        "processing_time": round(time.time() - t_start, 2),
    }


def _one_to_one_match(face_embeddings, valid_faces, group_cache) -> list:
    """
    Greedy one-to-one matching:
    Build full distance matrix, sort all pairs by distance,
    assign best pair first, remove both from pool.
    """
    if not face_embeddings or not group_cache:
        return []

    student_ids = list(group_cache.keys())
    student_data = [group_cache[sid] for sid in student_ids]

    # Build distance matrix
    distances = []
    for face_emb in face_embeddings:
        row = [cosine(face_emb, s["embedding"]) for s in student_data]
        distances.append(row)

    # Collect and sort all pairs
    all_pairs = [
        (distances[fi][si], fi, si)
        for fi in range(len(face_embeddings))
        for si in range(len(student_data))
    ]
    all_pairs.sort(key=lambda x: x[0])

    claimed_faces    = set()
    claimed_students = set()
    results          = [None] * len(face_embeddings)

    for dist, fi, si in all_pairs:
        if fi in claimed_faces or si in claimed_students:
            continue
        if dist <= THRESHOLD:
            results[fi] = {
                **valid_faces[fi],
                "matched":    True,
                "student_db_id": student_ids[si],
                "student_id": student_data[si]["student_id"],
                "name":       student_data[si]["name"],
                "distance":   round(dist, 4),
                "confidence": round((1 - dist) * 100, 1),
            }
            claimed_faces.add(fi)
            claimed_students.add(si)

    # Fill unmatched faces
    for fi in range(len(face_embeddings)):
        if results[fi] is None:
            best_dist = min(distances[fi])
            best_si   = distances[fi].index(best_dist)
            results[fi] = {
                **valid_faces[fi],
                "matched":    False,
                "student_db_id": None,
                "student_id": None,
                "name":       "Unknown",
                "distance":   round(best_dist, 4),
                "confidence": round((1 - best_dist) * 100, 1),
                "closest":    student_data[best_si]["name"],
            }

    return results
