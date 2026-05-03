"""Async job machinery.

ADR 0004. A job is a plain Python function with serializable arguments.
Use ``enqueue_job`` from this module to apply the project's standard
TTL and retry policy.

Jobs are collected under ``app/jobs/<topic>.py`` and never imported by
``app/services/`` (the service layer enqueues; the job layer executes).
"""

from typing import Any

from redis import Redis
from rq import Queue, Retry
from rq.job import Job

DEFAULT_QUEUE_NAME = "default"
DEFAULT_RESULT_TTL_SECONDS = 24 * 3600
DEFAULT_FAILURE_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_RETRY_INTERVAL_SECONDS = (10, 30, 90)
DEFAULT_RETRY_MAX = 3


class TransientJobError(Exception):
    """Raised by a job to signal a retryable failure (network, timeout, 5xx)."""


class PermanentJobError(Exception):
    """Raised by a job to signal a definitive failure (bad input, programming error)."""


def get_default_queue(redis_client: Redis) -> Queue:
    """Return the project's default RQ queue bound to ``redis_client``."""
    return Queue(DEFAULT_QUEUE_NAME, connection=redis_client)


def enqueue_job(
    queue: Queue,
    func: Any,
    *args: Any,
    transient_errors: bool = True,
    **kwargs: Any,
) -> Job:
    """Enqueue ``func(*args, **kwargs)`` with the standard policy.

    ``transient_errors=True`` (default): up to 3 retries with backoff
    ``[10s, 30s, 90s]`` on uncaught exceptions.

    ``transient_errors=False``: no retry — for jobs whose failures are
    always permanent.
    """
    retry = (
        Retry(max=DEFAULT_RETRY_MAX, interval=list(DEFAULT_RETRY_INTERVAL_SECONDS))
        if transient_errors
        else None
    )
    return queue.enqueue(
        func,
        *args,
        result_ttl=DEFAULT_RESULT_TTL_SECONDS,
        failure_ttl=DEFAULT_FAILURE_TTL_SECONDS,
        retry=retry,
        **kwargs,
    )
