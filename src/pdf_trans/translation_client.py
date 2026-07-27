from __future__ import annotations

import math
import os
from typing import Any

import httpx

from pdf_trans.errors import TranslationClientError, TranslationConfigError

TRANSLATION_SYSTEM_PROMPT = """你是一名化工工程学术论文翻译助手。请将英文化工学术论文准确翻译为简体中文。

要求：
- 不总结、不改写、不补充；
- 保留引用编号，例如 [38]、[39–41]；
- 保留 LaTeX 公式及 $...$ 内容；
- 保留数值、单位和百分数；
- 保留设备编号，例如 C-1、E-1、D-1、SS1；
- 术语翻译应符合化工论文表达；
- 只返回译文，不返回解释或前缀。"""

DEFAULT_TRANSLATION_TIMEOUT_SECONDS = 120.0
DEFAULT_TRANSLATION_MAX_RETRIES = 1
DEFAULT_TRANSLATION_CONCURRENCY = 5


def _parse_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise TranslationConfigError(
            "TRANSLATION_TIMEOUT_SECONDS 必须是大于 0 的有限数字"
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise TranslationConfigError(
            "TRANSLATION_TIMEOUT_SECONDS 必须是大于 0 的有限数字"
        )
    return timeout


def _parse_retries(value: str) -> int:
    try:
        retries = int(value)
    except ValueError as exc:
        raise TranslationConfigError(
            "TRANSLATION_MAX_RETRIES 必须是大于等于 0 的整数"
        ) from exc
    if retries < 0 or str(retries) != value.strip():
        raise TranslationConfigError(
            "TRANSLATION_MAX_RETRIES 必须是大于等于 0 的整数"
        )
    return retries


def _parse_positive_integer(name: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise TranslationConfigError(
            f"{name} 必须是大于 0 的整数"
        ) from exc
    if parsed <= 0 or str(parsed) != value.strip():
        raise TranslationConfigError(f"{name} 必须是大于 0 的整数")
    return parsed


class OpenAICompatibleTranslator:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_TRANSLATION_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_TRANSLATION_MAX_RETRIES,
        concurrency: int = DEFAULT_TRANSLATION_CONCURRENCY,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise TranslationConfigError(
                "TRANSLATION_TIMEOUT_SECONDS 必须是大于 0 的有限数字"
            )
        if max_retries < 0:
            raise TranslationConfigError(
                "TRANSLATION_MAX_RETRIES 必须是大于等于 0 的整数"
            )
        if type(concurrency) is not int or concurrency <= 0:
            raise TranslationConfigError(
                "TRANSLATION_CONCURRENCY 必须是大于 0 的整数"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.concurrency = concurrency
        self._api_key = api_key
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout_seconds)

    @classmethod
    def from_env(cls) -> "OpenAICompatibleTranslator":
        names = (
            "TRANSLATION_BASE_URL",
            "TRANSLATION_API_KEY",
            "TRANSLATION_MODEL",
        )
        values = {name: os.environ.get(name, "").strip() for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise TranslationConfigError(
                "缺少翻译环境变量：" + ", ".join(missing)
            )
        timeout = _parse_timeout(
            os.environ.get(
                "TRANSLATION_TIMEOUT_SECONDS",
                str(DEFAULT_TRANSLATION_TIMEOUT_SECONDS),
            )
        )
        max_retries = _parse_retries(
            os.environ.get(
                "TRANSLATION_MAX_RETRIES",
                str(DEFAULT_TRANSLATION_MAX_RETRIES),
            )
        )
        concurrency = _parse_positive_integer(
            "TRANSLATION_CONCURRENCY",
            os.environ.get(
                "TRANSLATION_CONCURRENCY",
                str(DEFAULT_TRANSLATION_CONCURRENCY),
            ),
        )
        return cls(
            base_url=values["TRANSLATION_BASE_URL"],
            api_key=values["TRANSLATION_API_KEY"],
            model=values["TRANSLATION_MODEL"],
            timeout_seconds=timeout,
            max_retries=max_retries,
            concurrency=concurrency,
        )

    def __enter__(self) -> "OpenAICompatibleTranslator":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def translate(self, text: str) -> str:
        try:
            response = self._http.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise TranslationClientError(f"翻译请求失败：{exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise TranslationClientError("翻译响应缺少非空译文")
        return content
