import copy
import json

import pytest

from pdf_trans.errors import NormalizationError
from pdf_trans.normalizer import (
    normalize_cross_page_items,
    write_normalized_content_list_file,
)


def test_normalize_cross_page_items_merges_pair_without_mutating_inputs():
    items = [
        {"type": "image", "img_path": "images/before.jpg"},
        {
            "type": "text",
            "text": "  上一页末尾   ",
            "page_idx": 5,
            "bbox": [1, 2, 3, 4],
            "custom": {"keep": True},
        },
        {
            "type": "text",
            "text": "   下一页开头  ",
            "page_idx": 6,
            "bbox": [5, 6, 7, 8],
        },
        {"type": "table", "table_body": "<table></table>"},
    ]
    candidates = [{"previous_index": 1, "next_index": 2}]
    original_items = copy.deepcopy(items)
    original_candidates = copy.deepcopy(candidates)

    normalized = normalize_cross_page_items(items, candidates)

    assert len(normalized) == 3
    assert normalized[0] == items[0]
    assert normalized[2] == items[3]
    assert normalized[1] == {
        "type": "text",
        "text": "  上一页末尾 下一页开头  ",
        "page_idx": 5,
        "bbox": [1, 2, 3, 4],
        "custom": {"keep": True},
        "source_page_indices": [5, 6],
        "source_bboxes": [[1, 2, 3, 4], [5, 6, 7, 8]],
        "merged_cross_page": True,
    }
    assert items == original_items
    assert candidates == original_candidates
    assert normalized[0] is not items[0]
    assert normalized[1]["custom"] is not items[1]["custom"]


def test_normalize_cross_page_items_merges_overlapping_and_independent_chains():
    items = [
        {"type": "text", "text": "A", "page_idx": 0, "bbox": [0]},
        {"type": "text", "text": "B", "page_idx": 1, "bbox": [1]},
        {"type": "text", "text": "C", "page_idx": 2, "bbox": [2]},
        {"type": "image", "img_path": "separator.jpg"},
        {"type": "text", "text": "D", "page_idx": 8, "bbox": [8]},
        {"type": "text", "text": "E", "page_idx": 9, "bbox": [9]},
        {"type": "table", "table_body": "<table></table>"},
    ]
    candidates = [
        {"previous_index": 1, "next_index": 2},
        {"previous_index": 4, "next_index": 5},
        {"previous_index": 0, "next_index": 1},
    ]

    normalized = normalize_cross_page_items(items, candidates)

    assert [item["type"] for item in normalized] == [
        "text",
        "image",
        "text",
        "table",
    ]
    assert normalized[0]["text"] == "A B C"
    assert normalized[0]["source_page_indices"] == [0, 1, 2]
    assert normalized[0]["source_bboxes"] == [[0], [1], [2]]
    assert normalized[2]["text"] == "D E"
    assert normalized[2]["source_page_indices"] == [8, 9]


def test_normalize_cross_page_items_records_missing_bbox_as_none():
    items = [
        {"type": "text", "text": "前", "page_idx": 2},
        {"type": "text", "text": "后", "page_idx": 3, "bbox": [3]},
    ]

    normalized = normalize_cross_page_items(
        items,
        [{"previous_index": 0, "next_index": 1}],
    )

    assert normalized[0]["source_bboxes"] == [None, [3]]


def test_normalize_cross_page_items_deep_copies_when_no_candidates():
    items = [{"type": "image", "metadata": {"value": 1}}]

    normalized = normalize_cross_page_items(items, [])

    assert normalized == items
    assert normalized is not items
    assert normalized[0] is not items[0]
    assert normalized[0]["metadata"] is not items[0]["metadata"]


@pytest.mark.parametrize(
    ("items", "candidates", "message"),
    [
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            ["not-a-dict"],
            "候选必须是对象",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": True, "next_index": 1}],
            "索引必须是整数",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": -1, "next_index": 0}],
            "索引越界",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": 1, "next_index": 2}],
            "索引越界",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
                {"type": "text", "text": "C", "page_idx": 2},
            ],
            [{"previous_index": 0, "next_index": 2}],
            "索引必须相邻",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [
                {"previous_index": 0, "next_index": 1},
                {"previous_index": 0, "next_index": 1},
            ],
            "候选对重复",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
                {"type": "text", "text": "C", "page_idx": 2},
            ],
            [
                {"previous_index": 0, "next_index": 1},
                {"previous_index": 0, "next_index": 2},
            ],
            "多个后继",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
                {"type": "text", "text": "C", "page_idx": 2},
            ],
            [
                {"previous_index": 0, "next_index": 2},
                {"previous_index": 1, "next_index": 2},
            ],
            "多个前驱",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [
                {"previous_index": 0, "next_index": 1},
                {"previous_index": 1, "next_index": 0},
            ],
            "形成环",
        ),
        (
            [
                {"type": "image", "img_path": "a.jpg", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": 0, "next_index": 1}],
            "必须都是 text",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": True},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": 0, "next_index": 1}],
            "page_idx 必须是整数",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 2},
            ],
            [{"previous_index": 0, "next_index": 1}],
            "页面必须连续",
        ),
        (
            [
                {"type": "text", "text": "   ", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": 0, "next_index": 1}],
            "text 必须是非空字符串",
        ),
    ],
)
def test_normalize_cross_page_items_rejects_invalid_candidates(
    items,
    candidates,
    message,
):
    with pytest.raises(NormalizationError, match=message):
        normalize_cross_page_items(items, candidates)


def test_write_normalized_content_list_file_writes_utf8_json(tmp_path):
    output = tmp_path / "normalized_content_list.json"
    items = [{"type": "text", "text": "中文"}]

    write_normalized_content_list_file(items, output)

    assert json.loads(output.read_text(encoding="utf-8")) == items
    assert "中文" in output.read_text(encoding="utf-8")
    assert output.read_text(encoding="utf-8").endswith("\n")


def test_normalize_cross_page_items_reduces_139_items_to_138():
    items = [
        {"type": "image", "img_path": f"images/{index}.jpg"}
        for index in range(139)
    ]
    items[36] = {
        "type": "text",
        "text": "前半段唯一标记",
        "page_idx": 5,
        "bbox": [36],
    }
    items[37] = {
        "type": "text",
        "text": "后半段唯一标记",
        "page_idx": 6,
        "bbox": [37],
    }

    normalized = normalize_cross_page_items(
        items,
        [{"previous_index": 36, "next_index": 37}],
    )

    assert len(items) == 139
    assert len(normalized) == 138
    assert "前半段唯一标记" in normalized[36]["text"]
    assert "后半段唯一标记" in normalized[36]["text"]
    assert normalized[36]["source_page_indices"] == [5, 6]
    assert normalized[36]["source_bboxes"] == [[36], [37]]
    assert not any(item == items[37] for item in normalized)
