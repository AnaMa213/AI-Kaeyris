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
# Per-job timeout: RQ default is 180 s, which is way too short for our
# transcription work (model load + audio decode + inference on long
# files can easily exceed several minutes). 30 minutes matches the
# upstream adapter timeout (TRANSCRIPTION_TIMEOUT_SECONDS=1800) so the
# adapter raises its own clean error before RQ kills the worker.
DEFAULT_JOB_TIMEOUT_SECONDS = 30 * 60


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
    job_timeout: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> Job:
    """Enqueue ``func(*args, **kwargs)`` with the standard policy.

    ``transient_errors=True`` (default): up to 3 retries with backoff
    ``[10s, 30s, 90s]`` on uncaught exceptions.
    ``transient_errors=False``: no retry — for jobs whose failures are
    always permanent.

    ``job_timeout`` (seconds) caps how long the worker will let the job
    run before raising ``JobTimeoutException``. The default
    (``DEFAULT_JOB_TIMEOUT_SECONDS``) is generous enough for our heaviest
    job (long-audio transcription) — short jobs simply ignore it.
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
        job_timeout=job_timeout,
        **kwargs,
    )
