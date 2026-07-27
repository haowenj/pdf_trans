import json

import pytest

from pdf_trans.web.streams import log_event_stream, task_event_stream


@pytest.mark.anyio
async def test_task_stream_sends_snapshot_then_stops(repository) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    disconnects = iter([False, True])

    async def is_disconnected():
        return next(disconnects)

    async def no_sleep(seconds):
        return None

    stream = task_event_stream(
        repository,
        is_disconnected=is_disconnected,
        sleep=no_sleep,
    )
    event = await anext(stream)

    assert event.startswith("event: tasks\n")
    payload = json.loads(event.split("data: ", 1)[1])
    assert payload[0]["id"] == "a"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.anyio
async def test_log_stream_resumes_after_last_id(repository) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    first = repository.append_log("a", "INFO", "first")
    second = repository.append_log("a", "WARN", "second")

    async def connected():
        return False

    async def no_sleep(seconds):
        return None

    stream = log_event_stream(
        repository,
        task_id="a",
        after_id=first.id,
        is_disconnected=connected,
        sleep=no_sleep,
    )

    event = await anext(stream)

    assert f"id: {second.id}\n" in event
    assert '"message": "second"' in event
