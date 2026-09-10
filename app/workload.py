import asyncio


def perform_cpu_work(iterations: int) -> int:
    """Perform deterministic CPU work and return a checksum."""
    if iterations == 0:
        return 0

    checksum = 0
    for i in range(iterations):
        checksum = (checksum + i) % 1000000007
    return checksum


async def perform_delay(delay_ms: int) -> None:
    """Perform an async delay."""
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)


def perform_payload_work(payload_size: int) -> int:
    """Perform bounded deterministic work based on payload size."""
    if payload_size == 0:
        return 0
    # Perform bounded work that doesn't return or log large payload
    data = "A" * payload_size
    return len(data)
