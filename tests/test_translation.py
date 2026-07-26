import json

import pytest

from pdf_trans.errors import TranslationContentError
from pdf_trans.translation import (
    TranslationStats,
    translate_content_list_file,
    translate_items,
)


class FakeTranslator:
    def __init__(self, results):
        self.results = iter(results)
        self.received = []

    def translate(self, text):
        self.received.append(text)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def test_translate_items_attempts_first_three_text_objects_and_preserves_data():
    items = [
        {"type": "text", "text": "Alpha [38]", "page_idx": 0},
        {"type": "image", "img_path": "images/a.png"},
        {"type": "text", "text": "Beta $x^2$", "custom": {"a": 1}},
        {"type": "text", "text": "Gamma C-1"},
        {"type": "text", "text": "Delta 20%"},
    ]
    translator = FakeTranslator(
        ["甲 [38]", RuntimeError("服务不可用"), "丙 C-1"]
    )

    translated, stats = translate_items(items, translator)

    assert translator.received == ["Alpha [38]", "Beta $x^2$", "Gamma C-1"]
    assert translated == [
        {
            "type": "text",
            "text": "Alpha [38]",
            "page_idx": 0,
            "translated_text": "甲 [38]",
            "translation_status": "success",
        },
        {"type": "image", "img_path": "images/a.png"},
        {
            "type": "text",
            "text": "Beta $x^2$",
            "custom": {"a": 1},
            "translated_text": None,
            "translation_status": "failed",
            "translation_error": "服务不可用",
        },
        {
            "type": "text",
            "text": "Gamma C-1",
            "translated_text": "丙 C-1",
            "translation_status": "success",
        },
        {
            "type": "text",
            "text": "Delta 20%",
            "translation_status": "pending",
        },
    ]
    assert items[0] == {"type": "text", "text": "Alpha [38]", "page_idx": 0}
    assert len(translated) == len(items)
    assert stats == TranslationStats(3, 2, 1, 1)


def test_translate_items_attempts_all_eligible_items_when_fewer_than_three():
    translator = FakeTranslator(["甲"])

    translated, stats = translate_items(
        [{"type": "table"}, {"type": "text", "text": "Alpha"}],
        translator,
    )

    assert translated[0] == {"type": "table"}
    assert stats == TranslationStats(1, 1, 0, 0)


def test_translate_content_list_file_writes_utf8_json(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "text", "text": "Alpha"}]),
        encoding="utf-8",
    )

    stats = translate_content_list_file(
        source,
        output,
        FakeTranslator(["阿尔法"]),
    )

    assert stats == TranslationStats(1, 1, 0, 0)
    assert json.loads(output.read_text(encoding="utf-8"))[0][
        "translated_text"
    ] == "阿尔法"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "无法读取规范化内容"),
        ('{"type": "text"}', "JSON 顶层必须是对象数组"),
        ('[{"type": "text"}, 1]', "JSON 顶层必须是对象数组"),
    ],
)
def test_translate_content_list_file_rejects_invalid_input(
    tmp_path,
    content,
    message,
):
    source = tmp_path / "normalized_content_list.json"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(TranslationContentError, match=message):
        translate_content_list_file(
            source,
            tmp_path / "translated_content_list.json",
            FakeTranslator([]),
        )
