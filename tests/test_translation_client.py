import json

import httpx
import pytest

from pdf_trans.errors import TranslationClientError, TranslationConfigError
from pdf_trans.translation_client import (
    DEFAULT_TRANSLATION_MAX_RETRIES,
    DEFAULT_TRANSLATION_TIMEOUT_SECONDS,
    OpenAICompatibleTranslator,
    TRANSLATION_SYSTEM_PROMPT,
)


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
    for required in (
        "[38]",
        "[39–41]",
        "LaTeX",
        "$...$",
        "C-1",
        "SS1",
        "只返回译文",
    ):
        assert required in TRANSLATION_SYSTEM_PROMPT


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
