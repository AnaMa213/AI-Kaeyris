"""Tests the enqueue policy via fakeredis (no real Redis needed)."""

import fakeredis

from app.jobs import (
    DEFAULT_FAILURE_TTL_SECONDS,
    DEFAULT_QUEUE_NAME,
    DEFAULT_RESULT_TTL_SECONDS,
    enqueue_job,
    get_default_queue,
)
from app.jobs.demo import add


def _redis() -> fakeredis.FakeStrictRedis:
    return fakeredis.FakeStrictRedis()


def test_get_default_queue_uses_expected_name():
    queue = get_default_queue(_redis())
    assert queue.name == DEFAULT_QUEUE_NAME


def test_enqueue_job_applies_default_ttls():
    queue = get_default_queue(_redis())
    job = enqueue_job(queue, add, 2, 3)

    assert job.result_ttl == DEFAULT_RESULT_TTL_SECONDS
    assert job.failure_ttl == DEFAULT_FAILURE_TTL_SECONDS
    assert job.args == (2, 3)


def test_enqueue_job_with_transient_errors_sets_retry():
    queue = get_default_queue(_redis())
    job = enqueue_job(queue, add, 1, 2, transient_errors=True)
    # RQ stores the remaining retries on the job itself.
    assert job.retries_left == 3
    assert job.retry_intervals == [10, 30, 90]


def test_enqueue_job_without_transient_errors_disables_retry():
    queue = get_default_queue(_redis())
    job = enqueue_job(queue, add, 1, 2, transient_errors=False)
    assert job.retries_left is None


def test_enqueue_job_applies_default_job_timeout():
    """RQ default (180 s) is too short for transcription; we override
    enqueue-wide so all jobs share a sensible cap."""
    from app.jobs import DEFAULT_JOB_TIMEOUT_SECONDS

    queue = get_default_queue(_redis())
    job = enqueue_job(queue, add, 1, 2)
    assert job.timeout == DEFAULT_JOB_TIMEOUT_SECONDS


def test_enqueue_job_allows_per_call_job_timeout():
    queue = get_default_queue(_redis())
    job = enqueue_job(queue, add, 1, 2, job_timeout=60)
    assert job.timeout == 60
