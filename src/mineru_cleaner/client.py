from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from mineru_cleaner.errors import MinerUClientError

DEFAULT_SVR_URL = "http://127.0.0.1:7100"
POLL_INTERVAL_SECONDS = 2.0
TASK_TIMEOUT_SECONDS = 30 * 60

PARSE_FORM = {
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
}


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
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._svr_url = svr_url.rstrip("/")
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
        submission = self.submit(pdf_path)
        self.wait_for_completion(submission)
        return self.download_result(submission)

    def submit(self, pdf_path: Path) -> TaskSubmission:
        try:
            with pdf_path.open("rb") as pdf_file:
                response = self._http.post(
                    f"{self._svr_url}/tasks",
                    data=PARSE_FORM,
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
