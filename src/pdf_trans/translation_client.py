from __future__ import annotations

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


class OpenAICompatibleTranslator:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=60.0)

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
        return cls(
            base_url=values["TRANSLATION_BASE_URL"],
            api_key=values["TRANSLATION_API_KEY"],
            model=values["TRANSLATION_MODEL"],
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
