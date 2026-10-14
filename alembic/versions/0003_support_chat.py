"""Persistent customer support conversations.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("assigned_admin_id", sa.Uuid(), sa.ForeignKey("accounts.id")),
        sa.Column("handoff_reason", sa.String(500)),
        sa.Column("next_sequence", sa.Integer(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("status IN ('ai_active','waiting_for_admin','human_active','resolved')"),
        sa.CheckConstraint("next_sequence > 0"),
    )
    for field in ("account_id", "customer_id", "status", "assigned_admin_id"):
        op.create_index(f"ix_chat_conversations_{field}", "chat_conversations", [field])
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("author", sa.String(20), nullable=False),
        sa.Column("author_id", sa.Uuid(), sa.ForeignKey("accounts.id")),
        sa.Column("client_id", sa.Uuid()),
        sa.Column(
            "reply_to_id",
            sa.Uuid(),
            sa.ForeignKey("chat_messages.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("conversation_id", "sequence"),
        sa.UniqueConstraint("conversation_id", "author_id", "client_id"),
        sa.CheckConstraint("author IN ('customer','assistant','admin','system')"),
        sa.CheckConstraint("sequence > 0"),
        sa.CheckConstraint("length(trim(content)) > 0 AND length(content) <= 8000"),
    )
    op.create_index("ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"])
    op.create_table(
        "chat_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(50)),
        sa.Column("model", sa.String(100)),
        sa.Column("workflow_version", sa.String(30), nullable=False),
        *timestamps(),
        sa.CheckConstraint("status IN ('running','completed','failed','cancelled')"),
    )
    op.create_index("ix_chat_runs_conversation_id", "chat_runs", ["conversation_id"])
    op.create_table(
        "chat_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("accounts.id")),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_chat_events_conversation_id", "chat_events", ["conversation_id"])
    # Grant the new capabilities to existing built-in roles exactly once.
    op.execute(
        "INSERT INTO permissions (id, name) VALUES (gen_random_uuid(), 'chat:own'), (gen_random_uuid(), 'chat:manage') ON CONFLICT (name) DO NOTHING"
    )
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r JOIN permissions p ON (r.name='user' AND p.name='chat:own') OR (r.name='admin' AND p.name='chat:manage') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    for table in ("chat_events", "chat_runs", "chat_messages", "chat_conversations"):
        op.drop_table(table)
    op.execute("DELETE FROM permissions WHERE name IN ('chat:own', 'chat:manage')")
