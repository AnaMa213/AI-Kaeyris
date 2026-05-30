"""add campaign auth context

Revision ID: a3d9c1e4b672
Revises: f2b7d9c1a805
Create Date: 2026-05-30 00:00:00.000000
"""

from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3d9c1e4b672"
down_revision: Union[str, Sequence[str], None] = "f2b7d9c1a805"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_CAMPAIGN_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_CAMPAIGN_ID_SQLITE = "00000000000000000000000000000001"
DEFAULT_CAMPAIGN_NAME = "Campagne par defaut"


def _default_campaign_id() -> str:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return DEFAULT_CAMPAIGN_ID_SQLITE
    return DEFAULT_CAMPAIGN_ID


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["core_users.id"],
            name=op.f("fk_campaigns_owner_id_core_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
    )
    op.create_index(op.f("ix_campaigns_owner_id"), "campaigns", ["owner_id"])

    op.create_table(
        "campaign_members",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "mj",
                "player",
                name="campaign_role",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("character_id", sa.Uuid(), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_campaign_members_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["character_id"],
            ["jdr_pjs.id"],
            name=op.f("fk_campaign_members_character_id_jdr_pjs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core_users.id"],
            name=op.f("fk_campaign_members_user_id_core_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "campaign_id", name=op.f("pk_campaign_members")
        ),
    )
    op.create_index(
        op.f("ix_campaign_members_character_id"),
        "campaign_members",
        ["character_id"],
    )

    with op.batch_alter_table("core_users") as batch:
        batch.add_column(sa.Column("default_campaign_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_core_users_default_campaign_id_campaigns"),
            "campaigns",
            ["default_campaign_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        op.f("ix_core_users_default_campaign_id"),
        "core_users",
        ["default_campaign_id"],
    )

    with op.batch_alter_table("jdr_sessions") as batch:
        batch.add_column(sa.Column("campaign_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_jdr_sessions_campaign_id_campaigns"),
            "campaigns",
            ["campaign_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(op.f("ix_jdr_sessions_campaign_id"), "jdr_sessions", ["campaign_id"])

    with op.batch_alter_table("jdr_pjs") as batch:
        batch.add_column(sa.Column("campaign_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_jdr_pjs_campaign_id_campaigns"),
            "campaigns",
            ["campaign_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(op.f("ix_jdr_pjs_campaign_id"), "jdr_pjs", ["campaign_id"])

    _backfill_default_campaign()


def _backfill_default_campaign() -> None:
    bind = op.get_bind()
    default_id = _default_campaign_id()
    now = datetime.now(UTC)
    users = bind.execute(
        sa.text(
            """
            SELECT id, profile
            FROM core_users
            WHERE status = 'active'
            ORDER BY
              CASE WHEN profile = 'gm' THEN 0 ELSE 1 END,
              created_at ASC,
              id ASC
            """
        )
    ).mappings().all()
    if not users:
        return

    owner_id = users[0]["id"]
    bind.execute(
        sa.text(
            """
            INSERT INTO campaigns (id, name, owner_id, created_at)
            VALUES (:id, :name, :owner_id, :created_at)
            """
        ),
        {
            "id": default_id,
            "name": DEFAULT_CAMPAIGN_NAME,
            "owner_id": owner_id,
            "created_at": now,
        },
    )

    for user in users:
        bind.execute(
            sa.text(
                """
                INSERT INTO campaign_members
                  (user_id, campaign_id, role, character_id, joined_at)
                VALUES (:user_id, :campaign_id, :role, NULL, :joined_at)
                """
            ),
            {
                "user_id": user["id"],
                "campaign_id": default_id,
                "role": "mj" if user["profile"] == "gm" else "player",
                "joined_at": now,
            },
        )

    bind.execute(
        sa.text(
            """
            UPDATE core_users
            SET default_campaign_id = :campaign_id
            WHERE status = 'active'
            """
        ),
        {"campaign_id": default_id},
    )
    bind.execute(
        sa.text(
            """
            UPDATE jdr_sessions
            SET campaign_id = :campaign_id
            WHERE campaign_id IS NULL
            """
        ),
        {"campaign_id": default_id},
    )
    bind.execute(
        sa.text(
            """
            UPDATE jdr_pjs
            SET campaign_id = :campaign_id
            WHERE campaign_id IS NULL
            """
        ),
        {"campaign_id": default_id},
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_jdr_pjs_campaign_id"), table_name="jdr_pjs")
    with op.batch_alter_table("jdr_pjs") as batch:
        batch.drop_constraint(
            op.f("fk_jdr_pjs_campaign_id_campaigns"), type_="foreignkey"
        )
        batch.drop_column("campaign_id")

    op.drop_index(op.f("ix_jdr_sessions_campaign_id"), table_name="jdr_sessions")
    with op.batch_alter_table("jdr_sessions") as batch:
        batch.drop_constraint(
            op.f("fk_jdr_sessions_campaign_id_campaigns"), type_="foreignkey"
        )
        batch.drop_column("campaign_id")

    op.drop_index(
        op.f("ix_core_users_default_campaign_id"), table_name="core_users"
    )
    with op.batch_alter_table("core_users") as batch:
        batch.drop_constraint(
            op.f("fk_core_users_default_campaign_id_campaigns"), type_="foreignkey"
        )
        batch.drop_column("default_campaign_id")

    op.drop_index(
        op.f("ix_campaign_members_character_id"), table_name="campaign_members"
    )
    op.drop_table("campaign_members")
    op.drop_index(op.f("ix_campaigns_owner_id"), table_name="campaigns")
    op.drop_table("campaigns")
