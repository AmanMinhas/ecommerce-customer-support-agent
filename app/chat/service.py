"""Transactional transcript and human support operations."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi import HTTPException
from httpx import TransportError
from psycopg import Connection
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session

from app.chat.domain import ConversationStatus, EventKind, MessageAuthor, RunStatus
from app.chat.runtime import ModelUnavailable, get_model, run_graph
from app.chat.schemas import MessageRead, SendMessage
from app.chat.tools import AccessChanged, current_customer
from app.config import settings
from app.models import ChatConversation, ChatEvent, ChatMessage, ChatRun


def locked_conversation(db: Session, conversation_id: uuid.UUID) -> ChatConversation:
    # Concurrency control: lock this conversation row until commit/rollback so
    # status changes and message sequence allocation cannot race. Refresh cached data.
    row = db.scalar(
        select(ChatConversation)
        .where(ChatConversation.id == conversation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Conversation not found")
    return row


def append_message(
    db: Session,
    conversation: ChatConversation,
    author: MessageAuthor,
    content: str,
    author_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    reply_to_id: uuid.UUID | None = None,
) -> ChatMessage:
    """Caller must hold the conversation row lock."""
    message = ChatMessage(
        conversation_id=conversation.id,
        sequence=conversation.next_sequence,
        author=author,
        content=content,
        author_id=author_id,
        client_id=client_id,
        reply_to_id=reply_to_id,
    )
    conversation.next_sequence += 1
    conversation.updated_at = datetime.now(UTC)
    db.add(message)
    # Persistence: flush INSERTs to obtain the message ID; the caller still owns
    # the commit, so message/sequence changes can be saved in one transaction.
    db.flush()
    return message


def save_message(
    db: Session,
    conversation: ChatConversation,
    actor_id: uuid.UUID,
    author: MessageAuthor,
    payload: SendMessage,
) -> ChatMessage:
    # Duplicate-send validation: the same sender/client_id must have the same
    # content. Return the saved message on a repeated request without inserting again.
    existing = db.scalar(
        select(ChatMessage).where(
            ChatMessage.conversation_id == conversation.id,
            ChatMessage.author_id == actor_id,
            ChatMessage.client_id == payload.client_id,
        )
    )
    if existing:
        if existing.content != payload.content:
            raise HTTPException(409, "Message ID was already used with different content")
        return existing
    # Conversation-state validation only: reject new messages after resolution.
    # Identical duplicate sends above remain safe even if the chat is now resolved.
    if conversation.status == ConversationStatus.RESOLVED:
        raise HTTPException(409, "This conversation is resolved. Start a new conversation.")
    # Persistence: append the new message in the caller's transaction.
    return append_message(db, conversation, author, payload.content, actor_id, payload.client_id)


def message_result(db: Session, message: ChatMessage) -> dict:
    # Response assembly only: return the saved submitted message and run status.
    # For customer sends, the assistant reply is read separately from the transcript.
    run = db.scalar(select(ChatRun).where(ChatRun.message_id == message.id))
    return {
        "message": MessageRead.model_validate(message),
        "run": None
        if run is None
        else {
            "id": run.id,
            "status": run.status,
            "error_code": run.error_code,
            "attempts": run.attempts,
        },
    }


def message_page(
    db: Session,
    conversation_id: uuid.UUID,
    after: int,
    limit: int,
    before: int | None = None,
    latest: bool = False,
) -> dict:
    if after and (before is not None or latest):
        raise HTTPException(422, "Use either after, before, or latest")
    query = select(ChatMessage, ChatRun.status, ChatRun.error_code)
    query = query.outerjoin(ChatRun, ChatRun.message_id == ChatMessage.id).where(
        ChatMessage.conversation_id == conversation_id, ChatMessage.sequence > after
    )
    if before is not None:
        query = query.where(ChatMessage.sequence < before)
    descending = before is not None or latest
    query = query.order_by(ChatMessage.sequence.desc() if descending else ChatMessage.sequence)
    rows = db.execute(query.limit(limit + 1)).all()
    visible = rows[:limit][::-1] if descending else rows[:limit]
    return {
        "items": [
            {
                **MessageRead.model_validate(m).model_dump(),
                "processing_status": status,
                "error_code": error,
            }
            for m, status, error in visible
        ],
        "has_more": len(rows) > limit,
    }


def handoff(
    db: Session, conversation: ChatConversation, actor_id: uuid.UUID | None, reason: str
) -> None:
    # State validation only: avoid repeating a handoff after leaving AI mode.
    if conversation.status != ConversationStatus.AI_ACTIVE:
        return
    # Human handoff: persist the waiting state, cancel outstanding AI work, and
    # append an event plus a customer-visible notice. The caller commits them.
    conversation.status = ConversationStatus.WAITING
    conversation.handoff_reason = reason[:500]
    cancel_pending(db, conversation.id)
    db.add(ChatEvent(conversation_id=conversation.id, actor_id=actor_id, kind=EventKind.HANDOFF))
    append_message(
        db,
        conversation,
        MessageAuthor.SYSTEM,
        "Your conversation is waiting for a support agent. You can return later for a reply.",
    )


def assigned(conversation: ChatConversation, admin_id: uuid.UUID) -> None:
    if conversation.assigned_admin_id != admin_id:
        raise HTTPException(409, "Claim this conversation before changing it")


def cancel_pending(db: Session, conversation_id: uuid.UUID) -> None:
    # Persistence: invalidate running/failed attempts on a handoff or admin state change.
    db.execute(
        update(ChatRun)
        .where(
            ChatRun.conversation_id == conversation_id,
            ChatRun.status.in_([RunStatus.RUNNING, RunStatus.FAILED]),
        )
        .values(status=RunStatus.CANCELLED)
    )


def admin_transition(
    db: Session, conversation_id: uuid.UUID, admin_id: uuid.UUID, action: EventKind
) -> ChatConversation:
    conversation = locked_conversation(db, conversation_id)
    if action == EventKind.CLAIM:
        if conversation.assigned_admin_id not in (None, admin_id):
            raise HTTPException(409, "Another support agent has claimed this conversation")
        if conversation.status == ConversationStatus.RESOLVED:
            raise HTTPException(409, "This conversation is resolved")
        conversation.assigned_admin_id = admin_id
        conversation.status = ConversationStatus.HUMAN_ACTIVE
    else:
        assigned(conversation, admin_id)
        if action == EventKind.RESOLVE:
            conversation.status = ConversationStatus.RESOLVED
        elif action == EventKind.RESUME:
            conversation.status = ConversationStatus.AI_ACTIVE
            conversation.assigned_admin_id = None
            conversation.handoff_reason = None
    conversation.updated_at = datetime.now(UTC)
    cancel_pending(db, conversation_id)
    db.add(ChatEvent(conversation_id=conversation.id, actor_id=admin_id, kind=action))
    db.commit()
    db.refresh(conversation)
    return conversation


@contextmanager
def execution_lock(engine: Engine, conversation_id: uuid.UUID) -> Iterator[Connection]:
    """Session lock survives commits, never holds a SQL transaction during inference."""
    # Concurrency control: only one AI execution may run for this conversation.
    # This session-level advisory lock outlives the short row-lock transactions.
    key = int.from_bytes(conversation_id.bytes[:8], "big", signed=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        if not acquired:
            raise HTTPException(409, "A reply is still being processed. Refresh before retrying.")
        try:
            # The same connection also hosts the graph's PostgreSQL checkpoints.
            yield connection.connection.driver_connection
        finally:
            # Resource cleanup: release the execution lock on success or failure.
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def process_message(
    db: Session,
    connection: Connection,
    account_id: uuid.UUID,
    conversation: ChatConversation,
    message: ChatMessage,
) -> dict:
    from google.genai.errors import APIError
    from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

    # Run/state validation: completed or cancelled attempts must not call Gemini
    # again. If human support took over, cancel the attempt and return its status.
    run = db.scalar(select(ChatRun).where(ChatRun.message_id == message.id))
    if run and run.status in (RunStatus.COMPLETED, RunStatus.CANCELLED):
        return message_result(db, message)
    if conversation.status != ConversationStatus.AI_ACTIVE:
        if run:
            run.status = RunStatus.CANCELLED
            db.commit()
        return message_result(db, message)
    # Attempt tracking: mark this execution running, increment attempts, and clear
    # an earlier failure. The run is distinct from the visible customer message.
    if run:
        run.attempts += 1
        run.status = RunStatus.RUNNING
        run.error_code = None
    else:
        run = ChatRun(
            conversation_id=conversation.id,
            message_id=message.id,
            status=RunStatus.RUNNING,
            model=settings.gemini_model,
        )
        db.add(run)
    conversation_id, message_id = conversation.id, message.id
    # A later human action invalidates this execution even if AI is subsequently resumed.
    event_marker = db.scalar(
        select(ChatEvent.id)
        .where(ChatEvent.conversation_id == conversation_id)
        .order_by(ChatEvent.created_at.desc(), ChatEvent.id.desc())
        .limit(1)
    )
    # Prompt context: load at most 40 messages through this customer message,
    # then reverse the newest-first query into chronological conversation order.
    transcript = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id, ChatMessage.sequence <= message.sequence
        )
        .order_by(ChatMessage.sequence.desc())
        .limit(40)
    ).all()[::-1]
    # Transaction boundary: make attempt state durable and release the row lock
    # before inference. The separate advisory execution lock stays held.
    db.commit()
    try:
        # LLM execution: build the Gemini client and run the model/tool graph
        # synchronously. The actual Gemini request is bound_model.invoke() in runtime.py.
        result = run_graph(
            db.bind, connection, account_id, conversation_id, message_id, transcript, get_model()
        )
    except AccessChanged:
        # Access validation failed during execution; suppress output below.
        result = None
    except (
        TimeoutError,
        ConnectionError,
        TransportError,
        ModelUnavailable,
        APIError,
        ChatGoogleGenerativeAIError,
    ):
        # Expected failure recovery: keep the customer message, record a safe
        # retryable error unless cancelled, and return run metadata normally (HTTP 200).
        # Reload under the row lock so a concurrent cancellation stays cancelled.
        db.rollback()
        locked_conversation(db, conversation_id)
        run = db.scalar(
            select(ChatRun)
            .where(ChatRun.message_id == message_id)
            .execution_options(populate_existing=True)
        )
        if run.status != RunStatus.CANCELLED:
            run.status, run.error_code = RunStatus.FAILED, "assistant_unavailable"
        db.commit()
        return message_result(db, db.get(ChatMessage, message_id))
    except Exception:
        # Preserve a retryable record, but let unexpected errors reach error monitoring.
        db.rollback()
        locked_conversation(db, conversation_id)
        run = db.scalar(
            select(ChatRun)
            .where(ChatRun.message_id == message_id)
            .execution_options(populate_existing=True)
        )
        if run.status != RunStatus.CANCELLED:
            run.status, run.error_code = RunStatus.FAILED, "processing_failed"
        db.commit()
        raise
    # Revalidate before publishing: a human action or revoked access may have
    # occurred while the graph ran. Reload state under the conversation row lock.
    conversation = locked_conversation(db, conversation_id)
    run = db.scalar(
        select(ChatRun)
        .where(ChatRun.message_id == message_id)
        .execution_options(populate_existing=True)
    )
    latest_event = db.scalar(
        select(ChatEvent.id)
        .where(ChatEvent.conversation_id == conversation_id)
        .order_by(ChatEvent.created_at.desc(), ChatEvent.id.desc())
        .limit(1)
    )
    try:
        # Authorization validation only: discard cached ORM attributes and check
        # current account grants, conversation ownership and AI mode again.
        db.expire_all()
        current_customer(db, account_id, conversation_id)
    except AccessChanged:
        result = None
    if run.status == RunStatus.CANCELLED or result is None or latest_event != event_marker:
        # Discard stale output, including a takeover followed by a return to AI.
        run.status = RunStatus.CANCELLED
    else:
        # Publish the outcome: persist a human handoff or one assistant reply.
        if result.get("handoff_reason"):
            handoff(db, conversation, None, result["handoff_reason"])
        elif result.get("answer"):
            # Duplicate-reply validation: a recovered checkpoint may already have
            # been published. reply_to_id links the answer to the triggering message.
            existing = db.scalar(
                select(ChatMessage.id).where(ChatMessage.reply_to_id == message_id)
            )
            if existing is None:
                append_message(
                    db,
                    conversation,
                    MessageAuthor.ASSISTANT,
                    result["answer"],
                    reply_to_id=message_id,
                )
        run.status = RunStatus.COMPLETED
    # Commit the reply/handoff and final run status together, then build the response.
    db.commit()
    return message_result(db, db.get(ChatMessage, message_id))


def send_customer(
    db: Session, account_id: uuid.UUID, conversation_id: uuid.UUID, payload: SendMessage
) -> dict:
    # Conversation routing: waiting/human chats only save the customer message.
    # save_message() rejects new sends to resolved chats; no graph runs in this branch.
    conversation = locked_conversation(db, conversation_id)
    if conversation.status != ConversationStatus.AI_ACTIVE:
        message = save_message(db, conversation, account_id, MessageAuthor.CUSTOMER, payload)
        db.commit()
        return message_result(db, message)
    # Release the initial row lock before acquiring the longer-lived AI lock.
    db.commit()
    with execution_lock(db.bind, conversation_id) as connection:
        # Refresh/lock the row again because its status could have changed while
        # acquiring the execution lock. Find duplicate sends and unfinished attempts.
        conversation = locked_conversation(db, conversation_id)
        previous = db.scalar(
            select(ChatMessage).where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.author_id == account_id,
                ChatMessage.client_id == payload.client_id,
            )
        )
        pending = db.scalar(
            select(ChatRun).where(
                ChatRun.conversation_id == conversation_id,
                ChatRun.status.in_([RunStatus.RUNNING, RunStatus.FAILED]),
            )
        )
        # Processing-order validation only: block a new message while an earlier
        # AI attempt needs recovery. Resubmitting that same message is allowed.
        if pending and (previous is None or pending.message_id != previous.id):
            raise HTTPException(
                409, "Retry the interrupted reply or request a support agent first."
            )
        # Persistence: save/reuse the customer message and create an AI run only
        # for a new message whose conversation is still in AI mode.
        message = save_message(db, conversation, account_id, MessageAuthor.CUSTOMER, payload)
        if previous is None and conversation.status == ConversationStatus.AI_ACTIVE:
            # Accept the message and its recoverable execution record atomically.
            db.add(
                ChatRun(
                    conversation_id=conversation_id,
                    message_id=message.id,
                    status=RunStatus.RUNNING,
                    attempts=0,
                    model=settings.gemini_model,
                )
            )
        db.commit()
        # Processing guard: skip inference if no AI run was created, for example
        # after a concurrent change to human mode following the initial state check.
        if db.scalar(select(ChatRun.id).where(ChatRun.message_id == message.id)) is None:
            return message_result(db, message)
        # AI processing: the message is already durable if inference fails or
        # this request is interrupted. Completed duplicates return without inference.
        return process_message(db, connection, account_id, conversation, message)


def retry_message(
    db: Session, account_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> dict:
    db.commit()
    with execution_lock(db.bind, conversation_id) as connection:
        conversation = locked_conversation(db, conversation_id)
        message = db.scalar(
            select(ChatMessage).where(
                ChatMessage.id == message_id,
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.author_id == account_id,
                ChatMessage.author == MessageAuthor.CUSTOMER,
            )
        )
        if message is None:
            raise HTTPException(404, "Message not found")
        if db.scalar(select(ChatRun.id).where(ChatRun.message_id == message_id)) is None:
            raise HTTPException(
                409, "This message was sent to human support and has no AI reply to retry"
            )
        return process_message(db, connection, account_id, conversation, message)
