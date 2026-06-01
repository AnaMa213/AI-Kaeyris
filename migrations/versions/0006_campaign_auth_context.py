"""add JDR campaign auth context

Revision ID: d4c9b8a7e6f1
Revises: f2b7d9c1a805
Create Date: 2026-05-31 00:00:00.000000
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "d4c9b8a7e6f1"
down_revision: Union[str, Sequence[str], None] = "f2b7d9c1a805"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _scalar(sql: str, **params):
    return op.get_bind().execute(sa.text(sql), params).scalar()


def _execute(sql: str, **params) -> None:
    op.get_bind().execute(sa.text(sql), params)


def _backfill_default_campaign() -> None:
    bind = op.get_bind()
    users = bind.execute(
        sa.text(
            """
            SELECT id, profile, status
            FROM core_users
            ORDER BY created_at, id
            """
        )
    ).mappings().all()
    if not users:
        return

    campaign_id = _scalar(
        "SELECT id FROM jdr_campaigns ORDER BY created_at, id LIMIT 1"
    )
    if campaign_id is None:
        owner = next(
            (
                user
                for user in users
                if user["profile"] == "gm" and user["status"] == "active"
            ),
            users[0],
        )
        campaign_id = str(uuid4())
        _execute(
            """
            INSERT INTO jdr_campaigns (id, name, owner_user_id, created_at)
            VALUES (:id, :name, :owner_user_id, :created_at)
            """,
            id=campaign_id,
            name="Campagne par defaut",
            owner_user_id=owner["id"],
            created_at=datetime.now(UTC),
        )

    for user in users:
        exists = _scalar(
            """
            SELECT 1
            FROM jdr_campaign_members
            WHERE user_id = :user_id AND campaign_id = :campaign_id
            """,
            user_id=user["id"],
            campaign_id=campaign_id,
        )
        if exists is None:
            _execute(
                """
                INSERT INTO jdr_campaign_members
                    (user_id, campaign_id, role, character_id, joined_at)
                VALUES
                    (:user_id, :campaign_id, :role, NULL, :joined_at)
                """,
                user_id=user["id"],
                campaign_id=campaign_id,
                role="gm" if user["profile"] == "gm" else "player",
                joined_at=datetime.now(UTC),
            )
        _execute(
            """
            UPDATE core_users
            SET default_campaign_id = :campaign_id
            WHERE id = :user_id AND default_campaign_id IS NULL
            """,
            user_id=user["id"],
            campaign_id=campaign_id,
        )

    _execute(
        """
        UPDATE jdr_pjs
        SET campaign_id = :campaign_id
        WHERE campaign_id IS NULL
        """,
        campaign_id=campaign_id,
    )
    _execute(
        """
        UPDATE jdr_sessions
        SET campaign_id = :campaign_id
        WHERE campaign_id IS NULL
        """,
        campaign_id=campaign_id,
    )


def upgrade() -> None:
    op.create_table(
        "jdr_campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["core_users.id"],
            name=op.f("fk_jdr_campaigns_owner_user_id_core_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jdr_campaigns")),
    )
    op.create_index(
        op.f("ix_jdr_campaigns_owner_user_id"),
        "jdr_campaigns",
        ["owner_user_id"],
    )

    op.create_table(
        "jdr_campaign_members",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "gm",
                "player",
                name="jdr_campaign_role",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("character_id", sa.Uuid(), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["jdr_campaigns.id"],
            name=op.f("fk_jdr_campaign_members_campaign_id_jdr_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["character_id"],
            ["jdr_pjs.id"],
            name=op.f("fk_jdr_campaign_members_character_id_jdr_pjs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core_users.id"],
            name=op.f("fk_jdr_campaign_members_user_id_core_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "campaign_id", name=op.f("pk_jdr_campaign_members")
        ),
    )
    op.create_index(
        op.f("ix_jdr_campaign_members_character_id"),
        "jdr_campaign_members",
        ["character_id"],
    )

    with op.batch_alter_table("core_users") as batch_op:
        batch_op.add_column(sa.Column("default_campaign_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_core_users_default_campaign_id_jdr_campaigns"),
            "jdr_campaigns",
            ["default_campaign_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            op.f("ix_core_users_default_campaign_id"),
            ["default_campaign_id"],
        )

    with op.batch_alter_table("jdr_pjs") as batch_op:
        batch_op.add_column(sa.Column("campaign_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_jdr_pjs_campaign_id_jdr_campaigns"),
            "jdr_campaigns",
            ["campaign_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(op.f("ix_jdr_pjs_campaign_id"), ["campaign_id"])

    with op.batch_alter_table("jdr_sessions") as batch_op:
        batch_op.add_column(sa.Column("campaign_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_jdr_sessions_campaign_id_jdr_campaigns"),
            "jdr_campaigns",
            ["campaign_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(op.f("ix_jdr_sessions_campaign_id"), ["campaign_id"])

    _backfill_default_campaign()


def downgrade() -> None:
    with op.batch_alter_table("jdr_sessions") as batch_op:
        batch_op.drop_index(op.f("ix_jdr_sessions_campaign_id"))
        batch_op.drop_constraint(
            op.f("fk_jdr_sessions_campaign_id_jdr_campaigns"),
            type_="foreignkey",
        )
        batch_op.drop_column("campaign_id")

    with op.batch_alter_table("jdr_pjs") as batch_op:
        batch_op.drop_index(op.f("ix_jdr_pjs_campaign_id"))
        batch_op.drop_constraint(
            op.f("fk_jdr_pjs_campaign_id_jdr_campaigns"),
            type_="foreignkey",
        )
        batch_op.drop_column("campaign_id")

    with op.batch_alter_table("core_users") as batch_op:
        batch_op.drop_index(op.f("ix_core_users_default_campaign_id"))
        batch_op.drop_constraint(
            op.f("fk_core_users_default_campaign_id_jdr_campaigns"),
            type_="foreignkey",
        )
        batch_op.drop_column("default_campaign_id")

    op.drop_index(
        op.f("ix_jdr_campaign_members_character_id"),
        table_name="jdr_campaign_members",
    )
    op.drop_table("jdr_campaign_members")
    op.drop_index(
        op.f("ix_jdr_campaigns_owner_user_id"),
        table_name="jdr_campaigns",
    )
    op.drop_table("jdr_campaigns")
