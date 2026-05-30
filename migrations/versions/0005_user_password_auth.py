"""add browser user/password authentication tables

Revision ID: f2b7d9c1a805
Revises: c7e3a2b04f56
Create Date: 2026-05-27 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2b7d9c1a805"
down_revision: Union[str, Sequence[str], None] = "c7e3a2b04f56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "core_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=150), nullable=False),
        sa.Column(
            "profile",
            sa.Enum(
                "gm",
                "user",
                name="core_profile",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "inactive",
                "deleted",
                name="core_user_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["jdr_api_keys.id"],
            name=op.f("fk_core_users_api_key_id_jdr_api_keys"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_core_users")),
        sa.UniqueConstraint("username", name=op.f("uq_core_users_username")),
    )
    op.create_index(op.f("ix_core_users_api_key_id"), "core_users", ["api_key_id"])
    op.create_index(op.f("ix_core_users_username"), "core_users", ["username"])

    op.create_table(
        "core_web_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("client_ip", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core_users.id"],
            name=op.f("fk_core_web_sessions_user_id_core_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_core_web_sessions")),
        sa.UniqueConstraint(
            "token_hash", name=op.f("uq_core_web_sessions_token_hash")
        ),
    )
    op.create_index(
        op.f("ix_core_web_sessions_expires_at"),
        "core_web_sessions",
        ["expires_at"],
    )
    op.create_index(
        op.f("ix_core_web_sessions_token_hash"),
        "core_web_sessions",
        ["token_hash"],
    )
    op.create_index(
        op.f("ix_core_web_sessions_user_id"),
        "core_web_sessions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_core_web_sessions_user_id"), table_name="core_web_sessions")
    op.drop_index(
        op.f("ix_core_web_sessions_token_hash"), table_name="core_web_sessions"
    )
    op.drop_index(
        op.f("ix_core_web_sessions_expires_at"), table_name="core_web_sessions"
    )
    op.drop_table("core_web_sessions")
    op.drop_index(op.f("ix_core_users_username"), table_name="core_users")
    op.drop_index(op.f("ix_core_users_api_key_id"), table_name="core_users")
    op.drop_table("core_users")
