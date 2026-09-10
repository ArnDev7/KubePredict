from pydantic import BaseModel, Field


class WorkRequest(BaseModel):
    cpu_iterations: int = Field(default=100000, ge=0)
    delay_ms: int = Field(default=0, ge=0)
    payload_size: int = Field(default=0, ge=0, le=10000)
    fail: bool = Field(default=False)

    model_config = {"extra": "forbid", "strict": True}


class WorkResponse(BaseModel):
    request_id: str
    status: str
    cpu_iterations_completed: int
    delay_ms_applied: int
    payload_size: int
    checksum: int
    duration_ms: float
