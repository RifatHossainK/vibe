"""Admin dashboard API (read-only).

Every endpoint requires the caller to be an admin (first registered user
has role="admin"). Admins can inspect users, conversations, activity and
all media across the site, and bulk-download media as a ZIP. No endpoint
here sends, edits or deletes anything.
"""

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import helpers, models
from ..database import get_db
from ..security import get_current_user
from ..ws import manager
from .media import sanitize_filename

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Public labels for media kinds, same as the frontend ChatList.
MEDIA_LABELS = {
    "image": "📷 Photo",
    "video": "🎬 Video",
    "voice": "🎙️ Voice message",
    "audio": "🔊 Audio",
}

# Admin media-library kind names -> stored message kinds.
KIND_MAP = {"photo": "image", "video": "video", "audio": "audio"}

MAX_EXPORT_BYTES = 200 * 1024 * 1024  # 200 MB total cap for the ZIP export
MAX_EXPORT_FILES = 100


def get_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def _naive(dt):
    """Strip tzinfo so aware/naive datetimes from SQLite compare safely."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _media_kind_filter(kind: str):
    """Translate an admin ?kind= filter into stored message kinds.

    Returns a set of kinds to include, or None for 'all' (any non-text).
    """
    if kind == "all":
        return None
    if kind == "audio":
        return {"audio", "voice"}
    if kind in KIND_MAP:
        return {KIND_MAP[kind]}
    raise HTTPException(status_code=400, detail="kind must be all, photo, video or audio")


def _admin_kind(msg_kind: str) -> str:
    """Map a stored message kind to the admin media-library kind label."""
    if msg_kind == "image":
        return "photo"
    if msg_kind == "video":
        return "video"
    return "audio"  # audio | voice


def _media_query(db: Session, kind: str):
    kinds = _media_kind_filter(kind)
    # Call-history lines are not media.
    q = db.query(models.Message).filter(
        models.Message.kind != "text", models.Message.kind != "call"
    )
    if kinds is not None:
        q = q.filter(models.Message.kind.in_(kinds))
    return q.order_by(models.Message.id.desc())


@router.get("/stats")
def stats(db: Session = Depends(get_db), admin: models.User = Depends(get_admin)):
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_start = _naive(today_start)
    messages_today = 0
    for (created_at,) in db.query(models.Message.created_at).all():
        if _naive(created_at) is not None and _naive(created_at) >= today_start:
            messages_today += 1
    return {
        "total_users": db.query(models.User).count(),
        "total_messages": db.query(models.Message).count(),
        "total_media": db.query(models.Message)
        .filter(models.Message.kind != "text", models.Message.kind != "call")
        .count(),
        "messages_today": messages_today,
        "online_now": len(manager.active),
    }


@router.get("/users")
def list_users(db: Session = Depends(get_db), admin: models.User = Depends(get_admin)):
    users = db.query(models.User).order_by(models.User.id).all()
    out = []
    for u in users:
        message_count = (
            db.query(models.Message).filter(models.Message.sender_id == u.id).count()
        )
        media_count = (
            db.query(models.Message)
            .filter(models.Message.sender_id == u.id, models.Message.kind != "text")
            .count()
        )
        last_sent = (
            db.query(models.Message)
            .filter(models.Message.sender_id == u.id)
            .order_by(models.Message.id.desc())
            .first()
        )
        last_read = (
            db.query(models.MessageRead)
            .filter(models.MessageRead.user_id == u.id)
            .order_by(models.MessageRead.id.desc())
            .first()
        )
        candidates = [c for c in (last_sent.created_at if last_sent else None,
                                  last_read.read_at if last_read else None)
                      if c is not None]
        last_seen = max((_naive(c) for c in candidates), default=None)
        if last_seen is None:
            last_seen = _naive(u.created_at)
        out.append(
            {
                "id": u.id,
                "display_name": u.display_name,
                "username": u.username,
                "email": u.email,
                "role": u.role,
                "avatar_url": helpers.avatar_url_for(u),
                "is_admin": u.role == "admin",
                "online": manager.is_online(u.id),
                "last_seen": _iso(last_seen),
                "created_at": _iso(u.created_at),
                "message_count": message_count,
                "media_count": media_count,
            }
        )
    return out


@router.get("/activity")
def activity(
    limit: int = 100,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_admin),
):
    limit = max(1, min(int(limit), 100))
    events = []
    users_by_id = {u.id: u for u in db.query(models.User).all()}
    for u in users_by_id.values():
        events.append(
            {
                "type": "register",
                "user_name": u.display_name,
                "user_email": u.email,
                "detail": "registered",
                "created_at": _iso(u.created_at),
                "_sort": _naive(u.created_at),
                "_id": u.id,
            }
        )
    for m in db.query(models.Message).all():
        sender = users_by_id.get(m.sender_id)
        if m.kind == "text":
            other = helpers.get_other_user(db, m.conversation_id, m.sender_id)
            if other is not None:
                detail = f"sent a message to {other.display_name}"
            else:
                detail = "sent a message"
            events.append(
                {
                    "type": "message",
                    "user_name": sender.display_name if sender else "Unknown",
                    "user_email": sender.email if sender else "",
                    "detail": detail,
                    "created_at": _iso(m.created_at),
                    "_sort": _naive(m.created_at),
                    "_id": m.id,
                }
            )
        else:
            detail = {
                "image": "sent a photo",
                "video": "sent a video",
                "voice": "sent a voice message",
                "audio": "sent an audio file",
            }.get(m.kind, "sent media")
            events.append(
                {
                    "type": "media",
                    "user_name": sender.display_name if sender else "Unknown",
                    "user_email": sender.email if sender else "",
                    "detail": detail,
                    "created_at": _iso(m.created_at),
                    "_sort": _naive(m.created_at),
                    "_id": m.id,
                }
            )
    events.sort(key=lambda e: (e["_sort"] or datetime.min, e["_id"]), reverse=True)
    for e in events:
        e.pop("_sort", None)
        e.pop("_id", None)
    return events[:limit]


@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db), admin: models.User = Depends(get_admin)
):
    convs = db.query(models.Conversation).all()
    users_by_id = {u.id: u for u in db.query(models.User).all()}
    with_last = []
    without_last = []
    for conv in convs:
        pids = helpers.get_participant_ids(db, conv.id)
        participants = [
            {
                "id": u.id,
                "display_name": u.display_name,
                "username": u.username,
                "email": u.email,
                "avatar_url": helpers.avatar_url_for(u),
            }
            for pid in pids
            if (u := users_by_id.get(pid)) is not None
        ]
        last = helpers.last_message_for(db, conv.id)
        count = (
            db.query(models.Message)
            .filter(models.Message.conversation_id == conv.id)
            .count()
        )
        entry = {
            "id": conv.id,
            "is_group": bool(getattr(conv, "is_group", 0) or 0),
            "name": getattr(conv, "name", None) if getattr(conv, "is_group", 0) else None,
            "participants": participants,
            "last_message_text": (
                last.content
                if last is not None and last.kind in ("text", "call")
                else (MEDIA_LABELS.get(last.kind) if last is not None else None)
            ),
            "last_message_kind": last.kind if last is not None else None,
            "last_message_at": _iso(last.created_at) if last is not None else None,
            "message_count": count,
        }
        if last is not None:
            with_last.append(((_naive(last.created_at) or datetime.min, last.id), entry))
        else:
            without_last.append(((_naive(conv.created_at) or datetime.min, conv.id), entry))
    with_last.sort(key=lambda t: t[0], reverse=True)
    without_last.sort(key=lambda t: t[0], reverse=True)
    return [e for _, e in with_last] + [e for _, e in without_last]


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(
    conversation_id: int,
    limit: int = 100,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_admin),
):
    limit = max(1, min(int(limit), 100))
    conv = (
        db.query(models.Conversation)
        .filter(models.Conversation.id == conversation_id)
        .first()
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.id)
        .limit(limit)
        .all()
    )
    users_by_id = {u.id: u for u in db.query(models.User).all()}
    out = []
    for m in msgs:
        # Admins see the FULL message even after it disappeared from the
        # participants' chat, with expired: true so the UI can label it.
        d = helpers.message_to_dict(db, m, for_admin=True)
        sender = users_by_id.get(m.sender_id)
        d["sender_name"] = sender.display_name if sender else "Unknown"
        # Who deleted it for everyone (sender or a moderating admin),
        # resolved to a name so the UI can label it "deleted by admin"
        # / "deleted by <name>". None when never deleted for everyone.
        deleted_by_id = getattr(m, "deleted_by", None)
        deleter = users_by_id.get(deleted_by_id) if deleted_by_id is not None else None
        d["deleted_by_name"] = deleter.display_name if deleter else None
        out.append(d)
    return out


@router.get("/media")
def list_media(
    kind: str = "all",
    limit: int = 100,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_admin),
):
    limit = max(1, min(int(limit), 100))
    msgs = _media_query(db, kind).limit(limit).all()
    users_by_id = {u.id: u for u in db.query(models.User).all()}
    out = []
    for m in msgs:
        sender = users_by_id.get(m.sender_id)
        pids = helpers.get_participant_ids(db, m.conversation_id)
        out.append(
            {
                "id": m.id,
                "kind": _admin_kind(m.kind),
                "conversation_id": m.conversation_id,
                "filename": m.media_name,
                "size_bytes": m.media_size,
                "sender_name": sender.display_name if sender else "Unknown",
                "sender_email": sender.email if sender else "",
                "conversation_participants": [
                    users_by_id[pid].display_name
                    for pid in pids
                    if pid in users_by_id
                ],
                "created_at": _iso(m.created_at),
                "download_url": f"/api/media/{m.id}",
                # Expired media still shows in the Library (labelled), the
                # admin is never blocked from seeing it.
                "expired": helpers.is_expired(m),
                # Deleted-for-everyone media still shows (labelled);
                # delete-for-me does NOT hide anything from the admin.
                "deleted": helpers.is_deleted_for_everyone(m),
            }
        )
    return out


@router.get("/media/export")
def export_media(
    kind: str = "all",
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_admin),
):
    msgs = _media_query(db, kind).limit(MAX_EXPORT_FILES).all()
    users_by_id = {u.id: u for u in db.query(models.User).all()}
    buf = io.BytesIO()
    total = 0
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for m in msgs:
            if not m.file_path or not Path(m.file_path).is_file():
                continue
            size = m.media_size or Path(m.file_path).stat().st_size
            if total + size > MAX_EXPORT_BYTES:
                continue
            sender = users_by_id.get(m.sender_id)
            sender_name = sanitize_filename(
                sender.display_name if sender else "unknown"
            )
            entry = f"{sender_name}_{m.id}_{sanitize_filename(m.media_name or 'file')}"
            zf.write(m.file_path, arcname=entry)
            total += size
            added += 1
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="vibe-media.zip"'},
    )
