import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import helpers, models, schemas
from ..config import DATA_DIR, MAX_AVATAR_BYTES
from ..database import get_db
from ..security import decode_token, get_current_user
from ..ws import manager
from .media import IMAGE_MIMES, get_optional_user, sanitize_filename

router = APIRouter(prefix="/api/users", tags=["users"])


def _delete_file_best_effort(path: str | None) -> None:
    """Remove a file if it exists; never raise (old avatar may be gone)."""
    if not path:
        return
    try:
        p = Path(path)
        if p.is_file():
            p.unlink()
    except OSError:
        pass


@router.get("/search")
def search_users(
    q: str = "",
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (q or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    users = (
        db.query(models.User)
        .filter(
            models.User.id != current_user.id,
            or_(
                models.User.display_name.ilike(like),
                models.User.username.ilike(like),
                models.User.email.ilike(like),
            ),
        )
        .limit(20)
        .all()
    )
    return [helpers.user_public_dict(u) for u in users]


@router.patch("/me")
def update_profile(
    payload: schemas.UpdateProfileRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change display name and/or username. Username must be unique."""
    if payload.display_name is not None:
        name = payload.display_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Display name must not be empty")
        current_user.display_name = name
    if payload.username is not None:
        username = payload.username.strip().lower()
        if not helpers.USERNAME_RE.match(username):
            raise HTTPException(
                status_code=400,
                detail="Username must be 3-20 characters, letters, numbers and underscore only",
            )
        taken = (
            db.query(models.User)
            .filter(
                models.User.username == username,
                models.User.id != current_user.id,
            )
            .first()
        )
        if taken is not None:
            raise HTTPException(status_code=400, detail="Username is already taken")
        current_user.username = username
    db.commit()
    db.refresh(current_user)
    return helpers.user_full_dict(current_user)


@router.post("/me/avatar", status_code=201)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a profile picture (image only, max 5 MB).

    Stored under DATA_DIR/uploads/avatars/ (the same uploads storage as
    chat media). Returns the updated user incl. avatar_url.
    """
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if not mime:
        guessed, _ = mimetypes.guess_type(file.filename or "")
        mime = (guessed or "").lower()
    if mime not in IMAGE_MIMES:
        raise HTTPException(
            status_code=400, detail="Profile picture must be a PNG, JPG, GIF or WebP image"
        )
    data = await file.read()
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="Profile picture too large (max 5 MB)")

    safe_name = sanitize_filename(file.filename or "avatar")
    unique_name = f"{current_user.id}_{uuid.uuid4().hex}_{safe_name}"
    dest_dir = DATA_DIR / "uploads" / "avatars"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / unique_name
    dest_path.write_bytes(data)

    old_path = current_user.avatar_path
    current_user.avatar_path = str(dest_path)
    db.commit()
    db.refresh(current_user)
    # Best effort: drop the previous picture now that the new one is saved.
    _delete_file_best_effort(old_path)
    return helpers.user_full_dict(current_user)


@router.delete("/me/avatar")
def remove_avatar(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove the profile picture (back to initials)."""
    old_path = current_user.avatar_path
    current_user.avatar_path = None
    db.commit()
    db.refresh(current_user)
    _delete_file_best_effort(old_path)
    return helpers.user_full_dict(current_user)


@router.get("/{user_id}/avatar")
async def get_avatar(
    user_id: int,
    token: str | None = None,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Serve a user's profile picture.

    Authenticated like the media download (Bearer header OR ?token=, so
    <img> tags can use it). Any logged-in user may view any avatar —
    avatars are meant to be seen in the chat list and on messages.
    """
    user = current_user
    if user is None:
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        user_id_from_token = decode_token(token)
        user = db.query(models.User).filter(models.User.id == user_id_from_token).first()
        if user is None:
            raise HTTPException(status_code=401, detail="Could not validate credentials")

    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target is None or not target.avatar_path:
        raise HTTPException(status_code=404, detail="No profile picture")
    if not Path(target.avatar_path).is_file():
        raise HTTPException(status_code=404, detail="Profile picture not found on disk")

    media_type, _ = mimetypes.guess_type(target.avatar_path)
    return FileResponse(
        path=target.avatar_path,
        media_type=media_type or "application/octet-stream",
    )


@router.get("/{user_id}/online")
def user_online(
    user_id: int,
    current_user: models.User = Depends(get_current_user),
):
    return {"online": manager.is_online(user_id)}


@router.get("/{user_id}")
def get_user(
    user_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return helpers.user_full_dict(user)
