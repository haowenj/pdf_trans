import hashlib
import io
import json
from zipfile import ZipFile

import httpx
import pytest

from pdf_trans.client import MinerUClient
from pdf_trans.errors import MinerUClientError


def result_zip() -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("structured_content.json", b'{"pages": []}')
    return buffer.getvalue()


def test_parse_pdf_calls_mineru_4_directly_with_model_and_key(tmp_path):
    pdf = tmp_path / "contract.pdf"
    content = b"%PDF-1.7 sample"
    pdf.write_bytes(content)
    archive_bytes = result_zip()
    calls = []
    statuses = iter(["queued", "running", "completed"])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.url.path.startswith("/proxy/v1/")
        assert request.headers["X-GPUStack-Model"] == "mineru4"
        assert request.headers["Authorization"] == "Bearer mineru-key"
        path = request.url.path
        if path == "/proxy/v1/uploads":
            assert json.loads(request.content) == {
                "filename": "contract.pdf",
                "bytes": len(content),
                "mime_type": "application/pdf",
                "purpose": "parse",
                "sha256sum": hashlib.sha256(content).hexdigest(),
            }
            return httpx.Response(200, json={
                "id": "upload-1",
                "upload_url": "http://wrong-host/v1/uploads/upload-1/content",
            })
        if path == "/proxy/v1/uploads/upload-1/content":
            assert request.method == "PUT"
            assert request.content == content
            return httpx.Response(200)
        if path == "/proxy/v1/uploads/upload-1/complete":
            assert json.loads(request.content) == {
                "sha256sum": hashlib.sha256(content).hexdigest()
            }
            return httpx.Response(200, json={"file": {"id": "input-1"}})
        if path == "/proxy/v1/parse/jobs":
            assert json.loads(request.content) == {
                "files": [{"source": {"type": "file_id", "file_id": "input-1"}}],
                "tier": "standard",
                "ocr_mode": "auto",
                "output_formats": ["zip"],
            }
            return httpx.Response(202, json={"job_id": "job-1"})
        if path == "/proxy/v1/parse/jobs/job-1":
            status = next(statuses)
            return httpx.Response(200, json={
                "status": status,
                "files": [{"output_files": {"zip": {"file_id": "output-1"}}}],
            })
        if path == "/proxy/v1/files/output-1/content":
            return httpx.Response(200, content=archive_bytes,
                                  headers={"content-type": "application/octet-stream"})
        if request.method == "DELETE" and path in {
            "/proxy/v1/files/input-1", "/proxy/v1/files/output-1"
        }:
            return httpx.Response(200)
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = MinerUClient(
        svr_url="http://mineru.test/proxy/",
        gpustack_model="mineru4",
        api_key="mineru-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )

    assert client.parse_pdf(pdf) == archive_bytes
    assert calls[0] == ("POST", "/proxy/v1/uploads")
    assert ("DELETE", "/proxy/v1/files/input-1") in calls
    assert ("DELETE", "/proxy/v1/files/output-1") in calls


def test_parse_pdf_uses_cached_upload_without_key(tmp_path):
    pdf = tmp_path / "cached.pdf"
    pdf.write_bytes(b"%PDF")
    archive_bytes = result_zip()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        assert request.headers["X-GPUStack-Model"] == "custom-model"
        assert "authorization" not in request.headers
        if path == "/v1/uploads":
            return httpx.Response(200, json={
                "id": "cached", "status": "completed", "file": {"id": "input-1"}
            })
        if path == "/v1/parse/jobs":
            return httpx.Response(202, json={"job_id": "job-1"})
        if path == "/v1/parse/jobs/job-1":
            return httpx.Response(200, json={
                "status": "completed",
                "files": [{"output_files": {"zip": {"file_id": "output-1"}}}],
            })
        if path == "/v1/files/output-1/content":
            return httpx.Response(200, content=archive_bytes)
        if request.method == "DELETE":
            return httpx.Response(200)
        raise AssertionError(path)

    client = MinerUClient(
        gpustack_model="custom-model",
        api_key="  ",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.parse_pdf(pdf) == archive_bytes
    assert ("PUT", "/v1/uploads/cached/content") not in calls
    assert ("DELETE", "/v1/files/input-1") not in calls
    assert ("DELETE", "/v1/files/output-1") in calls


def test_parse_pdf_reports_v1_upload_http_error(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(500)

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(MinerUClientError, match="HTTP 500"):
        client.parse_pdf(pdf)
    assert calls == ["/v1/uploads"]


def test_parse_pdf_reports_failed_job(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/uploads":
            return httpx.Response(200, json={
                "id": "cached", "status": "completed", "file": {"id": "input-1"}
            })
        if request.url.path == "/v1/parse/jobs":
            return httpx.Response(202, json={"job_id": "job-1"})
        return httpx.Response(200, json={"status": "failed"})

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(MinerUClientError, match="解析任务未完成：failed"):
        client.parse_pdf(pdf)


def test_parse_pdf_rejects_non_zip_v1_result(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/uploads":
            return httpx.Response(200, json={
                "id": "cached", "status": "completed", "file": {"id": "input-1"}
            })
        if request.url.path == "/v1/parse/jobs":
            return httpx.Response(202, json={"job_id": "job-1"})
        if request.url.path == "/v1/parse/jobs/job-1":
            return httpx.Response(200, json={
                "status": "completed",
                "files": [{"output_files": {"zip": {"file_id": "output-1"}}}],
            })
        if request.url.path == "/v1/files/output-1/content":
            return httpx.Response(200, content=b"not a zip")
        return httpx.Response(200)

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(MinerUClientError, match="结果不是 ZIP"):
        client.parse_pdf(pdf)
