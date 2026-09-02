from __future__ import annotations

import math
import os
from typing import Any

import httpx

from pdf_trans.errors import TranslationClientError, TranslationConfigError

TRANSLATION_SYSTEM_PROMPT = """你是一名化工工程学术论文翻译助手。请将英文化工学术内容准确翻译为简体中文。

总体要求：
- 完整翻译所有英文说明性内容；
- 不总结、不删减、不改写、不补充；
- 保持原文的信息顺序、逻辑关系、数值和技术含义；
- 只输出译文，也只返回译文；如果请求要求返回结构化 JSON，则只输出符合要求的 JSON，不输出解释。

不可变内容：
- 公式占位符是不可翻译的原子字符串，必须逐字保留；
- 每个公式占位符只能出现一次；
- 不得删除、复制、翻译、拆开或篡改公式占位符；不得在占位符内部插入空格、换行、标点、文字或其他内容；
- 占位符可以根据中文语序调整位置，不要求保持原文中的先后位置；
- 保留引用编号，例如 [38]、[39–41]；
- 保留数字、单位、百分数、化学式、设备编号、控制回路编号、产品型号和必要的专有名词，例如 OG-200、JH-100、FIC0041、C-1、SS1；
- 保留 HTML、LaTeX 和其他结构化标记的结构；输入中可能包含 $...$ 形式的数学内容。

翻译边界：
- 除上述明确不可变内容外，输入中的英文自然语言内容都必须翻译成中文；
- 包含公式占位符的句子也必须翻译占位符周围的英文内容，占位符本身保持不变；
- 技术术语应结合化工论文语境翻译；不要因为术语邻近公式、设备编号或专有名词而保留整句英文；
- 只有明确属于设备编号、型号、控制回路编号、化学式、引用编号、单位或必要专有名词的内容才可以保留原文；
- 不要把普通技术术语默认当作不可翻译内容，也不要按脱离上下文的普通词义误译固定化工术语；
- 不要原样复制包含英文说明性内容的完整句子。"""

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


def _parse_optional_boolean(
    name: str,
    value: str,
) -> bool | None:
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise TranslationConfigError(
        f"{name} 只能是 true、false 或不配置"
    )


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
        enable_thinking: bool | None = None,
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
        if (
            enable_thinking is not None
            and type(enable_thinking) is not bool
        ):
            raise TranslationConfigError(
                "TRANSLATION_ENABLE_THINKING "
                "只能是 true、false 或不配置"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.concurrency = concurrency
        self.enable_thinking = enable_thinking
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
        enable_thinking = _parse_optional_boolean(
            "TRANSLATION_ENABLE_THINKING",
            os.environ.get("TRANSLATION_ENABLE_THINKING", ""),
        )
        return cls(
            base_url=values["TRANSLATION_BASE_URL"],
            api_key=values["TRANSLATION_API_KEY"],
            model=values["TRANSLATION_MODEL"],
            timeout_seconds=timeout,
            max_retries=max_retries,
            concurrency=concurrency,
            enable_thinking=enable_thinking,
        )

    def __enter__(self) -> "OpenAICompatibleTranslator":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def translate(
        self,
        text: str,
        *,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        return self._translate(text, response_format=response_format)

    def translate_with_instruction(
        self,
        text: str,
        instruction: str,
    ) -> str:
        return self._translate(text, instruction=instruction)

    def _translate(
        self,
        text: str,
        *,
        response_format: dict[str, Any] | None = None,
        instruction: str | None = None,
    ) -> str:
        system_prompt = TRANSLATION_SYSTEM_PROMPT
        if instruction:
            system_prompt = f"{system_prompt}\n\n{instruction}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        }
        if self.enable_thinking is not None:
            payload["chat_template_kwargs"] = {
                "enable_thinking": self.enable_thinking,
            }
        if response_format is not None:
            payload["response_format"] = response_format

        try:
            response = self._http.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise TranslationClientError(f"翻译请求失败：{exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise TranslationClientError("翻译响应缺少非空译文")
        return content
