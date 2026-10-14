import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.chat.domain import ConversationStatus, MessageAuthor, RunStatus


class CreateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: uuid.UUID
    title: str = Field(default="Support conversation", min_length=1, max_length=120)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    status: ConversationStatus
    assigned_admin_id: uuid.UUID | None
    handoff_reason: str | None
    created_at: datetime
    updated_at: datetime


class AdminConversationRead(ConversationRead):
    can_reply: bool


class SendMessage(BaseModel):
    # Request validation only: reject unknown fields and trim surrounding whitespace.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    # The client reuses this UUID when resending the same message so the service
    # can detect duplicates; it is distinct from the database message ID.
    client_id: uuid.UUID
    # After trimming, content must contain 1-4000 characters (internal spaces count).
    content: str = Field(min_length=1, max_length=4000)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sequence: int
    author: MessageAuthor
    content: str
    created_at: datetime


class TranscriptMessage(MessageRead):
    processing_status: RunStatus | None = None
    error_code: str | None = None


class MessagePage(BaseModel):
    items: list[TranscriptMessage]
    has_more: bool


class RunRead(BaseModel):
    id: uuid.UUID
    status: RunStatus
    error_code: str | None
    attempts: int


class SendResult(BaseModel):
    # Response contract: the saved submitted message plus AI processing metadata.
    # For customer sends, assistant replies are separate GET .../messages records.
    message: MessageRead
    # A new message sent while waiting for/having human support has no AI run.
    run: RunRead | None
