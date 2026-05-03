"""Demo jobs to validate the async machinery (ADR 0004).

Intentionally trivial. The first real jobs land at Jalon 4 (DeepInfra)
or Jalon 5 (audio transcription). These exist so that the worker, the
queue, and the test suite have something concrete to exercise.
"""

import time


def add(a: int, b: int) -> int:
    """Pure function — directly testable, no Redis required."""
    return a + b


def simulate_long_task(seconds: float) -> str:
    """Sleep for ``seconds`` then return a confirmation string.

    Used for end-to-end checks ("is the worker actually consuming jobs?").
    Avoid in unit tests; call ``add`` instead.
    """
    time.sleep(seconds)
    return f"slept {seconds}s"
