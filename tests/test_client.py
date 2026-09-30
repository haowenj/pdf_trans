import hashlib
import io
import json
import zipfile

import httpx
import pytest

from pdf_trans.client import (
    DEFAULT_MINERU_BACKEND,
    MinerUClient,
    resolve_mineru_backend_config,
)
from pdf_trans.errors import MinerUClientError, MinerUConfigError


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
        "image_analysis": "false",
        "return_md": "false",
        "return_middle_json": "false",
        "return_model_output": "false",
        "return_content_list": "true",
        "return_images": "true",
        "response_format_zip": "true",
    }.items():
        assert f'name="{field}"'.encode() in seen_post_body
        assert value.encode() in seen_post_body
    assert b'name="server_url"' not in seen_post_body


@pytest.mark.parametrize("missing_status", [404, 405])
def test_parse_pdf_uses_v1_when_tasks_endpoint_is_missing(tmp_path, missing_status):
    pdf = tmp_path / "contract.pdf"
    content = b"%PDF-1.7 example"
    pdf.write_bytes(content)
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("structured_content.json", b'{"pages": []}')
    archive_bytes = archive_buffer.getvalue()
    calls = []
    job_requests = []
    statuses = iter(["queued", "running", "completed"])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/tasks":
            return httpx.Response(missing_status)
        if path == "/v1/uploads":
            assert request.method == "POST"
            assert json.loads(request.content) == {
                "filename": "contract.pdf",
                "bytes": len(content),
                "mime_type": "application/pdf",
                "purpose": "parse",
                "sha256sum": hashlib.sha256(content).hexdigest(),
            }
            return httpx.Response(200, json={
                "id": "upload-1",
                "upload_url": "/v1/uploads/upload-1/content",
                "upload_headers": {"content-type": "application/octet-stream"},
            })
        if path == "/v1/uploads/upload-1/content":
            assert request.method == "PUT"
            assert request.content == content
            return httpx.Response(200)
        if path == "/v1/uploads/upload-1/complete":
            assert json.loads(request.content) == {
                "sha256sum": hashlib.sha256(content).hexdigest()
            }
            return httpx.Response(200, json={"file": {"id": "input-file"}})
        if path == "/v1/parse/jobs":
            job_requests.append(json.loads(request.content))
            return httpx.Response(202, json={"job_id": "job-1"})
        if path == "/v1/parse/jobs/job-1":
            status = next(statuses)
            return httpx.Response(200, json={
                "status": status,
                "files": [{"status": status, "output_files": {
                    "zip": {"file_id": "output-file"}
                }}],
            })
        if path == "/v1/files/output-file/content":
            return httpx.Response(200, content=archive_bytes,
                                  headers={"content-type": "application/octet-stream"})
        if path.startswith("/v1/files/") and request.method == "DELETE":
            return httpx.Response(200)
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = MinerUClient(
        svr_url="http://mineru.test/",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )

    assert client.parse_pdf(pdf) == archive_bytes
    assert job_requests == [{
        "files": [{"source": {"type": "file_id", "file_id": "input-file"}}],
        "tier": "standard",
        "ocr_mode": "auto",
        "output_formats": ["zip"],
    }]
    assert ("DELETE", "/v1/files/input-file") in calls
    assert ("DELETE", "/v1/files/output-file") in calls


def test_parse_pdf_does_not_fallback_after_tasks_server_error(tmp_path):
    pdf = tmp_path / "contract.pdf"
    pdf.write_bytes(b"%PDF")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(500, text="server unavailable")

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(MinerUClientError, match="HTTP 500"):
        client.parse_pdf(pdf)
    assert calls == ["/tasks"]


def test_submit_reports_missing_tasks_endpoint_as_client_error(tmp_path):
    pdf = tmp_path / "contract.pdf"
    pdf.write_bytes(b"%PDF")
    client = MinerUClient(http_client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(404)
    )))

    with pytest.raises(MinerUClientError, match="HTTP 404"):
        client.submit(pdf)


def test_submit_uses_remote_hybrid_backend_and_server_url(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    seen_post_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_post_body
        seen_post_body = request.read()
        return httpx.Response(
            202,
            json={
                "task_id": "remote-1",
                "status_url": "http://test/tasks/remote-1",
                "result_url": "http://test/tasks/remote-1/result",
            },
        )

    client = MinerUClient(
        backend="hybrid-http-client",
        server_url="http://gpustack:8000/",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.submit(pdf)

    assert b'name="backend"' in seen_post_body
    assert b"hybrid-http-client" in seen_post_body
    assert b'name="server_url"' in seen_post_body
    assert b"http://gpustack:8000" in seen_post_body
    assert b'name="image_analysis"' in seen_post_body
    assert b"false" in seen_post_body


def test_resolve_backend_defaults_to_local_and_ignores_server_url():
    config = resolve_mineru_backend_config(
        DEFAULT_MINERU_BACKEND,
        "http://unused.example/",
    )

    assert config.backend == "hybrid-engine"
    assert config.server_url is None


@pytest.mark.parametrize(
    ("backend", "server_url", "message"),
    [
        ("pipeline", None, "PDF_TRANS_MINERU_BACKEND"),
        ("hybrid-http-client", None, "PDF_TRANS_MINERU_SERVER_URL"),
        ("hybrid-http-client", "  ", "PDF_TRANS_MINERU_SERVER_URL"),
    ],
)
def test_resolve_backend_rejects_invalid_configuration(
    backend, server_url, message
):
    with pytest.raises(MinerUConfigError, match=message):
        resolve_mineru_backend_config(backend, server_url)


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
