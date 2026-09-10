from datetime import UTC, datetime

import pytest

from shared.validation import (
    require_non_empty_string,
    require_positive_integer,
    require_ratio,
    require_timezone_aware,
    validate_replica_bounds,
)


def test_require_timezone_aware():
    aware_dt = datetime.now(UTC)
    assert require_timezone_aware(aware_dt) == aware_dt

    naive_dt = datetime.now()
    with pytest.raises(ValueError, match="timezone-aware"):
        require_timezone_aware(naive_dt)


def test_require_non_empty_string():
    assert require_non_empty_string("valid") == "valid"

    with pytest.raises(ValueError, match="must be a string"):
        require_non_empty_string(123)  # type: ignore

    with pytest.raises(ValueError, match="empty or whitespace"):
        require_non_empty_string("   ")

    with pytest.raises(ValueError, match="empty or whitespace"):
        require_non_empty_string("")


def test_require_ratio():
    assert require_ratio(0.5) == 0.5
    assert require_ratio(0.0) == 0.0
    assert require_ratio(1.0) == 1.0
    assert require_ratio(1) == 1.0

    with pytest.raises(ValueError, match="must be a float"):
        require_ratio(True)  # type: ignore

    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        require_ratio(1.5)

    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        require_ratio(-0.1)


def test_require_positive_integer():
    assert require_positive_integer(5) == 5

    with pytest.raises(ValueError, match="must be an integer"):
        require_positive_integer(True)  # type: ignore

    with pytest.raises(ValueError, match="greater than zero"):
        require_positive_integer(0)

    with pytest.raises(ValueError, match="greater than zero"):
        require_positive_integer(-5)


def test_validate_replica_bounds():
    assert validate_replica_bounds(1, 10) == (1, 10)
    assert validate_replica_bounds(5, 5) == (5, 5)

    with pytest.raises(ValueError, match="minimum must be an integer"):
        validate_replica_bounds(True, 10)  # type: ignore

    with pytest.raises(ValueError, match="greater than zero"):
        validate_replica_bounds(0, 10)

    with pytest.raises(ValueError, match="cannot exceed maximum replicas"):
        validate_replica_bounds(10, 5)
