"""Regression for the ``jdr_jobs`` enum storage (BD fix).

``jdr_jobs.kind`` was a *native* Postgres ENUM created without the SUMMARY
label, so persisting a summary job raised
``invalid input value for enum jdr_job_kind: "SUMMARY"`` on Postgres (SQLite
accepted it silently). The column is now a non-native VARCHAR enum that stores
the lowercase ``.value`` — these tests lock in that mapping so every JobKind
round-trips and the raw value is the lowercase form, not the member name.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from app.services.jdr.db.models import Job, JobKind, JobStatus


async def test_job_kind_summary_persists_as_lowercase_value(db_session):
    job = Job(
        id=f"job-{uuid4().hex[:8]}",
        kind=JobKind.SUMMARY,
        session_id=uuid4(),
        status=JobStatus.QUEUED,
        queued_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    raw_kind = await db_session.scalar(
        text("SELECT kind FROM jdr_jobs WHERE id = :id"), {"id": job.id}
    )
    raw_status = await db_session.scalar(
        text("SELECT status FROM jdr_jobs WHERE id = :id"), {"id": job.id}
    )
    # The stored value is the lowercase enum *value*, not the member name.
    assert raw_kind == "summary"
    assert raw_status == "queued"

    db_session.expunge_all()
    fetched = await db_session.get(Job, job.id)
    assert fetched is not None
    assert fetched.kind is JobKind.SUMMARY
    assert fetched.status is JobStatus.QUEUED


async def test_all_job_kinds_round_trip(db_session):
    ids: dict[str, JobKind] = {}
    for kind in JobKind:
        job_id = f"job-{kind.value}-{uuid4().hex[:6]}"
        ids[job_id] = kind
        db_session.add(
            Job(
                id=job_id,
                kind=kind,
                session_id=uuid4(),
                status=JobStatus.QUEUED,
                queued_at=datetime.now(UTC),
            )
        )
    await db_session.flush()
    db_session.expunge_all()

    for job_id, expected in ids.items():
        fetched = await db_session.get(Job, job_id)
        assert fetched is not None
        assert fetched.kind is expected
