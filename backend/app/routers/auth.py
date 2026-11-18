from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import helpers, models, schemas
from ..database import get_db
from ..security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", status_code=201)
def register(payload: schemas.RegisterRequest, db: Session = Depends(get_db)):
    existing = (
        db.query(models.User).filter(models.User.email == payload.email).first()
    )
    if existing is not None:
        raise HTTPException(status_code=400, detail="Email already registered")
    # Username: use the requested one (validated + unique) or generate
    # one from the email local-part.
    requested = (payload.username or "").strip().lower()
    if requested:
        if not helpers.USERNAME_RE.match(requested):
            raise HTTPException(
                status_code=400,
                detail="Username must be 3-20 characters, letters, numbers and underscore only",
            )
        taken = (
            db.query(models.User).filter(models.User.username == requested).first()
        )
        if taken is not None:
            raise HTTPException(status_code=400, detail="Username is already taken")
        username = requested
    else:
        username = helpers.generate_unique_username(
            db, payload.email.split("@")[0]
        )
    is_first = db.query(models.User).count() == 0
    user = models.User(
        display_name=payload.display_name.strip(),
        email=payload.email,
        username=username,
        password_hash=hash_password(payload.password),
        role="admin" if is_first else "user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id)
    out = helpers.user_full_dict(user)
    out["access_token"] = token
    out["token_type"] = "bearer"
    return out


@router.post("/login")
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_access_token(user.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": helpers.user_full_dict(user),
    }


@router.get("/me")
def me(current_user: models.User = Depends(get_current_user)):
    return helpers.user_full_dict(current_user)
