import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chat import service
from app.chat.domain import ConversationStatus, EventKind, MessageAuthor
from app.chat.schemas import (
    AdminConversationRead,
    ConversationRead,
    CreateConversation,
    MessagePage,
    SendMessage,
    SendResult,
)
from app.db import get_db
from app.models import Account, ChatConversation
from app.permissions import PermissionCode
from app.schemas import Page
from app.security import customer_account, require_permission

# Access validation: every customer-chat route requires an authenticated account
# with user:access. The Customer dependency below also requires chat:own.
router = APIRouter(
    prefix="/chat/conversations",
    tags=["customer chat"],
    dependencies=[Depends(require_permission(PermissionCode.USER_ACCESS))],
)
DB = Annotated[Session, Depends(get_db)]
# Validation only: load the authenticated account, check chat:own and require
# a linked customer profile. This dependency does not save or process messages.
Customer = Annotated[Account, Depends(customer_account(PermissionCode.CHAT_OWN))]
Admin = Annotated[Account, Depends(require_permission(PermissionCode.CHAT_MANAGE))]
admin_router = APIRouter(
    prefix="/chat/conversations",
    tags=["admin chat"],
    dependencies=[Depends(require_permission(PermissionCode.ADMIN_ACCESS))],
)


def owned_conversation(
    db: Session, account: Account, conversation_id: uuid.UUID
) -> ChatConversation:
    # Ownership validation only: read a conversation belonging to both this
    # account and its customer profile. A missing or foreign conversation is 404.
    row = db.scalar(
        select(ChatConversation).where(
            ChatConversation.id == conversation_id,
            ChatConversation.account_id == account.id,
            ChatConversation.customer_id == account.customer_id,
        )
    )
    if row is None:
        raise HTTPException(404, "Conversation not found")
    return row


@router.post("", response_model=ConversationRead, status_code=201)
def create(payload: CreateConversation, account: Customer, db: DB) -> ChatConversation:
    row = ChatConversation(
        **payload.model_dump(), account_id=account.id, customer_id=account.customer_id
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except IntegrityError:
        db.rollback()
        row = db.get(ChatConversation, payload.id)
        if row is None or row.account_id != account.id or row.title != payload.title:
            raise HTTPException(409, "Conversation ID is already in use") from None
    return row


@router.get("", response_model=Page)
def conversations(
    account: Customer, db: DB, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)
) -> Page:
    query = select(ChatConversation).where(
        ChatConversation.account_id == account.id,
        ChatConversation.customer_id == account.customer_id,
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(ChatConversation.updated_at.desc(), ChatConversation.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(
        items=[ConversationRead.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{conversation_id}", response_model=ConversationRead)
def detail(conversation_id: uuid.UUID, account: Customer, db: DB) -> ChatConversation:
    return owned_conversation(db, account, conversation_id)


@router.get("/{conversation_id}/messages", response_model=MessagePage)
def messages(
    conversation_id: uuid.UUID,
    account: Customer,
    db: DB,
    after: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    before: int | None = Query(None, ge=1),
    latest: bool = False,
) -> dict:
    owned_conversation(db, account, conversation_id)
    return service.message_page(db, conversation_id, after, limit, before, latest)


# Customer-send entry point (mounted under /api/v1/chat/conversations).
# FastAPI validates/serializes the returned dictionary using SendResult.
@router.post("/{conversation_id}/messages", response_model=SendResult)
def send(conversation_id: uuid.UUID, payload: SendMessage, account: Customer, db: DB) -> dict:
    # Request validation: before this function runs, FastAPI validates the path
    # UUID and SendMessage body, resolves the DB session, and checks access above.
    # Ownership validation only: reject access to another customer's conversation.
    owned_conversation(db, account, conversation_id)
    # Message processing: save the message and, when AI is active, wait for the
    # graph to finish in this HTTP request. No background job is started here.
    return service.send_customer(db, account.id, conversation_id, payload)


@router.post("/{conversation_id}/messages/{message_id}/retry", response_model=SendResult)
def retry(conversation_id: uuid.UUID, message_id: uuid.UUID, account: Customer, db: DB) -> dict:
    owned_conversation(db, account, conversation_id)
    return service.retry_message(db, account.id, conversation_id, message_id)


@router.post("/{conversation_id}/handoff", response_model=ConversationRead)
def request_human(conversation_id: uuid.UUID, account: Customer, db: DB) -> ChatConversation:
    owned_conversation(db, account, conversation_id)
    conversation = service.locked_conversation(db, conversation_id)
    service.handoff(db, conversation, account.id, "Customer requested a support agent")
    db.commit()
    db.refresh(conversation)
    return conversation


@admin_router.get("", response_model=Page)
def inbox(
    account: Admin,
    db: DB,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: ConversationStatus | None = None,
) -> Page:
    query = select(ChatConversation)
    if status:
        query = query.where(ChatConversation.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(ChatConversation.updated_at.desc(), ChatConversation.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(
        items=[ConversationRead.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@admin_router.get("/{conversation_id}", response_model=AdminConversationRead)
def admin_detail(conversation_id: uuid.UUID, account: Admin, db: DB) -> AdminConversationRead:
    row = db.get(ChatConversation, conversation_id)
    if row is None:
        raise HTTPException(404, "Conversation not found")
    return AdminConversationRead(
        **ConversationRead.model_validate(row).model_dump(),
        can_reply=row.assigned_admin_id == account.id,
    )


@admin_router.get("/{conversation_id}/messages", response_model=MessagePage)
def admin_messages(
    conversation_id: uuid.UUID,
    account: Admin,
    db: DB,
    after: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    before: int | None = Query(None, ge=1),
    latest: bool = False,
) -> dict:
    admin_detail(conversation_id, account, db)
    return service.message_page(db, conversation_id, after, limit, before, latest)


@admin_router.post("/{conversation_id}/messages", response_model=SendResult)
def admin_send(conversation_id: uuid.UUID, payload: SendMessage, account: Admin, db: DB) -> dict:
    conversation = service.locked_conversation(db, conversation_id)
    service.assigned(conversation, account.id)
    message = service.save_message(db, conversation, account.id, MessageAuthor.ADMIN, payload)
    db.commit()
    return service.message_result(db, message)


@admin_router.post("/{conversation_id}/claim", response_model=ConversationRead)
def claim(conversation_id: uuid.UUID, account: Admin, db: DB) -> ChatConversation:
    return service.admin_transition(db, conversation_id, account.id, EventKind.CLAIM)


@admin_router.post("/{conversation_id}/resolve", response_model=ConversationRead)
def resolve(conversation_id: uuid.UUID, account: Admin, db: DB) -> ChatConversation:
    return service.admin_transition(db, conversation_id, account.id, EventKind.RESOLVE)


@admin_router.post("/{conversation_id}/resume", response_model=ConversationRead)
def resume(conversation_id: uuid.UUID, account: Admin, db: DB) -> ChatConversation:
    return service.admin_transition(db, conversation_id, account.id, EventKind.RESUME)
