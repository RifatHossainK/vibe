from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---- Auth ----

class RegisterRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)
    # Optional: 3-20 chars [a-z0-9_], lowercase. When omitted the server
    # auto-generates one from the email local-part.
    username: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    display_name: str
    username: Optional[str] = None
    email: str
    role: str
    avatar_url: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=50)
    username: Optional[str] = None


class RegisterResponse(UserOut):
    access_token: str
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---- Conversations / messages ----

class CreateConversationRequest(BaseModel):
    user_id: int


class CreateGroupRequest(BaseModel):
    # Length/count validated in the handler (400s, not 422s): name must
    # be 1-100 non-blank chars, user_ids 1-49 others after dedup/self.
    name: str
    user_ids: list[int]


class UpdateMembersRequest(BaseModel):
    add: Optional[list[int]] = None
    remove: Optional[list[int]] = None


class OtherUserOut(BaseModel):
    id: int
    display_name: str
    username: Optional[str] = None
    email: str
    avatar_url: Optional[str] = None


class LastMessageOut(BaseModel):
    id: int
    sender_id: int
    kind: str
    content: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    expired: bool = False
    deleted: bool = False
    deleted_for_everyone: bool = False


class ConversationOut(BaseModel):
    id: int
    other_user: OtherUserOut
    disappearing_seconds: int = 0
    created_at: datetime
    last_message: Optional[LastMessageOut] = None
    last_message_at: Optional[datetime] = None
    unread_count: int


class SetDisappearingRequest(BaseModel):
    # 0 = off; otherwise 1–86400 seconds (max 24h).
    seconds: int = Field(..., ge=0, le=86400)


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    sender_id: int
    kind: str
    content: Optional[str] = None
    media_url: Optional[str] = None
    media_name: Optional[str] = None
    media_size: Optional[int] = None
    expires_at: Optional[datetime] = None
    expired: bool = False
    deleted: bool = False
    deleted_for_everyone: bool = False
    created_at: datetime
    seen_by: list[int] = []


class CreateMessageRequest(BaseModel):
    kind: str = "text"
    content: str = Field(..., min_length=1, max_length=5000)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be blank")
        return v


class MediaItemOut(BaseModel):
    id: int
    kind: str
    media_url: Optional[str] = None
    media_name: Optional[str] = None
    media_size: Optional[int] = None
    created_at: datetime
    sender_id: int
