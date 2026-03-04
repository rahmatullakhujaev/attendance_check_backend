from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    verify_password, hash_password,
    create_access_token, create_refresh_token, decode_token,
)
from app.models import Instructor
from app.schemas import LoginRequest, TokenResponse, RefreshRequest, InstructorCreate, InstructorResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=InstructorResponse, status_code=201)
def register(payload: InstructorCreate, db: Session = Depends(get_db)):
    """Register a new instructor account."""
    existing = db.query(Instructor).filter(Instructor.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    instructor = Instructor(
        first_name = payload.first_name,
        last_name  = payload.last_name,
        university = payload.university,
        field      = payload.field,
        email      = payload.email,
        password   = hash_password(payload.password),
    )
    db.add(instructor)
    db.commit()
    db.refresh(instructor)
    return instructor


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Login with email + password, returns access + refresh tokens."""
    instructor = db.query(Instructor).filter(Instructor.email == payload.email).first()

    if not instructor or not verify_password(payload.password, instructor.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token_data = {"sub": str(instructor.id)}
    return TokenResponse(
        access_token  = create_access_token(token_data),
        refresh_token = create_refresh_token(token_data),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest):
    """Exchange a refresh token for a new access token."""
    data = decode_token(payload.refresh_token)

    if not data or data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    token_data = {"sub": data["sub"]}
    return TokenResponse(
        access_token  = create_access_token(token_data),
        refresh_token = create_refresh_token(token_data),
    )
