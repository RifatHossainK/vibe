from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from .database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    display_name = Column(String(50), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="user", nullable=False)
    # Unique handle, 3-20 chars [a-z0-9_], lowercase. Nullable only so
    # pre-existing rows can be backfilled by the startup migration.
    username = Column(String(20), unique=True, index=True, nullable=True)
    # On-disk path of the profile picture (under DATA_DIR/uploads/avatars/).
    # None means no picture — the UI shows initials instead.
    avatar_path = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    # Group chat: 0 = 1:1 conversation, 1 = group. Groups have a name;
    # 1:1 conversations have name NULL.
    is_group = Column(Integer, default=0, nullable=False)
    name = Column(String(100), nullable=True)
    # Disappearing-message timer in seconds. 0 = off. Set by either
    # participant; applies to messages sent while it is on.
    disappearing_seconds = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class Participant(Base):
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (UniqueConstraint("conversation_id", "user_id"),)


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    sender_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(20), nullable=False)  # text|image|video|audio|voice
    content = Column(Text, nullable=True)
    media_url = Column(String(512), nullable=True)
    media_name = Column(String(255), nullable=True)
    media_size = Column(Integer, nullable=True)
    # Internal only: on-disk path of the uploaded file (not in the API contract).
    file_path = Column(String(1024), nullable=True)
    # When this message disappears from the participants' chat views.
    # None = never (timer was off when sent). Messages are NEVER deleted
    # from the database — the admin panel still shows them.
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True, nullable=False)


class MessageRead(Base):
    __tablename__ = "message_reads"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(
        Integer, ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    read_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("message_id", "user_id"),)
