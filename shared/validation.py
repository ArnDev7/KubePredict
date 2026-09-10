from datetime import datetime


def require_timezone_aware(value: datetime) -> datetime:
    """Ensure the provided datetime object is timezone-aware."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("datetime must be timezone-aware")
    return value


def require_non_empty_string(value: str) -> str:
    """Ensure the provided string is not empty or entirely whitespace."""
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    if not value.strip():
        raise ValueError("string must not be empty or whitespace only")
    return value


def require_ratio(value: float) -> float:
    """Ensure the float is between 0.0 and 1.0 inclusive."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("value must be a float")
    value_float = float(value)
    if value_float < 0.0 or value_float > 1.0:
        raise ValueError("ratio must be between 0.0 and 1.0")
    return value_float


def require_positive_integer(value: int) -> int:
    """Ensure the integer is strictly greater than 0."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("value must be an integer")
    if value <= 0:
        raise ValueError("integer must be greater than zero")
    return value


def validate_replica_bounds(minimum: int, maximum: int) -> tuple[int, int]:
    """Ensure replica bounds are valid (min > 0, min <= max)."""
    if not isinstance(minimum, int) or isinstance(minimum, bool):
        raise ValueError("minimum must be an integer")
    if not isinstance(maximum, int) or isinstance(maximum, bool):
        raise ValueError("maximum must be an integer")
    if minimum <= 0:
        raise ValueError("minimum replicas must be greater than zero")
    if minimum > maximum:
        raise ValueError("minimum replicas cannot exceed maximum replicas")
    return minimum, maximum
