from enum import StrEnum


class ConversationStatus(StrEnum):
    AI_ACTIVE = "ai_active"
    WAITING = "waiting_for_admin"
    HUMAN_ACTIVE = "human_active"
    RESOLVED = "resolved"


class MessageAuthor(StrEnum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"
    ADMIN = "admin"
    SYSTEM = "system"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventKind(StrEnum):
    HANDOFF = "handoff"
    CLAIM = "claim"
    RESOLVE = "resolve"
    RESUME = "resume"
