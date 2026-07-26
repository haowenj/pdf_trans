import copy
import json

import pytest

from mineru_cleaner.cross_page import (
    CROSS_PAGE_REASON,
    detect_cross_page_candidates,
    detect_cross_page_candidates_file,
    write_cross_page_candidates_file,
)
from mineru_cleaner.errors import ContentListError


def test_detect_cross_page_candidates_reports_full_candidate_without_mutation():
    previous_text = "跨页前半段 \n\t"
    next_text = "跨页后半段。"
    items = [
        {
            "type": "text",
            "text": previous_text,
            "page_idx": 5,
            "bbox": [1, 2, 3, 4],
        },
        {
            "type": "text",
            "text": next_text,
            "page_idx": 6,
            "bbox": [5, 6, 7, 8],
        },
    ]
    original = copy.deepcopy(items)

    candidates = detect_cross_page_candidates(items)

    assert candidates == [
        {
            "previous_index": 0,
            "next_index": 1,
            "previous_page_idx": 5,
            "next_page_idx": 6,
            "previous_text": previous_text,
            "next_text": next_text,
            "reason": CROSS_PAGE_REASON,
        }
    ]
    assert items == original


@pytest.mark.parametrize("ending", [".", "!", "?", ":", ";"])
def test_detect_cross_page_candidates_rejects_complete_sentence_endings(ending):
    items = [
        {"type": "text", "text": f"完整句{ending} \n", "page_idx": 2},
        {"type": "text", "text": "下一页", "page_idx": 3},
    ]

    assert detect_cross_page_candidates(items) == []


def test_detector_treats_non_configured_punctuation_as_candidate():
    items = [
        {"type": "text", "text": "中文句号。", "page_idx": 2},
        {"type": "text", "text": "下一页", "page_idx": 3},
    ]

    assert len(detect_cross_page_candidates(items)) == 1


def test_detect_cross_page_candidates_requires_consecutive_pages():
    invalid_page_pairs = [
        (2, 2),
        (2, 4),
        (2, 1),
    ]

    for previous_page, next_page in invalid_page_pairs:
        items = [
            {"type": "text", "text": "未结束", "page_idx": previous_page},
            {"type": "text", "text": "下一段", "page_idx": next_page},
        ]
        assert detect_cross_page_candidates(items) == []


def test_detector_does_not_skip_objects_but_still_detects_text_titles():
    items = [
        {"type": "text", "text": "不会跨过图片", "page_idx": 0},
        {"type": "image", "img_path": "images/a.jpg", "page_idx": 0},
        {"type": "text", "text": "上一页正文未结束", "page_idx": 1},
        {
            "type": "text",
            "text": "下一页标题",
            "text_level": 1,
            "page_idx": 2,
        },
    ]

    candidates = detect_cross_page_candidates(items)

    assert [(item["previous_index"], item["next_index"]) for item in candidates] == [
        (2, 3)
    ]


def test_detect_cross_page_candidates_skips_invalid_objects_and_fields():
    invalid_pairs = [
        [
            "非字典",
            {"type": "text", "text": "下一段", "page_idx": 1},
        ],
        [
            {"type": "ref_text", "text": "未结束", "page_idx": 0},
            {"type": "text", "text": "下一段", "page_idx": 1},
        ],
        [
            {"type": "text", "text": "未结束", "page_idx": True},
            {"type": "text", "text": "下一段", "page_idx": 1},
        ],
        [
            {"type": "text", "text": None, "page_idx": 0},
            {"type": "text", "text": "下一段", "page_idx": 1},
        ],
        [
            {"type": "text", "text": "   ", "page_idx": 0},
            {"type": "text", "text": "下一段", "page_idx": 1},
        ],
        [
            {"type": "text", "text": "未结束", "page_idx": 0},
            {"type": "text", "text": "", "page_idx": 1},
        ],
    ]

    for items in invalid_pairs:
        assert detect_cross_page_candidates(items) == []


def test_detect_cross_page_candidates_allows_overlapping_adjacent_pairs():
    items = [
        {"type": "text", "text": "第一页未结束", "page_idx": 0},
        {"type": "text", "text": "第二页仍未结束", "page_idx": 1},
        {"type": "text", "text": "第三页结束。", "page_idx": 2},
    ]

    candidates = detect_cross_page_candidates(items)

    assert [
        (item["previous_index"], item["next_index"]) for item in candidates
    ] == [(0, 1), (1, 2)]


def test_detect_cross_page_candidates_file_writes_report_without_changing_source(
    tmp_path,
):
    source = tmp_path / "cleaned_content_list.json"
    output = tmp_path / "cross_page_candidates.json"
    items = [
        {"type": "text", "text": "上一页未结束", "page_idx": 7},
        {"type": "text", "text": "下一页正文。", "page_idx": 8},
    ]
    source.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    source_before = source.read_bytes()

    count = detect_cross_page_candidates_file(source, output)

    assert count == 1
    assert json.loads(output.read_text(encoding="utf-8")) == [
        {
            "previous_index": 0,
            "next_index": 1,
            "previous_page_idx": 7,
            "next_page_idx": 8,
            "previous_text": "上一页未结束",
            "next_text": "下一页正文。",
            "reason": CROSS_PAGE_REASON,
        }
    ]
    assert output.read_bytes().endswith(b"\n")
    assert source.read_bytes() == source_before


def test_write_cross_page_candidates_file_writes_existing_candidates(tmp_path):
    output = tmp_path / "cross_page_candidates.json"
    candidates = [
        {
            "previous_index": 0,
            "next_index": 1,
            "previous_page_idx": 5,
            "next_page_idx": 6,
            "previous_text": "上半段",
            "next_text": "下半段",
            "reason": "诊断原因",
        }
    ]
    original = copy.deepcopy(candidates)

    write_cross_page_candidates_file(candidates, output)

    assert json.loads(output.read_text(encoding="utf-8")) == candidates
    assert output.read_text(encoding="utf-8").endswith("\n")
    assert candidates == original


def test_detect_cross_page_candidates_file_writes_empty_array(tmp_path):
    source = tmp_path / "cleaned_content_list.json"
    output = tmp_path / "cross_page_candidates.json"
    source.write_text(
        '[{"type": "text", "text": "完整句。", "page_idx": 0}]\n',
        encoding="utf-8",
    )

    count = detect_cross_page_candidates_file(source, output)

    assert count == 0
    assert output.read_text(encoding="utf-8") == "[]\n"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"type": "text"}', "顶层必须是数组"),
        ("not json", "无法读取 content list"),
    ],
)
def test_detect_cross_page_candidates_file_rejects_invalid_input(
    tmp_path, content, message
):
    source = tmp_path / "cleaned_content_list.json"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ContentListError, match=message):
        detect_cross_page_candidates_file(
            source,
            tmp_path / "cross_page_candidates.json",
        )
