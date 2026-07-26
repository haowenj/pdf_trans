import httpx
import pytest

from mineru_cleaner.client import MinerUClient
from mineru_cleaner.errors import MinerUClientError


def test_parse_pdf_submits_polls_and_downloads_zip(tmp_path):
    pdf = tmp_path / "示例.pdf"
    pdf.write_bytes(b"%PDF-1.7 sample")
    statuses = iter(["pending", "processing", "completed"])
    seen_post_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_post_body
        if request.method == "POST" and request.url.path == "/tasks":
            assert str(request.url).startswith("http://mineru.example:7200/")
            seen_post_body = request.read()
            return httpx.Response(
                202,
                json={
                    "task_id": "task-1",
                    "status_url": "http://127.0.0.1:7100/tasks/task-1",
                    "result_url": "http://127.0.0.1:7100/tasks/task-1/result",
                },
            )
        if request.url.path == "/tasks/task-1":
            return httpx.Response(200, json={"status": next(statuses)})
        if request.url.path == "/tasks/task-1/result":
            return httpx.Response(
                200,
                content=b"PK fake zip",
                headers={"content-type": "application/zip"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = MinerUClient(
        svr_url="http://mineru.example:7200/",
        http_client=http_client,
        sleep=lambda _: None,
    )

    result = client.parse_pdf(pdf)

    assert result == b"PK fake zip"
    assert b'name="files"; filename="' in seen_post_body
    assert "示例.pdf".encode() in seen_post_body
    for field, value in {
        "backend": "hybrid-engine",
        "parse_method": "auto",
        "effort": "medium",
        "formula_enable": "true",
        "table_enable": "true",
        "return_md": "false",
        "return_middle_json": "false",
        "return_model_output": "false",
        "return_content_list": "true",
        "return_images": "true",
        "response_format_zip": "true",
    }.items():
        assert f'name="{field}"'.encode() in seen_post_body
        assert value.encode() in seen_post_body


def test_parse_pdf_reports_failed_task(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "task_id": "task-2",
                    "status_url": "http://test/tasks/task-2",
                    "result_url": "http://test/tasks/task-2/result",
                },
            )
        return httpx.Response(
            200,
            json={"status": "failed", "error": "模型执行失败"},
        )

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )

    with pytest.raises(MinerUClientError, match="模型执行失败"):
        client.parse_pdf(pdf)


def test_wait_for_completion_times_out_without_polling(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")
    clock_values = iter([0.0, 1801.0])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "task_id": "task-3",
                    "status_url": "http://test/tasks/task-3",
                    "result_url": "http://test/tasks/task-3/result",
                },
            )
        raise AssertionError("deadline expired before status request")

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
        clock=lambda: next(clock_values),
    )

    with pytest.raises(MinerUClientError, match="等待任务超时"):
        client.parse_pdf(pdf)


def test_submit_rejects_malformed_task_response(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(202, json={"task_id": "missing-urls"})
    )
    client = MinerUClient(http_client=httpx.Client(transport=transport))

    with pytest.raises(MinerUClientError, match="无效任务响应"):
        client.parse_pdf(pdf)


def test_wait_for_completion_rejects_unknown_status(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "task_id": "task-4",
                    "status_url": "http://test/tasks/task-4",
                    "result_url": "http://test/tasks/task-4/result",
                },
            )
        return httpx.Response(200, json={"status": "mystery"})

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(MinerUClientError, match="未知任务状态"):
        client.parse_pdf(pdf)


def test_download_rejects_non_zip_result(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "task_id": "task-5",
                    "status_url": "http://test/tasks/task-5",
                    "result_url": "http://test/tasks/task-5/result",
                },
            )
        if request.url.path == "/tasks/task-5":
            return httpx.Response(200, json={"status": "completed"})
        return httpx.Response(
            200,
            json={"detail": "not a zip"},
            headers={"content-type": "application/json"},
        )

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(MinerUClientError, match="结果不是 ZIP"):
        client.parse_pdf(pdf)
