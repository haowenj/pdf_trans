import json

import pytest

from mineru_cleaner.cleaner import (
    CleaningStats,
    clean_content_list_file,
    clean_items,
)
from mineru_cleaner.errors import ContentListError


def test_clean_items_removes_only_explicitly_filtered_entries():
    body = {
        "type": "text",
        "text": "正文",
        "page_idx": 1,
        "bbox": [1, 2, 3, 4],
    }
    heading = {
        "type": "text",
        "text": "标题",
        "text_level": 2,
        "page_idx": 1,
    }
    image = {
        "type": "image",
        "img_path": "images/a.jpg",
        "image_caption": ["图 1"],
        "page_idx": 2,
    }
    table = {
        "type": "table",
        "table_body": "<table><tr><td>A</td></tr></table>",
        "table_caption": ["表 1"],
        "page_idx": 3,
    }
    chart = {"type": "chart", "img_path": "images/chart.jpg", "content": ""}
    reference = {"type": "ref_text", "text": "1. Reference"}
    unknown = {"type": "future_type", "payload": {"unchanged": True}}
    items = [
        {"type": "header", "text": "页眉"},
        body,
        {"type": "footer", "text": "页脚"},
        heading,
        {"type": "page_number", "text": "2"},
        {"type": "text", "text": " \n\t "},
        image,
        table,
        chart,
        reference,
        unknown,
    ]

    cleaned, stats = clean_items(items)

    assert cleaned == [body, heading, image, table, chart, reference, unknown]
    assert cleaned[0] is body
    assert cleaned[2] is image
    assert cleaned[3] is table
    assert stats == CleaningStats(before_count=11, filtered_count=4, after_count=7)


def test_clean_items_preserves_text_with_missing_or_non_string_text_value():
    items = [
        {"type": "text"},
        {"type": "text", "text": None},
        {"type": "text", "text": 0},
    ]

    cleaned, stats = clean_items(items)

    assert cleaned == items
    assert stats == CleaningStats(before_count=3, filtered_count=0, after_count=3)


def test_clean_content_list_file_writes_utf8_json_and_counts(tmp_path):
    source = tmp_path / "paper_content_list.json"
    output = tmp_path / "cleaned_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"type": "header", "text": "页眉"},
                {"type": "text", "text": "中文正文", "page_idx": 0},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    stats = clean_content_list_file(source, output)

    assert stats == CleaningStats(before_count=2, filtered_count=1, after_count=1)
    assert json.loads(output.read_text(encoding="utf-8")) == [
        {"type": "text", "text": "中文正文", "page_idx": 0}
    ]
    assert "中文正文" in output.read_text(encoding="utf-8")


def test_clean_content_list_file_rejects_non_array_json(tmp_path):
    source = tmp_path / "paper_content_list.json"
    source.write_text('{"type": "text"}', encoding="utf-8")

    with pytest.raises(ContentListError, match="顶层必须是数组"):
        clean_content_list_file(source, tmp_path / "cleaned_content_list.json")
