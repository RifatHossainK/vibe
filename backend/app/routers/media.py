import mimetypes
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import helpers, models
from ..config import DATA_DIR, MAX_MEDIA_BYTES
from ..database import get_db
from ..security import decode_token, get_current_user
from ..ws import broadcast_message

router = APIRouter(tags=["media"])

IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime"}
AUDIO_MIMES = {
    "audio/mpeg",
    "audio/wav",
    "audio/ogg",
    "audio/webm",
    "audio/mp4",
    "audio/x-m4a",
}

MIME_TO_KIND = {}
for _m in IMAGE_MIMES:
    MIME_TO_KIND[_m] = "image"
for _m in VIDEO_MIMES:
    MIME_TO_KIND[_m] = "video"
for _m in AUDIO_MIMES:
    MIME_TO_KIND[_m] = "audio"

ALLOWED_KINDS = {"image", "video", "audio", "voice"}


def sanitize_filename(name: str) -> str:
    base = os.path.basename(name or "file")
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    cleaned = cleaned.strip(".") or "file"
    return cleaned[:100]


def get_optional_user(request: Request, db: Session = Depends(get_db)):
    """Return the current user from a Bearer header, or None if absent/invalid.

    The download endpoint also accepts ?token=, handled in the route itself.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        user_id = decode_token(auth[len("Bearer "):].strip())
    except HTTPException:
        return None
    return db.query(models.User).filter(models.User.id == user_id).first()


@router.post("/api/conversations/{conversation_id}/upload", status_code=201)
async def upload_media(
    conversation_id: int,
    file: UploadFile = File(...),
    kind: str | None = Form(None),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = (
        db.query(models.Conversation)
        .filter(models.Conversation.id == conversation_id)
        .first()
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not helpers.is_participant(db, conversation_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant of this conversation")

    mime = (file.content_type or "").split(";")[0].strip().lower()
    if not mime:
        guessed, _ = mimetypes.guess_type(file.filename or "")
        mime = (guessed or "").lower()
    inferred = MIME_TO_KIND.get(mime)
    if inferred is None:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {mime or 'unknown'}")

    if kind is not None:
        kind = kind.strip().lower()
        if kind not in ALLOWED_KINDS:
            raise HTTPException(status_code=400, detail="kind must be image, video, audio or voice")
        # kind must match the MIME category (voice shares the audio category).
        expected = "audio" if kind in ("audio", "voice") else kind
        if inferred != expected:
            raise HTTPException(
                status_code=400,
                detail=f"File type {mime} does not match kind {kind}",
            )
    else:
        kind = inferred

    # One 1 GB cap for every kind (photo, video, audio, voice).
    cap = MAX_MEDIA_BYTES

    safe_name = sanitize_filename(file.filename or "file")
    unique_name = f"{uuid.uuid4().hex}_{safe_name}"
    dest_dir = DATA_DIR / "uploads" / str(conversation_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / unique_name
    # Stream the body to disk in 1 MB chunks — NEVER hold the whole
    # file in memory (the old `await file.read()` loaded it all, which
    # would exhaust the Render free instance's RAM on a 1 GB upload
    # and crash the service). Count bytes as they land; if the total
    # passes the cap, abort, delete the partial file, and 400.
    total = 0
    too_large = False
    try:
        with dest_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    too_large = True
                    break
                out.write(chunk)
    finally:
        await file.close()
    if too_large:
        dest_path.unlink(missing_ok=True)
        cap_label = "1 GB" if cap >= 1024 * 1024 * 1024 else f"{cap // (1024 * 1024)} MB"
        raise HTTPException(
            status_code=400,
            detail=f"File too large (max {cap_label})",
        )

    from ..models import _utcnow

    now = _utcnow()
    msg = models.Message(
        conversation_id=conversation_id,
        sender_id=current_user.id,
        kind=kind,
        content=None,
        media_name=safe_name,
        media_size=total,
        file_path=str(dest_path),
        expires_at=helpers.expires_at_for(conv, now),
        created_at=now,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    # media_url is defined by the API contract as /api/media/<message_id>.
    msg.media_url = f"/api/media/{msg.id}"
    db.commit()
    db.refresh(msg)

    msg_dict = helpers.message_to_dict(db, msg)
    participant_ids = helpers.get_participant_ids(db, conversation_id)
    await broadcast_message(msg_dict, participant_ids)
    return msg_dict


@router.get("/api/media/{message_id}")
async def download_media(
    message_id: int,
    token: str | None = None,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    # Auth via Bearer header OR ?token= query param.
    user = current_user
    if user is None:
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        user_id = decode_token(token)
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if user is None:
            raise HTTPException(status_code=401, detail="Could not validate credentials")

    msg = db.query(models.Message).filter(models.Message.id == message_id).first()
    if msg is None or not msg.media_url:
        raise HTTPException(status_code=404, detail="Media not found")
    # A deleted message's media is gone for participants: deleted for
    # everyone blocks all participants; deleted for me blocks that
    # user. Admins may still download it (Media Library / audit).
    if user.role != "admin":
        if helpers.is_deleted_for_everyone(msg):
            raise HTTPException(status_code=404, detail="Media not found")
        if helpers.is_deleted_for(msg, user.id):
            raise HTTPException(status_code=404, detail="Media not found")
    # An expired disappearing message's media is gone for participants.
    # Admins may still download it (Media Library / audit).
    if helpers.is_expired(msg) and user.role != "admin":
        raise HTTPException(status_code=404, detail="Media not found")
    if not helpers.is_participant(db, msg.conversation_id, user.id):
        # Admins may read any conversation through the read-only admin
        # endpoints, so they may download its media too; everyone else
        # must be a participant.
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="Not a participant of this conversation")
    if not msg.file_path or not Path(msg.file_path).is_file():
        raise HTTPException(status_code=404, detail="Media file not found on disk")

    media_type, _ = mimetypes.guess_type(msg.file_path)
    return FileResponse(
        path=msg.file_path,
        media_type=media_type or "application/octet-stream",
        filename=msg.media_name or "file",
    )
