"""Add accounts and database-managed role permissions.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "customer_id",
            sa.Uuid(),
            sa.ForeignKey("customers.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("email = lower(trim(email))", name="ck_accounts_normalized_email"),
    )
    for name in ("roles", "permissions"):
        op.create_table(
            name,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False, unique=True),
        )
    op.create_table(
        "account_roles",
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
        ),
    )
    op.create_index("ix_account_roles_role_id", "account_roles", ["role_id"])
    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "permission_id",
            sa.Uuid(),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_role_permissions_permission_id", "role_permissions", ["permission_id"])


def downgrade() -> None:
    for name in ("role_permissions", "account_roles", "permissions", "roles", "accounts"):
        op.drop_table(name)
