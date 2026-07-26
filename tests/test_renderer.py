import copy
import json

import pytest

from mineru_cleaner.errors import ContentListError
from mineru_cleaner.renderer import render_content_list_file, render_items


def test_render_items_renders_supported_types_in_order_without_mutation():
    items = [
        {"type": "text", "text": "一级标题", "text_level": 1},
        {"type": "text", "text": "二级标题", "text_level": 2},
        {"type": "text", "text": "正文 **保持原样**"},
        {"type": "ref_text", "text": "1. Reference $x$"},
        {
            "type": "image",
            "img_path": "images/a.png",
            "image_caption": ["图注一", "图注二"],
            "image_footnote": ["图片脚注"],
        },
        {
            "type": "chart",
            "img_path": "images/chart.png",
            "chart_caption": ["图表说明"],
            "chart_footnote": ["图表脚注一", "图表脚注二"],
        },
        {
            "type": "table",
            "table_caption": ["表格说明"],
            "table_body": "<table><tr><td>A</td></tr></table>",
            "table_footnote": ["表格脚注"],
        },
        {
            "type": "equation",
            "text": "$$\nE = mc^2\n$$",
            "text_format": "latex",
        },
    ]
    original = copy.deepcopy(items)

    rendered = render_items(items)

    assert rendered == (
        "# 一级标题\n\n"
        "## 二级标题\n\n"
        "正文 **保持原样**\n\n"
        "1. Reference $x$\n\n"
        "![](images/a.png)\n\n"
        "图注一\n\n"
        "图注二\n\n"
        "图片脚注\n\n"
        "![](images/chart.png)\n\n"
        "图表说明\n\n"
        "图表脚注一\n\n"
        "图表脚注二\n\n"
        "表格说明\n\n"
        "<table><tr><td>A</td></tr></table>\n\n"
        "表格脚注\n\n"
        "$$\nE = mc^2\n$$\n"
    )
    assert items == original


def test_render_items_skips_invalid_values_and_uses_plain_text_for_other_levels():
    items = [
        {"type": "text", "text": "三级按正文", "text_level": 3},
        {"type": "text", "text": "布尔层级按正文", "text_level": True},
        {"type": "text", "text": "   "},
        {
            "type": "image",
            "img_path": "",
            "image_caption": ["有效图注", " ", 9],
            "image_footnote": None,
        },
        {"type": "chart", "chart_caption": "不是数组"},
        {"type": "unknown", "text": "不应输出"},
        "非字典元素",
    ]

    assert render_items(items) == (
        "三级按正文\n\n"
        "布尔层级按正文\n\n"
        "有效图注\n"
    )


def test_render_items_returns_empty_string_when_nothing_is_renderable():
    assert render_items(
        [
            {"type": "unknown", "text": "内容"},
            {"type": "text", "text": ""},
            None,
        ]
    ) == ""


def test_render_content_list_file_writes_utf8_markdown(tmp_path):
    source = tmp_path / "cleaned_content_list.json"
    output = tmp_path / "rendered.md"
    items = [
        {"type": "text", "text": "中文正文"},
        {"type": "equation", "text": "$$\nx + y\n$$"},
    ]
    source.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    render_content_list_file(source, output)

    assert output.read_text(encoding="utf-8") == "中文正文\n\n$$\nx + y\n$$\n"
    assert json.loads(source.read_text(encoding="utf-8")) == items


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"type": "text"}', "顶层必须是数组"),
        ("not json", "无法读取 content list"),
    ],
)
def test_render_content_list_file_rejects_invalid_input(
    tmp_path, content, message
):
    source = tmp_path / "cleaned_content_list.json"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ContentListError, match=message):
        render_content_list_file(source, tmp_path / "rendered.md")
