import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import helpers, models
from .database import SessionLocal
from .security import get_current_user_ws

router = APIRouter()


class ConnectionManager:
    def __init__(self):
        # user_id -> set of websockets
        self.active: dict[int, set[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        self.active.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket):
        sockets = self.active.get(user_id)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                del self.active[user_id]

    def is_online(self, user_id: int) -> bool:
        return user_id in self.active and len(self.active[user_id]) > 0

    async def send_to_user(self, user_id: int, payload: dict):
        for ws in list(self.active.get(user_id, set())):
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    async def send_to_users(self, user_ids: list[int], payload: dict):
        for uid in user_ids:
            await self.send_to_user(uid, payload)

    async def broadcast_all(self, payload: dict):
        for uid in list(self.active.keys()):
            await self.send_to_user(uid, payload)


manager = ConnectionManager()

# ---- Call signaling registry (in-memory only) ----
# conversation_id -> {caller_id, callee_id, call_type, state, started_at}
# state: "ringing" | "active". started_at is set when the call is accepted.
# 1-to-1 calls only; group invites are rejected. A server restart drops
# in-flight calls (no history is stored for a call that never ended).
active_calls: dict[int, dict] = {}


async def broadcast_message(message_dict: dict, participant_ids: list[int]):
    await manager.send_to_users(
        participant_ids, {"type": "message", "message": message_dict}
    )


async def broadcast_read(conversation_id: int, reader_id: int, participant_ids: list[int]):
    others = [uid for uid in participant_ids if uid != reader_id]
    await manager.send_to_users(
        others, {"type": "read", "conversation_id": conversation_id, "user_id": reader_id}
    )


def _format_duration(total_seconds: int) -> str:
    """Seconds -> 'M:SS' (e.g. 154 -> '2:34', 45 -> '0:45')."""
    total_seconds = max(0, int(total_seconds))
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def _missed_content(call_type: str) -> str:
    return "📹 Missed video call" if call_type == "video" else "📞 Missed audio call"


def _ended_content(call_type: str, duration_seconds: int) -> str:
    if call_type == "video":
        return f"📹 Video call · {_format_duration(duration_seconds)}"
    return f"📞 Audio call · {_format_duration(duration_seconds)}"


async def _store_call_history(conversation_id: int, sender_id: int, content: str) -> dict:
    """Persist a kind='call' history message (never expires — call
    history is exempt from the disappearing timer) and broadcast it as
    a normal {"type": "message"} so it appears in both users' chats."""
    db = SessionLocal()
    try:
        msg = models.Message(
            conversation_id=conversation_id,
            sender_id=sender_id,
            kind="call",
            content=content,
            expires_at=None,
            created_at=models._utcnow(),
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
        msg_dict = helpers.message_to_dict(db, msg)
        participant_ids = helpers.get_participant_ids(db, conversation_id)
    finally:
        db.close()
    await broadcast_message(msg_dict, participant_ids)
    return msg_dict


async def _validate_call_conversation(websocket, db, conversation_id: int, user_id: int):
    """Shared checks for every call:* message.

    Returns (conv, participant_ids), or None after sending an error:
    conversation must exist, sender must be a participant, and group
    conversations reject all calls ("Group calls are not supported in
    Phase 2").
    """
    conv = (
        db.query(models.Conversation)
        .filter(models.Conversation.id == conversation_id)
        .first()
    )
    if conv is None:
        await websocket.send_json({"type": "error", "detail": "Conversation not found"})
        return None
    participant_ids = helpers.get_participant_ids(db, conversation_id)
    if user_id not in participant_ids:
        await websocket.send_json(
            {"type": "error", "detail": "Not a participant of this conversation"}
        )
        return None
    if getattr(conv, "is_group", 0):
        await websocket.send_json(
            {"type": "error", "detail": "Group calls are not supported in Phase 2"}
        )
        return None
    return conv, participant_ids


@router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = None):
    # Authenticate by the ?token= query param; reject with 4401 if bad.
    # (Must accept first: closing an unaccepted socket yields an HTTP 403
    # instead of a 4401 close frame.)
    try:
        user_id = get_current_user_ws(token)
    except Exception:
        await websocket.accept()
        await websocket.close(code=4401)
        return

    await manager.connect(user_id, websocket)
    await manager.broadcast_all(
        {"type": "presence", "user_id": user_id, "online": True}
    )
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                await websocket.send_json(
                    {"type": "error", "detail": "Invalid JSON"}
                )
                continue
            msg_type = data.get("type")

            if msg_type == "message":
                conversation_id = data.get("conversation_id")
                content = data.get("content")
                if not isinstance(conversation_id, int) or not isinstance(content, str):
                    await websocket.send_json(
                        {"type": "error", "detail": "conversation_id and content are required"}
                    )
                    continue
                if not (1 <= len(content) <= 5000) or not content.strip():
                    await websocket.send_json(
                        {"type": "error", "detail": "content must be 1-5000 characters"}
                    )
                    continue
                db = SessionLocal()
                try:
                    if not helpers.is_participant(db, conversation_id, user_id):
                        await websocket.send_json(
                            {"type": "error", "detail": "Not a participant of this conversation"}
                        )
                        continue
                    from .models import _utcnow

                    conv = db.query(models.Conversation).filter(
                        models.Conversation.id == conversation_id
                    ).first()
                    now = _utcnow()
                    msg = models.Message(
                        conversation_id=conversation_id,
                        sender_id=user_id,
                        kind="text",
                        content=content,
                        expires_at=helpers.expires_at_for(conv, now) if conv else None,
                        created_at=now,
                    )
                    db.add(msg)
                    db.commit()
                    db.refresh(msg)
                    msg_dict = helpers.message_to_dict(db, msg)
                    participant_ids = helpers.get_participant_ids(db, conversation_id)
                finally:
                    db.close()
                await broadcast_message(msg_dict, participant_ids)

            elif msg_type == "typing":
                conversation_id = data.get("conversation_id")
                typing = bool(data.get("typing", False))
                if not isinstance(conversation_id, int):
                    await websocket.send_json(
                        {"type": "error", "detail": "conversation_id is required"}
                    )
                    continue
                db = SessionLocal()
                try:
                    participant_ids = helpers.get_participant_ids(db, conversation_id)
                    if user_id not in participant_ids:
                        await websocket.send_json(
                            {"type": "error", "detail": "Not a participant of this conversation"}
                        )
                        continue
                finally:
                    db.close()
                others = [uid for uid in participant_ids if uid != user_id]
                await manager.send_to_users(
                    others,
                    {
                        "type": "typing",
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                        "typing": typing,
                    },
                )

            elif msg_type == "read":
                conversation_id = data.get("conversation_id")
                if not isinstance(conversation_id, int):
                    await websocket.send_json(
                        {"type": "error", "detail": "conversation_id is required"}
                    )
                    continue
                db = SessionLocal()
                try:
                    if not helpers.is_participant(db, conversation_id, user_id):
                        await websocket.send_json(
                            {"type": "error", "detail": "Not a participant of this conversation"}
                        )
                        continue
                    helpers.mark_as_read(db, conversation_id, user_id)
                    participant_ids = helpers.get_participant_ids(db, conversation_id)
                finally:
                    db.close()
                await broadcast_read(conversation_id, user_id, participant_ids)

            elif msg_type == "call:invite":
                conversation_id = data.get("conversation_id")
                call_type = data.get("call_type")
                if not isinstance(conversation_id, int) or call_type not in ("audio", "video"):
                    await websocket.send_json(
                        {"type": "error", "detail": "conversation_id and call_type (audio|video) are required"}
                    )
                    continue
                db = SessionLocal()
                try:
                    validated = await _validate_call_conversation(
                        websocket, db, conversation_id, user_id
                    )
                    if validated is None:
                        continue
                    _, participant_ids = validated
                    others = [uid for uid in participant_ids if uid != user_id]
                    other_id = others[0] if others else None
                    me = db.query(models.User).filter(models.User.id == user_id).first()
                    from_name = me.display_name if me else ""
                    from_avatar_url = helpers.avatar_url_for(me)
                finally:
                    db.close()
                if conversation_id in active_calls:
                    await websocket.send_json(
                        {"type": "call:busy", "conversation_id": conversation_id}
                    )
                    continue
                if other_id is None or not manager.is_online(other_id):
                    await websocket.send_json(
                        {"type": "call:unavailable", "conversation_id": conversation_id}
                    )
                    await _store_call_history(
                        conversation_id, user_id, _missed_content(call_type)
                    )
                    continue
                active_calls[conversation_id] = {
                    "caller_id": user_id,
                    "callee_id": other_id,
                    "call_type": call_type,
                    "state": "ringing",
                    "started_at": None,
                }
                await manager.send_to_user(
                    other_id,
                    {
                        "type": "call:invite",
                        "conversation_id": conversation_id,
                        "call_type": call_type,
                        "from_user_id": user_id,
                        "from_name": from_name,
                        "from_avatar_url": from_avatar_url,
                    },
                )

            elif msg_type == "call:accept":
                conversation_id = data.get("conversation_id")
                if not isinstance(conversation_id, int):
                    await websocket.send_json(
                        {"type": "error", "detail": "conversation_id is required"}
                    )
                    continue
                call = active_calls.get(conversation_id)
                if (
                    call is None
                    or call["state"] != "ringing"
                    or call["callee_id"] != user_id
                ):
                    await websocket.send_json(
                        {"type": "error", "detail": "No ringing call to accept"}
                    )
                    continue
                call["state"] = "active"
                call["started_at"] = datetime.now(timezone.utc)
                await manager.send_to_user(
                    call["caller_id"],
                    {
                        "type": "call:accept",
                        "conversation_id": conversation_id,
                        "from_user_id": user_id,
                    },
                )

            elif msg_type == "call:reject":
                conversation_id = data.get("conversation_id")
                if not isinstance(conversation_id, int):
                    await websocket.send_json(
                        {"type": "error", "detail": "conversation_id is required"}
                    )
                    continue
                call = active_calls.get(conversation_id)
                if (
                    call is None
                    or call["state"] != "ringing"
                    or call["callee_id"] != user_id
                ):
                    await websocket.send_json(
                        {"type": "error", "detail": "No ringing call to reject"}
                    )
                    continue
                caller_id = call["caller_id"]
                call_type = call["call_type"]
                del active_calls[conversation_id]
                await manager.send_to_user(
                    caller_id,
                    {
                        "type": "call:reject",
                        "conversation_id": conversation_id,
                        "from_user_id": user_id,
                    },
                )
                await _store_call_history(
                    conversation_id, caller_id, _missed_content(call_type)
                )

            elif msg_type == "call:end":
                conversation_id = data.get("conversation_id")
                if not isinstance(conversation_id, int):
                    await websocket.send_json(
                        {"type": "error", "detail": "conversation_id is required"}
                    )
                    continue
                try:
                    duration_seconds = int(data.get("duration_seconds", 0) or 0)
                except (TypeError, ValueError):
                    duration_seconds = 0
                duration_seconds = max(0, duration_seconds)
                db = SessionLocal()
                try:
                    validated = await _validate_call_conversation(
                        websocket, db, conversation_id, user_id
                    )
                    if validated is None:
                        continue
                    _, participant_ids = validated
                finally:
                    db.close()
                # Only the FIRST call:end for a call stores history: pop
                # the registry entry, so a second end only relays.
                call = active_calls.pop(conversation_id, None)
                others = [uid for uid in participant_ids if uid != user_id]
                await manager.send_to_users(
                    others,
                    {
                        "type": "call:end",
                        "conversation_id": conversation_id,
                        "from_user_id": user_id,
                        "duration_seconds": duration_seconds,
                        "reason": "ended",
                    },
                )
                if call is not None:
                    if call["state"] == "active":
                        await _store_call_history(
                            conversation_id,
                            user_id,
                            _ended_content(call["call_type"], duration_seconds),
                        )
                    elif call["state"] == "ringing":
                        await _store_call_history(
                            conversation_id,
                            call["caller_id"],
                            _missed_content(call["call_type"]),
                        )

            elif msg_type in ("call:offer", "call:answer", "call:ice"):
                conversation_id = data.get("conversation_id")
                field = "sdp" if msg_type in ("call:offer", "call:answer") else "candidate"
                if not isinstance(conversation_id, int) or data.get(field) is None:
                    await websocket.send_json(
                        {"type": "error", "detail": f"conversation_id and {field} are required"}
                    )
                    continue
                call = active_calls.get(conversation_id)
                if call is None or user_id not in (call["caller_id"], call["callee_id"]):
                    await websocket.send_json(
                        {"type": "error", "detail": "No active call for this conversation"}
                    )
                    continue
                other_id = (
                    call["callee_id"] if user_id == call["caller_id"] else call["caller_id"]
                )
                await manager.send_to_user(
                    other_id,
                    {
                        "type": msg_type,
                        "conversation_id": conversation_id,
                        "from_user_id": user_id,
                        field: data.get(field),
                    },
                )

            elif msg_type == "call:screen":
                conversation_id = data.get("conversation_id")
                sharing = data.get("sharing")
                if not isinstance(conversation_id, int) or not isinstance(sharing, bool):
                    await websocket.send_json(
                        {"type": "error", "detail": "conversation_id and sharing (bool) are required"}
                    )
                    continue
                call = active_calls.get(conversation_id)
                if (
                    call is None
                    or call["state"] != "active"
                    or user_id not in (call["caller_id"], call["callee_id"])
                ):
                    await websocket.send_json(
                        {"type": "error", "detail": "No active call for this conversation"}
                    )
                    continue
                other_id = (
                    call["callee_id"] if user_id == call["caller_id"] else call["caller_id"]
                )
                await manager.send_to_user(
                    other_id,
                    {
                        "type": "call:screen",
                        "conversation_id": conversation_id,
                        "from_user_id": user_id,
                        "sharing": sharing,
                    },
                )

            else:
                await websocket.send_json(
                    {"type": "error", "detail": f"Unknown message type: {msg_type}"}
                )
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id, websocket)
        # If this user was on a ringing/active call, end it for the peer.
        # A ringing call that dies on disconnect stores NO history; an
        # active call stores an ended-call line with the elapsed time.
        for conversation_id, call in list(active_calls.items()):
            if user_id not in (call["caller_id"], call["callee_id"]):
                continue
            active_calls.pop(conversation_id, None)
            other_id = (
                call["callee_id"] if user_id == call["caller_id"] else call["caller_id"]
            )
            await manager.send_to_user(
                other_id,
                {
                    "type": "call:end",
                    "conversation_id": conversation_id,
                    "from_user_id": user_id,
                    "duration_seconds": 0,
                    "reason": "peer-disconnected",
                },
            )
            if call["state"] == "active" and call.get("started_at") is not None:
                elapsed = int(
                    (datetime.now(timezone.utc) - call["started_at"]).total_seconds()
                )
                await _store_call_history(
                    conversation_id,
                    user_id,
                    _ended_content(call["call_type"], max(0, elapsed)),
                )
        if not manager.is_online(user_id):
            await manager.broadcast_all(
                {"type": "presence", "user_id": user_id, "online": False}
            )
