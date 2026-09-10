from app.workload import perform_cpu_work, perform_delay, perform_payload_work


def test_perform_cpu_work():
    assert perform_cpu_work(0) == 0
    assert perform_cpu_work(5) > 0


import asyncio


def test_perform_delay():
    asyncio.run(perform_delay(0))
    asyncio.run(perform_delay(1))


def test_perform_payload_work():
    assert perform_payload_work(0) == 0
    assert perform_payload_work(10000) == 10000
