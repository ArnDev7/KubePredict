import pytest
from pydantic import ValidationError

from app.schemas import WorkRequest, WorkResponse


def test_work_request_defaults():
    req = WorkRequest()
    assert req.cpu_iterations == 100000
    assert req.delay_ms == 0
    assert req.payload_size == 0
    assert req.fail is False


def test_work_request_validation():
    with pytest.raises(ValidationError):
        WorkRequest(cpu_iterations=-1)

    with pytest.raises(ValidationError):
        WorkRequest(delay_ms=-1)

    with pytest.raises(ValidationError):
        WorkRequest(payload_size=-1)

    with pytest.raises(ValidationError):
        WorkRequest(payload_size=10001)

    # Check strict bool
    with pytest.raises(ValidationError):
        WorkRequest(fail=1)

    # Check unknown fields rejected
    with pytest.raises(ValidationError):
        WorkRequest(unknown_field=123)


def test_work_response_schema():
    resp = WorkResponse(
        request_id="abc",
        status="completed",
        cpu_iterations_completed=100,
        delay_ms_applied=10,
        payload_size=100,
        checksum=12345,
        duration_ms=15.5,
    )
    assert resp.request_id == "abc"
