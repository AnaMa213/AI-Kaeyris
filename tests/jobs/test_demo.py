"""Direct calls — no Redis needed."""

from app.jobs.demo import add


def test_add_returns_sum():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_add_propagates_type_error():
    # add doesn't validate types on purpose — Python's natural behavior.
    # Document the contract: callers must pass numeric values.
    import pytest

    with pytest.raises(TypeError):
        add("a", 1)  # type: ignore[arg-type]
