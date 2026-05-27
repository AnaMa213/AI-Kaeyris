"""First-run setup helper tests."""

import pytest

from app.core.users import SetupClosedError, create_first_gm


async def test_create_first_gm_rechecks_empty_users_table(db_session):
    first = await create_first_gm(
        db_session,
        username="admin",
        password="chosen-password",
    )

    with pytest.raises(SetupClosedError):
        await create_first_gm(
            db_session,
            username="other",
            password="chosen-password",
        )

    assert first.username == "admin"
