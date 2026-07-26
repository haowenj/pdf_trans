import copy
import json

import pytest

from pdf_trans.cleaner import (
    ContentStats,
    CleaningStats,
    clean_content_list_file,
    clean_content_list_file_with_items,
    clean_items,
    summarize_items,
)
from pdf_trans.errors import ContentListError


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
    assert stats == CleaningStats(
        before_count=11,
        filtered_count=4,
        after_count=7,
        content_stats=ContentStats(
            type_counts={
                "chart": 1,
                "future_type": 1,
                "image": 1,
                "ref_text": 1,
                "table": 1,
                "text": 2,
            },
            text_level_count=1,
            text_level_counts={2: 1},
            page_idx_counts={1: 2, 2: 1, 3: 1},
        ),
    )


def test_clean_items_preserves_text_with_missing_or_non_string_text_value():
    items = [
        {"type": "text"},
        {"type": "text", "text": None},
        {"type": "text", "text": 0},
    ]

    cleaned, stats = clean_items(items)

    assert cleaned == items
    assert stats == CleaningStats(
        before_count=3,
        filtered_count=0,
        after_count=3,
        content_stats=ContentStats(
            type_counts={"text": 3},
            text_level_count=0,
            text_level_counts={},
            page_idx_counts={},
        ),
    )


def test_summarize_items_counts_valid_fields_in_sorted_groups_without_mutation():
    items = [
        {"type": "text", "text": "标题二", "text_level": 2, "page_idx": 0},
        {"type": "image", "text_level": 1, "page_idx": 1},
        {"type": "text", "text": "标题一", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "另一个标题", "text_level": 2, "page_idx": 1},
        {"type": "future", "page_idx": 2},
        {"type": 9, "page_idx": True},
        {"text": "缺少类型", "text_level": False, "page_idx": "3"},
        "原始非字典元素",
    ]
    original = copy.deepcopy(items)

    stats = summarize_items(items)

    assert stats == ContentStats(
        type_counts={"future": 1, "image": 1, "text": 3},
        text_level_count=3,
        text_level_counts={1: 1, 2: 2},
        page_idx_counts={0: 2, 1: 2, 2: 1},
    )
    assert list(stats.type_counts) == ["future", "image", "text"]
    assert list(stats.text_level_counts) == [1, 2]
    assert list(stats.page_idx_counts) == [0, 1, 2]
    assert items == original


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

    assert stats == CleaningStats(
        before_count=2,
        filtered_count=1,
        after_count=1,
        content_stats=ContentStats(
            type_counts={"text": 1},
            text_level_count=0,
            text_level_counts={},
            page_idx_counts={0: 1},
        ),
    )
    assert json.loads(output.read_text(encoding="utf-8")) == [
        {"type": "text", "text": "中文正文", "page_idx": 0}
    ]
    assert "中文正文" in output.read_text(encoding="utf-8")


def test_clean_content_list_file_with_items_returns_written_items(tmp_path):
    source = tmp_path / "content_list.json"
    output = tmp_path / "cleaned_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"type": "header", "text": "页眉"},
                {
                    "type": "text",
                    "text": "正文",
                    "page_idx": 3,
                    "bbox": [1, 2, 3, 4],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cleaned, stats = clean_content_list_file_with_items(source, output)

    assert cleaned == [
        {
            "type": "text",
            "text": "正文",
            "page_idx": 3,
            "bbox": [1, 2, 3, 4],
        }
    ]
    assert json.loads(output.read_text(encoding="utf-8")) == cleaned
    assert stats.before_count == 2
    assert stats.filtered_count == 1
    assert stats.after_count == 1


def test_clean_content_list_file_does_not_serialize_statistics(tmp_path):
    source = tmp_path / "paper_content_list.json"
    output = tmp_path / "cleaned_content_list.json"
    heading = {"type": "text", "text": "标题", "text_level": 1, "page_idx": 0}
    source.write_text(
        json.dumps([{"type": "header", "text": "页眉"}, heading], ensure_ascii=False),
        encoding="utf-8",
    )

    stats = clean_content_list_file(source, output)

    assert json.loads(output.read_text(encoding="utf-8")) == [heading]
    assert stats.content_stats == ContentStats(
        type_counts={"text": 1},
        text_level_count=1,
        text_level_counts={1: 1},
        page_idx_counts={0: 1},
    )


def test_clean_content_list_file_rejects_non_array_json(tmp_path):
    source = tmp_path / "paper_content_list.json"
    source.write_text('{"type": "text"}', encoding="utf-8")

    with pytest.raises(ContentListError, match="顶层必须是数组"):
        clean_content_list_file(source, tmp_path / "cleaned_content_list.json")
