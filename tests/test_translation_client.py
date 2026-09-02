import json

import httpx
import pytest

from pdf_trans.errors import TranslationClientError, TranslationConfigError
from pdf_trans.translation_client import (
    DEFAULT_TRANSLATION_CONCURRENCY,
    DEFAULT_TRANSLATION_MAX_RETRIES,
    DEFAULT_TRANSLATION_TIMEOUT_SECONDS,
    OpenAICompatibleTranslator,
    TRANSLATION_SYSTEM_PROMPT,
)


@pytest.fixture(autouse=True)
def clear_translation_thinking_environment(monkeypatch):
    monkeypatch.delenv("TRANSLATION_ENABLE_THINKING", raising=False)


def test_from_env_reads_required_translation_configuration(monkeypatch):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "http://translate.example/v1/")
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.base_url == "http://translate.example/v1"
    assert translator.model == "paper-model"
    assert translator.timeout_seconds == DEFAULT_TRANSLATION_TIMEOUT_SECONDS
    assert translator.max_retries == DEFAULT_TRANSLATION_MAX_RETRIES
    translator.close()


def test_from_env_uses_new_timeout_and_concurrency_defaults(monkeypatch):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "http://translate.example/v1")
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")
    monkeypatch.delenv("TRANSLATION_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TRANSLATION_MAX_RETRIES", raising=False)
    monkeypatch.delenv("TRANSLATION_CONCURRENCY", raising=False)

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.timeout_seconds == 120.0
    assert translator.timeout_seconds == DEFAULT_TRANSLATION_TIMEOUT_SECONDS
    assert translator.max_retries == DEFAULT_TRANSLATION_MAX_RETRIES
    assert translator.concurrency == DEFAULT_TRANSLATION_CONCURRENCY == 5
    translator.close()


def test_from_env_leaves_thinking_unconfigured_by_default(monkeypatch):
    monkeypatch.setenv(
        "TRANSLATION_BASE_URL",
        "http://translate.example/v1",
    )
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")
    monkeypatch.delenv("TRANSLATION_ENABLE_THINKING", raising=False)

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.enable_thinking is None
    translator.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", None),
        ("   ", None),
        ("true", True),
        ("TRUE", True),
        (" false ", False),
        ("FaLsE", False),
    ],
)
def test_from_env_parses_optional_thinking_configuration(
    monkeypatch,
    value,
    expected,
):
    monkeypatch.setenv(
        "TRANSLATION_BASE_URL",
        "http://translate.example/v1",
    )
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")
    monkeypatch.setenv("TRANSLATION_ENABLE_THINKING", value)

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.enable_thinking is expected
    translator.close()


@pytest.mark.parametrize(
    "value",
    ["1", "0", "yes", "no", "enabled", "tru"],
)
def test_from_env_rejects_invalid_thinking_configuration(
    monkeypatch,
    value,
):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "configured")
    monkeypatch.setenv("TRANSLATION_API_KEY", "configured")
    monkeypatch.setenv("TRANSLATION_MODEL", "configured")
    monkeypatch.setenv("TRANSLATION_ENABLE_THINKING", value)

    with pytest.raises(
        TranslationConfigError,
        match="TRANSLATION_ENABLE_THINKING",
    ):
        OpenAICompatibleTranslator.from_env()


@pytest.mark.parametrize("value", [0, 1, "false", object()])
def test_constructor_rejects_non_boolean_thinking_configuration(value):
    with pytest.raises(
        TranslationConfigError,
        match="TRANSLATION_ENABLE_THINKING",
    ):
        OpenAICompatibleTranslator(
            base_url="http://translate.example/v1",
            api_key="secret",
            model="paper-model",
            enable_thinking=value,
        )


def test_from_env_reads_concurrency(monkeypatch):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "http://translate.example/v1")
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")
    monkeypatch.setenv("TRANSLATION_CONCURRENCY", "8")

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.concurrency == 8
    translator.close()


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "1.5", "not-an-integer", "01"],
)
def test_from_env_rejects_invalid_concurrency(monkeypatch, value):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "configured")
    monkeypatch.setenv("TRANSLATION_API_KEY", "configured")
    monkeypatch.setenv("TRANSLATION_MODEL", "configured")
    monkeypatch.setenv("TRANSLATION_CONCURRENCY", value)

    with pytest.raises(
        TranslationConfigError,
        match="TRANSLATION_CONCURRENCY",
    ):
        OpenAICompatibleTranslator.from_env()


def test_from_env_reads_timeout_and_retry_configuration(monkeypatch):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "http://translate.example/v1")
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")
    monkeypatch.setenv("TRANSLATION_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("TRANSLATION_MAX_RETRIES", "3")

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.timeout_seconds == 12.5
    assert translator.max_retries == 3
    translator.close()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TRANSLATION_TIMEOUT_SECONDS", "0"),
        ("TRANSLATION_TIMEOUT_SECONDS", "nan"),
        ("TRANSLATION_TIMEOUT_SECONDS", "not-a-number"),
        ("TRANSLATION_MAX_RETRIES", "-1"),
        ("TRANSLATION_MAX_RETRIES", "1.5"),
        ("TRANSLATION_MAX_RETRIES", "not-an-integer"),
    ],
)
def test_from_env_rejects_invalid_timeout_or_retries(monkeypatch, name, value):
    for env_name in (
        "TRANSLATION_BASE_URL",
        "TRANSLATION_API_KEY",
        "TRANSLATION_MODEL",
    ):
        monkeypatch.setenv(env_name, "configured")
    monkeypatch.setenv(name, value)

    with pytest.raises(TranslationConfigError, match=name):
        OpenAICompatibleTranslator.from_env()


def test_constructor_passes_configured_timeout_to_http_client(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        def close(self):
            pass

    monkeypatch.setattr("pdf_trans.translation_client.httpx.Client", FakeClient)

    translator = OpenAICompatibleTranslator(
        base_url="http://translate.example/v1",
        api_key="secret",
        model="paper-model",
        timeout_seconds=7.25,
        max_retries=4,
    )

    assert captured["timeout"] == 7.25
    assert translator.timeout_seconds == 7.25
    assert translator.max_retries == 4
    translator.close()


@pytest.mark.parametrize(
    "missing_name",
    ["TRANSLATION_BASE_URL", "TRANSLATION_API_KEY", "TRANSLATION_MODEL"],
)
def test_from_env_rejects_missing_configuration(monkeypatch, missing_name):
    for name in (
        "TRANSLATION_BASE_URL",
        "TRANSLATION_API_KEY",
        "TRANSLATION_MODEL",
    ):
        monkeypatch.setenv(name, "configured")
    monkeypatch.delenv(missing_name)

    with pytest.raises(TranslationConfigError, match=missing_name):
        OpenAICompatibleTranslator.from_env()


def test_translate_sends_compatible_request_and_returns_only_content():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "中文译文 [38]"}}]},
        )

    translator = OpenAICompatibleTranslator(
        base_url="http://translate.example/v1/",
        api_key="secret",
        model="paper-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = translator.translate("Source [38] with $x$ and C-1 at 20%.")

    assert result == "中文译文 [38]"
    assert seen["url"] == "http://translate.example/v1/chat/completions"
    assert seen["authorization"] == "Bearer secret"
    assert seen["payload"] == {
        "model": "paper-model",
        "messages": [
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Source [38] with $x$ and C-1 at 20%.",
            },
        ],
    }
    assert set(seen["payload"]) == {"model", "messages"}
    assert "reasoning" not in seen["payload"]
    assert "reasoning_effort" not in seen["payload"]
    assert "thinking" not in seen["payload"]
    assert "chat_template_kwargs" not in seen["payload"]
    for required in (
        "[38]",
        "[39–41]",
        "LaTeX",
        "$...$",
        "C-1",
        "SS1",
        "占位符",
        "只返回译文",
        "完整翻译所有英文说明性内容",
        "公式占位符是不可翻译的原子字符串",
        "每个公式占位符只能出现一次",
        "占位符可以根据中文语序调整位置",
        "不得在占位符内部插入空格",
        "保留 HTML、LaTeX 和其他结构化标记的结构",
        "技术术语应结合化工论文语境翻译",
    ):
        assert required in TRANSLATION_SYSTEM_PROMPT


def test_translate_with_instruction_adds_retry_instruction_to_system_prompt():
    seen = {}

    def handler(request):
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "译文"}}]},
        )

    translator = OpenAICompatibleTranslator(
        base_url="http://translate.example/v1",
        api_key="secret",
        model="paper-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert translator.translate_with_instruction("Source", "重试要求") == "译文"
    assert seen["payload"]["messages"] == [
        {
            "role": "system",
            "content": TRANSLATION_SYSTEM_PROMPT + "\n\n重试要求",
        },
        {"role": "user", "content": "Source"},
    ]


@pytest.mark.parametrize("enabled", [True, False])
def test_translate_sends_configured_vllm_thinking_switch(enabled):
    seen = {}

    def handler(request):
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "译文"}}]},
        )

    translator = OpenAICompatibleTranslator(
        base_url="http://translate.example/v1",
        api_key="secret",
        model="qwen3.6-plus",
        enable_thinking=enabled,
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler)
        ),
    )

    assert translator.translate("Source") == "译文"
    assert seen["payload"]["chat_template_kwargs"] == {
        "enable_thinking": enabled,
    }
    assert "enable_thinking" not in seen["payload"]


def test_translate_includes_explicit_response_format():
    seen = {}
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "table_translation",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }

    def handler(request):
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"translations":[]}'}}]},
        )

    translator = OpenAICompatibleTranslator(
        base_url="http://translate.example/v1",
        api_key="secret",
        model="paper-model",
        enable_thinking=False,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = translator.translate(
        "table request",
        response_format=response_format,
    )

    assert result == '{"translations":[]}'
    assert seen["payload"]["response_format"] == response_format
    assert seen["payload"]["chat_template_kwargs"] == {
        "enable_thinking": False,
    }
    assert set(seen["payload"]) == {
        "model",
        "messages",
        "response_format",
        "chat_template_kwargs",
    }


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(503), "翻译请求失败"),
        (httpx.Response(200, content=b"not-json"), "翻译请求失败"),
        (httpx.Response(200, json={"choices": []}), "翻译请求失败"),
        (
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "  "}}]},
            ),
            "翻译响应缺少非空译文",
        ),
    ],
)
def test_translate_rejects_http_and_response_errors(response, message):
    translator = OpenAICompatibleTranslator(
        base_url="http://translate.example/v1",
        api_key="secret",
        model="paper-model",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: response)
        ),
    )

    with pytest.raises(TranslationClientError, match=message):
        translator.translate("Source")
