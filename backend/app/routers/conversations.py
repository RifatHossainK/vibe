from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import helpers, models, schemas
from ..database import get_db
from ..security import get_current_user
from ..ws import broadcast_message, broadcast_read

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _require_participant(db: Session, conversation_id: int, user_id: int):
    conv = (
        db.query(models.Conversation)
        .filter(models.Conversation.id == conversation_id)
        .first()
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not helpers.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not a participant of this conversation")
    return conv


@router.post("")
async def create_conversation(
    payload: schemas.CreateConversationRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot create a conversation with yourself")
    other = db.query(models.User).filter(models.User.id == payload.user_id).first()
    if other is None:
        raise HTTPException(status_code=400, detail="User not found")

    # Find an existing 1:1 conversation between the two users.
    my_convs = (
        db.query(models.Participant.conversation_id)
        .filter(models.Participant.user_id == current_user.id)
        .all()
    )
    my_conv_ids = {row[0] for row in my_convs}
    existing = None
    if my_conv_ids:
        other_part = (
            db.query(models.Participant)
            .filter(
                models.Participant.user_id == payload.user_id,
                models.Participant.conversation_id.in_(my_conv_ids),
            )
            .first()
        )
        if other_part is not None:
            # Ensure it is exactly a 2-person conversation.
            count = (
                db.query(models.Participant)
                .filter(models.Participant.conversation_id == other_part.conversation_id)
                .count()
            )
            if count == 2:
                existing = (
                    db.query(models.Conversation)
                    .filter(models.Conversation.id == other_part.conversation_id)
                    .first()
                )
    if existing is not None:
        return helpers.conversation_to_dict(db, existing, current_user.id)

    conv = models.Conversation()
    db.add(conv)
    db.flush()
    db.add(models.Participant(conversation_id=conv.id, user_id=current_user.id))
    db.add(models.Participant(conversation_id=conv.id, user_id=payload.user_id))
    db.commit()
    db.refresh(conv)
    # 201 for a newly created conversation.
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=201,
        content=jsonable_encoder(helpers.conversation_to_dict(db, conv, current_user.id)),
    )


@router.post("/groups", status_code=201)
async def create_group(
    payload: schemas.CreateGroupRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a group conversation. Creator is auto-added as a member."""
    name = (payload.name or "").strip()
    if not (1 <= len(name) <= 100):
        raise HTTPException(
            status_code=400, detail="Group name must be 1-100 characters"
        )
    # Deduplicate, exclude self (auto-added as creator).
    other_ids = list(dict.fromkeys(uid for uid in payload.user_ids if uid != current_user.id))
    if not (1 <= len(other_ids) <= 49):
        raise HTTPException(
            status_code=400, detail="user_ids must contain 1-49 other users"
        )
    existing = (
        db.query(models.User).filter(models.User.id.in_(other_ids)).all()
    )
    if len(existing) != len(other_ids):
        raise HTTPException(status_code=400, detail="One or more users not found")

    conv = models.Conversation(is_group=1, name=name)
    db.add(conv)
    db.flush()
    db.add(models.Participant(conversation_id=conv.id, user_id=current_user.id))
    for uid in other_ids:
        db.add(models.Participant(conversation_id=conv.id, user_id=uid))
    db.commit()
    db.refresh(conv)
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=201,
        content=jsonable_encoder(helpers.conversation_to_dict(db, conv, current_user.id)),
    )


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = _require_participant(db, conversation_id, current_user.id)
    return helpers.conversation_to_dict(db, conv, current_user.id)


@router.patch("/{conversation_id}/members")
def update_members(
    conversation_id: int,
    payload: schemas.UpdateMembersRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add/remove group members. Caller must be a member; group only.

    Removing yourself = leave. A group that ends up with 0 members stays
    in the DB (invisible to everyone except admin).
    """
    conv = _require_participant(db, conversation_id, current_user.id)
    if not getattr(conv, "is_group", 0):
        raise HTTPException(
            status_code=400, detail="Members can only be changed for group conversations"
        )
    add_ids = list(dict.fromkeys(payload.add or []))
    remove_ids = list(dict.fromkeys(payload.remove or []))
    if add_ids:
        existing = db.query(models.User).filter(models.User.id.in_(add_ids)).all()
        if len(existing) != len(add_ids):
            raise HTTPException(status_code=400, detail="One or more users not found")
        current_ids = set(helpers.get_participant_ids(db, conversation_id))
        for uid in add_ids:
            if uid not in current_ids:
                db.add(models.Participant(conversation_id=conversation_id, user_id=uid))
    if remove_ids:
        db.query(models.Participant).filter(
            models.Participant.conversation_id == conversation_id,
            models.Participant.user_id.in_(remove_ids),
        ).delete(synchronize_session=False)
    db.commit()
    db.refresh(conv)
    return helpers.conversation_to_dict(db, conv, current_user.id)


@router.get("")
def list_conversations(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.Participant)
        .filter(models.Participant.user_id == current_user.id)
        .all()
    )
    convs = []
    for row in rows:
        conv = (
            db.query(models.Conversation)
            .filter(models.Conversation.id == row.conversation_id)
            .first()
        )
        if conv is not None:
            convs.append(conv)
    result = [helpers.conversation_to_dict(db, c, current_user.id) for c in convs]
    # Sort by last_message_at desc; conversations with no messages last,
    # ordered by created_at desc.
    result.sort(
        key=lambda d: (
            d["last_message_at"] is not None,
            d["last_message_at"] or d["created_at"],
        ),
        reverse=True,
    )
    return result


@router.get("/{conversation_id}/messages")
def get_messages(
    conversation_id: int,
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_participant(db, conversation_id, current_user.id)
    query = db.query(models.Message).filter(
        models.Message.conversation_id == conversation_id
    )
    if before_id is not None:
        query = query.filter(models.Message.id < before_id)
    msgs = query.order_by(models.Message.id.desc()).limit(limit).all()
    msgs = list(reversed(msgs))  # chronological, oldest first
    return [helpers.message_to_dict(db, m) for m in msgs]


@router.post("/{conversation_id}/messages", status_code=201)
async def send_message(
    conversation_id: int,
    payload: schemas.CreateMessageRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = _require_participant(db, conversation_id, current_user.id)
    if payload.kind != "text":
        raise HTTPException(
            status_code=400, detail="Use the upload endpoint for media messages"
        )
    from ..models import _utcnow

    now = _utcnow()
    msg = models.Message(
        conversation_id=conversation_id,
        sender_id=current_user.id,
        kind="text",
        content=payload.content,
        expires_at=helpers.expires_at_for(conv, now),
        created_at=now,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    msg_dict = helpers.message_to_dict(db, msg)
    participant_ids = helpers.get_participant_ids(db, conversation_id)
    await broadcast_message(msg_dict, participant_ids)
    return msg_dict


@router.patch("/{conversation_id}/disappearing")
def set_disappearing(
    conversation_id: int,
    payload: schemas.SetDisappearingRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set the disappearing-message timer for this conversation.

    Either participant may set or change it. 0 = off; 1–86400 seconds
    otherwise. Only messages sent while the timer is on get an expiry;
    existing messages are unaffected.
    """
    conv = _require_participant(db, conversation_id, current_user.id)
    conv.disappearing_seconds = payload.seconds
    db.commit()
    db.refresh(conv)
    return helpers.conversation_to_dict(db, conv, current_user.id)


@router.post("/{conversation_id}/read")
async def mark_read(
    conversation_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_participant(db, conversation_id, current_user.id)
    helpers.mark_as_read(db, conversation_id, current_user.id)
    participant_ids = helpers.get_participant_ids(db, conversation_id)
    await broadcast_read(conversation_id, current_user.id, participant_ids)
    return {"ok": True}


@router.get("/{conversation_id}/media")
def list_media(
    conversation_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_participant(db, conversation_id, current_user.id)
    msgs = (
        db.query(models.Message)
        .filter(
            models.Message.conversation_id == conversation_id,
            models.Message.kind != "text",
            # Call-history lines are not media.
            models.Message.kind != "call",
        )
        .order_by(models.Message.id.desc())
        .all()
    )
    # Expired media is gone from the chat album (admin still sees it in
    # the Media Library).
    msgs = [m for m in msgs if not helpers.is_expired(m)]
    return [
        {
            "id": m.id,
            "kind": m.kind,
            "media_url": m.media_url,
            "media_name": m.media_name,
            "media_size": m.media_size,
            "created_at": m.created_at,
            "sender_id": m.sender_id,
        }
        for m in msgs
    ]
