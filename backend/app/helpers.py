"""Shared DB helpers used by both the REST routers and the WebSocket code.

Kept in one place so ws.py and routers/conversations.py never import each
other (which would be a circular import).
"""

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import models


def _now_naive() -> datetime:
    """Current UTC time as a naive datetime (SQLite stores naive)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def is_expired(msg) -> bool:
    """True when a disappearing message's time has run out."""
    if getattr(msg, "expires_at", None) is None:
        return False
    return _naive(msg.expires_at) <= _now_naive()


def expires_at_for(conv, created_at) -> datetime | None:
    """Expiry for a message sent now in this conversation.

    Timer on (>0) -> created_at + timer. Timer off (0) -> None (never).
    """
    seconds = getattr(conv, "disappearing_seconds", 0) or 0
    if seconds <= 0:
        return None
    base = _naive(created_at) if created_at is not None else _now_naive()
    return base + timedelta(seconds=seconds)

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")


def sanitize_username_base(text: str) -> str:
    """Turn free text (e.g. an email local-part) into a username base.

    Lowercase, keep only [a-z0-9_], fall back to 'user' when empty, and
    cap at 20 chars. The result may still be shorter than 3 chars or
    taken — generate_unique_username() fixes both.
    """
    base = re.sub(r"[^a-z0-9_]", "", (text or "").lower())
    if not base:
        base = "user"
    return base[:20]


def generate_unique_username(db: Session, base: str) -> str:
    """Return a free username derived from base: base, base1, base2, ...

    Pads short bases to 3 chars (base + 'user') and truncates the base so
    the numeric suffix still fits in 20 chars.
    """
    base = sanitize_username_base(base)
    if len(base) < 3:
        base = (base + "user")[:20]
    candidate = base
    suffix = 0
    while (
        db.query(models.User).filter(models.User.username == candidate).first()
        is not None
    ):
        suffix += 1
        suffix_str = str(suffix)
        candidate = base[: 20 - len(suffix_str)] + suffix_str
    return candidate


def avatar_url_for(user) -> str | None:
    """Public avatar URL for a user, or None when no picture is set.

    Served by GET /api/users/{id}/avatar (authenticated, Bearer or
    ?token=, so <img> tags can use it).
    """
    if user is None or not getattr(user, "avatar_path", None):
        return None
    return f"/api/users/{user.id}/avatar"


def user_public_dict(user) -> dict:
    """The user shape shown to other users (search, get user, other_user)."""
    if user is None:
        return None
    return {
        "id": user.id,
        "display_name": user.display_name,
        "username": user.username,
        "email": user.email,
        "avatar_url": avatar_url_for(user),
    }


def user_full_dict(user) -> dict:
    """The user shape for the owner (me / login / register)."""
    d = user_public_dict(user)
    d["role"] = user.role
    d["created_at"] = user.created_at.isoformat() if user.created_at else None
    return d


def is_participant(db: Session, conversation_id: int, user_id: int) -> bool:
    return (
        db.query(models.Participant)
        .filter(
            models.Participant.conversation_id == conversation_id,
            models.Participant.user_id == user_id,
        )
        .first()
        is not None
    )


def get_participant_ids(db: Session, conversation_id: int) -> list[int]:
    rows = (
        db.query(models.Participant)
        .filter(models.Participant.conversation_id == conversation_id)
        .all()
    )
    return [r.user_id for r in rows]


def get_other_user(db: Session, conversation_id: int, user_id: int):
    """The other participant of a 1:1 conversation (None if not found)."""
    row = (
        db.query(models.Participant)
        .filter(
            models.Participant.conversation_id == conversation_id,
            models.Participant.user_id != user_id,
        )
        .first()
    )
    if row is None:
        return None
    return db.query(models.User).filter(models.User.id == row.user_id).first()


def message_to_dict(db: Session, msg: models.Message, for_admin: bool = False) -> dict:
    read_rows = (
        db.query(models.MessageRead)
        .filter(models.MessageRead.message_id == msg.id)
        .all()
    )
    seen_by = [msg.sender_id] + [r.user_id for r in read_rows]
    # Deduplicate, keep sender first.
    seen_by = list(dict.fromkeys(seen_by))
    expired = is_expired(msg)
    # Participants never receive an expired message's content or media —
    # it is stripped here (and the media download is blocked too). The
    # admin sees the full message with expired: true.
    content = msg.content
    media_url = msg.media_url
    media_name = msg.media_name
    media_size = msg.media_size
    if expired and not for_admin:
        content = None
        media_url = None
        media_name = None
        media_size = None
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "sender_id": msg.sender_id,
        "kind": msg.kind,
        "content": content,
        "media_url": media_url,
        "media_name": media_name,
        "media_size": media_size,
        "expires_at": msg.expires_at.isoformat() if msg.expires_at else None,
        "expired": expired,
        # ISO strings (not datetime objects) so the dict is JSON-safe for
        # WebSocket send_json as well as plain JSONResponse.
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "seen_by": seen_by,
    }


def last_message_for(db: Session, conversation_id: int):
    return (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.id.desc())
        .first()
    )


def unread_count(db: Session, conversation_id: int, user_id: int) -> int:
    read_ids = {
        r.message_id
        for r in db.query(models.MessageRead)
        .filter(models.MessageRead.user_id == user_id)
        .all()
    }
    msgs = (
        db.query(models.Message)
        .filter(
            models.Message.conversation_id == conversation_id,
            models.Message.sender_id != user_id,
        )
        .all()
    )
    # Expired messages are gone from the chat, so they are not unread.
    return sum(1 for m in msgs if m.id not in read_ids and not is_expired(m))


def conversation_to_dict(db: Session, conv: models.Conversation, user_id: int) -> dict:
    is_group = bool(getattr(conv, "is_group", 0) or 0)
    # Groups: other_user is always None; participants lists every member.
    # 1:1: keep the existing shape (other_user set) and also include
    # participants = both users.
    other = None if is_group else get_other_user(db, conv.id, user_id)
    participant_rows = (
        db.query(models.Participant)
        .filter(models.Participant.conversation_id == conv.id)
        .order_by(models.Participant.user_id)
        .all()
    )
    participants = []
    for row in participant_rows:
        u = db.query(models.User).filter(models.User.id == row.user_id).first()
        if u is not None:
            participants.append(user_public_dict(u))
    last = last_message_for(db, conv.id)
    last_message = None
    last_message_at = None
    if last is not None:
        expired = is_expired(last)
        last_message = {
            "id": last.id,
            "sender_id": last.sender_id,
            "kind": last.kind,
            # Never leak an expired message's content in the list preview.
            "content": None if expired else last.content,
            "created_at": last.created_at.isoformat() if last.created_at else None,
            "expires_at": last.expires_at.isoformat() if last.expires_at else None,
            "expired": expired,
        }
        last_message_at = last.created_at.isoformat() if last.created_at else None
    return {
        "id": conv.id,
        "other_user": user_public_dict(other) if other else None,
        "is_group": is_group,
        "name": getattr(conv, "name", None) if is_group else None,
        "participants": participants,
        "disappearing_seconds": getattr(conv, "disappearing_seconds", 0) or 0,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "last_message": last_message,
        "last_message_at": last_message_at,
        "unread_count": unread_count(db, conv.id, user_id),
    }


def mark_as_read(db: Session, conversation_id: int, user_id: int) -> None:
    """Insert message_reads rows for all messages from the OTHER user
    in this conversation that the caller has not read yet."""
    msgs = (
        db.query(models.Message)
        .filter(
            models.Message.conversation_id == conversation_id,
            models.Message.sender_id != user_id,
        )
        .all()
    )
    existing = {
        r.message_id
        for r in db.query(models.MessageRead)
        .filter(models.MessageRead.user_id == user_id)
        .all()
    }
    for m in msgs:
        if m.id not in existing:
            db.add(models.MessageRead(message_id=m.id, user_id=user_id))
    db.commit()
