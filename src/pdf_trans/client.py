from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from zipfile import is_zipfile

import httpx

from pdf_trans.errors import MinerUClientError, MinerUConfigError

DEFAULT_SVR_URL = "http://127.0.0.1:7100"
DEFAULT_MINERU_BACKEND = "hybrid-engine"
REMOTE_MINERU_BACKEND = "hybrid-http-client"
SUPPORTED_MINERU_BACKENDS = (
    DEFAULT_MINERU_BACKEND,
    REMOTE_MINERU_BACKEND,
)
POLL_INTERVAL_SECONDS = 2.0
TASK_TIMEOUT_SECONDS = 30 * 60
LOGGER = logging.getLogger(__name__)


class _LegacyEndpointUnavailable(Exception):
    """The server does not expose the MinerU 3.x /tasks endpoint."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

BASE_PARSE_FORM = MappingProxyType(
    {
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
    }
)


@dataclass(frozen=True)
class MinerUBackendConfig:
    backend: str
    server_url: str | None


def resolve_mineru_backend_config(
    backend: str = DEFAULT_MINERU_BACKEND,
    server_url: str | None = None,
) -> MinerUBackendConfig:
    if backend not in SUPPORTED_MINERU_BACKENDS:
        allowed = ", ".join(SUPPORTED_MINERU_BACKENDS)
        raise MinerUConfigError(
            "PDF_TRANS_MINERU_BACKEND "
            f"必须是以下值之一：{allowed}；当前值：{backend!r}"
        )

    normalized_url = (server_url or "").strip().rstrip("/")
    if backend == REMOTE_MINERU_BACKEND:
        if not normalized_url:
            raise MinerUConfigError(
                "PDF_TRANS_MINERU_SERVER_URL 在 "
                "hybrid-http-client 模式下不能为空"
            )
        return MinerUBackendConfig(backend, normalized_url)
    return MinerUBackendConfig(backend, None)


@dataclass(frozen=True)
class TaskSubmission:
    task_id: str
    status_url: str
    result_url: str


class MinerUClient:
    def __init__(
        self,
        *,
        svr_url: str = DEFAULT_SVR_URL,
        backend: str = DEFAULT_MINERU_BACKEND,
        server_url: str | None = None,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._svr_url = svr_url.rstrip("/")
        self._backend_config = resolve_mineru_backend_config(
            backend,
            server_url,
        )
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0),
            follow_redirects=True,
        )
        self._sleep = sleep
        self._clock = clock

    def __enter__(self) -> "MinerUClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def parse_pdf(self, pdf_path: Path) -> bytes:
        try:
            submission = self._submit_legacy(pdf_path)
        except _LegacyEndpointUnavailable:
            return self._parse_v1_pdf(pdf_path)
        self.wait_for_completion(submission)
        return self.download_result(submission)

    def _parse_form(self) -> dict[str, str]:
        form = dict(BASE_PARSE_FORM)
        form["backend"] = self._backend_config.backend
        if self._backend_config.server_url is not None:
            form["server_url"] = self._backend_config.server_url
        return form

    def submit(self, pdf_path: Path) -> TaskSubmission:
        try:
            return self._submit_legacy(pdf_path)
        except _LegacyEndpointUnavailable as exc:
            raise MinerUClientError(
                f"提交 MinerU 任务失败：HTTP {exc.status_code}"
            ) from exc

    def _submit_legacy(self, pdf_path: Path) -> TaskSubmission:
        try:
            with pdf_path.open("rb") as pdf_file:
                response = self._http.post(
                    f"{self._svr_url}/tasks",
                    data=self._parse_form(),
                    files={
                        "files": (
                            pdf_path.name,
                            pdf_file,
                            "application/pdf",
                        )
                    },
                )
        except (OSError, httpx.HTTPError) as exc:
            raise MinerUClientError(f"提交 MinerU 任务失败：{exc}") from exc

        if response.status_code in {404, 405}:
            raise _LegacyEndpointUnavailable(response.status_code)
        if response.status_code != 202:
            raise MinerUClientError(
                f"提交 MinerU 任务失败：HTTP {response.status_code} {response.text}"
            )
        payload = self._json_object(response, "任务提交")
        values = [payload.get(key) for key in ("task_id", "status_url", "result_url")]
        if not all(isinstance(value, str) and value for value in values):
            raise MinerUClientError("MinerU 返回了无效任务响应")
        return TaskSubmission(
            task_id=values[0],
            status_url=values[1],
            result_url=values[2],
        )

    def wait_for_completion(self, submission: TaskSubmission) -> None:
        deadline = self._clock() + TASK_TIMEOUT_SECONDS
        while self._clock() < deadline:
            try:
                response = self._http.get(submission.status_url)
            except httpx.HTTPError as exc:
                raise MinerUClientError(f"查询 MinerU 任务失败：{exc}") from exc
            if response.status_code != 200:
                raise MinerUClientError(
                    f"查询 MinerU 任务失败：HTTP {response.status_code} {response.text}"
                )

            payload = self._json_object(response, "任务状态")
            status = payload.get("status")
            if status in {"pending", "processing"}:
                self._sleep(POLL_INTERVAL_SECONDS)
                continue
            if status == "completed":
                return
            if status == "failed":
                detail = payload.get("error") or json.dumps(payload, ensure_ascii=False)
                raise MinerUClientError(f"MinerU 任务失败：{detail}")
            raise MinerUClientError(f"MinerU 返回未知任务状态：{status!r}")

        raise MinerUClientError(
            f"等待任务超时：{submission.task_id}，超过 {TASK_TIMEOUT_SECONDS} 秒"
        )

    def download_result(self, submission: TaskSubmission) -> bytes:
        try:
            response = self._http.get(submission.result_url)
        except httpx.HTTPError as exc:
            raise MinerUClientError(f"下载 MinerU 结果失败：{exc}") from exc
        if response.status_code != 200:
            raise MinerUClientError(
                f"下载 MinerU 结果失败：HTTP {response.status_code} {response.text}"
            )
        content_type = response.headers.get("content-type", "")
        if "application/zip" not in content_type.lower():
            raise MinerUClientError(
                f"MinerU 结果不是 ZIP：content-type={content_type or 'unknown'}"
            )
        return response.content

    @staticmethod
    def _json_object(response: httpx.Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise MinerUClientError(f"MinerU {label}响应不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise MinerUClientError(f"MinerU {label}响应必须是 JSON 对象")
        return payload

    def _v1_json_request(
        self, method: str, url: str, label: str, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            response = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise MinerUClientError(f"MinerU v1 {label}失败：{exc}") from exc
        if response.status_code not in {200, 202}:
            raise MinerUClientError(
                f"MinerU v1 {label}失败：HTTP {response.status_code}"
            )
        return self._json_object(response, f"v1 {label}")

    def _parse_v1_pdf(self, pdf_path: Path) -> bytes:
        base_url = self._svr_url
        input_file_id: str | None = None
        output_file_id: str | None = None
        remove_input_file = False
        try:
            try:
                content = pdf_path.read_bytes()
            except OSError as exc:
                raise MinerUClientError(f"读取 MinerU 输入文件失败：{exc}") from exc
            digest = hashlib.sha256(content).hexdigest()
            upload = self._v1_json_request(
                "POST",
                f"{base_url}/v1/uploads",
                "创建上传",
                json={
                    "filename": pdf_path.name,
                    "bytes": len(content),
                    "mime_type": "application/pdf",
                    "purpose": "parse",
                    "sha256sum": digest,
                },
            )
            upload_id = upload.get("id")
            if not isinstance(upload_id, str) or not upload_id:
                raise MinerUClientError("MinerU v1 返回了无效 upload id")

            file_payload = upload.get("file")
            if upload.get("status") != "completed" or not isinstance(
                file_payload, dict
            ):
                upload_url = upload.get("upload_url")
                if not isinstance(upload_url, str) or not upload_url:
                    upload_url = f"{base_url}/v1/uploads/{upload_id}/content"
                elif upload_url.startswith("/"):
                    upload_url = f"{base_url}{upload_url}"
                headers = upload.get("upload_headers")
                if not isinstance(headers, dict):
                    headers = {"content-type": "application/octet-stream"}
                try:
                    response = self._http.put(
                        upload_url,
                        headers={str(key): str(value) for key, value in headers.items()},
                        content=content,
                    )
                except httpx.HTTPError as exc:
                    raise MinerUClientError(f"MinerU v1 上传内容失败：{exc}") from exc
                if response.status_code != 200:
                    raise MinerUClientError(
                        f"MinerU v1 上传内容失败：HTTP {response.status_code}"
                    )
                completed = self._v1_json_request(
                    "POST",
                    f"{base_url}/v1/uploads/{upload_id}/complete",
                    "完成上传",
                    json={"sha256sum": digest},
                )
                file_payload = completed.get("file")
                remove_input_file = True

            input_file_id = (
                file_payload.get("id") if isinstance(file_payload, dict) else None
            )
            if not isinstance(input_file_id, str) or not input_file_id:
                raise MinerUClientError("MinerU v1 返回了无效 input file id")

            job = self._v1_json_request(
                "POST",
                f"{base_url}/v1/parse/jobs",
                "创建解析任务",
                json={
                    "files": [{"source": {"type": "file_id", "file_id": input_file_id}}],
                    "tier": "basic" if self._backend_config.backend == "pipeline" else "standard",
                    "ocr_mode": "auto",
                    "output_formats": ["zip"],
                },
            )
            job_id = job.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise MinerUClientError("MinerU v1 返回了无效 job id")

            deadline = self._clock() + TASK_TIMEOUT_SECONDS
            while self._clock() < deadline:
                job = self._v1_json_request(
                    "GET",
                    f"{base_url}/v1/parse/jobs/{job_id}",
                    "查询解析任务",
                )
                status = job.get("status")
                if status in {"queued", "running"}:
                    self._sleep(POLL_INTERVAL_SECONDS)
                    continue
                if status in {"completed", "partial"}:
                    break
                raise MinerUClientError(f"MinerU v1 解析任务未完成：{status}")
            else:
                raise MinerUClientError("等待 MinerU v1 解析任务超时")

            files = job.get("files")
            first_file = files[0] if isinstance(files, list) and files else None
            output_files = (
                first_file.get("output_files") if isinstance(first_file, dict) else None
            )
            zip_ref = output_files.get("zip") if isinstance(output_files, dict) else None
            output_file_id = zip_ref.get("file_id") if isinstance(zip_ref, dict) else None
            if not isinstance(output_file_id, str) or not output_file_id:
                raise MinerUClientError("MinerU v1 结果缺少 ZIP")

            try:
                response = self._http.get(
                    f"{base_url}/v1/files/{output_file_id}/content"
                )
            except httpx.HTTPError as exc:
                raise MinerUClientError(f"下载 MinerU v1 结果失败：{exc}") from exc
            if response.status_code != 200:
                raise MinerUClientError(
                    f"下载 MinerU v1 结果失败：HTTP {response.status_code}"
                )
            if not is_zipfile(io.BytesIO(response.content)):
                raise MinerUClientError("MinerU v1 结果不是 ZIP")
            return response.content
        finally:
            cleanup_ids = [output_file_id]
            if remove_input_file:
                cleanup_ids.append(input_file_id)
            for file_id in cleanup_ids:
                if not file_id:
                    continue
                try:
                    self._http.delete(f"{base_url}/v1/files/{file_id}")
                except httpx.HTTPError:
                    LOGGER.warning("无法清理临时 MinerU 文件")
