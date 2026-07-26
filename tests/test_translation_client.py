import json

import httpx
import pytest

from pdf_trans.errors import TranslationClientError, TranslationConfigError
from pdf_trans.translation_client import (
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
