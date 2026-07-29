import json

import pytest

from pdf_trans.table_translation import (
    TableTranslationError,
    prepare_table_translation,
)


def response(*items):
    return json.dumps(
        {
            "translations": [
                {"id": node_id, "text": text}
                for node_id, text in items
            ]
        },
        ensure_ascii=False,
    )


def test_extracts_caption_th_and_td_but_skips_numeric_and_hidden_text():
    prepared = prepare_table_translation(
        "<table><caption>Operating Data</caption>"
        "<tr><th>Component</th><th>123</th></tr>"
        "<tr><td>  Ethanol  </td><td><script>Ignored</script>42</td></tr>"
        "</table>"
    )

    payload = json.loads(prepared.build_request())

    assert payload["items"] == [
        {"id": "table-text-0001", "text": "Operating Data"},
        {"id": "table-text-0002", "text": "Component"},
        {"id": "table-text-0003", "text": "Ethanol"},
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "<table" not in serialized
    assert "123" not in serialized
    assert "42" not in serialized
    assert "Ignored" not in serialized


def test_applies_reordered_ids_and_preserves_tags_attributes_and_whitespace():
    source = (
        '<table class="source"><tr>'
        '<th rowspan="2"> Component </th>'
        '<td colspan="3"><em>Mass</em> Fraction</td>'
        '<td data-code="A1">100</td>'
        "</tr></table>"
    )
    prepared = prepare_table_translation(source)
    request = prepared.build_request()

    translated = prepared.apply_response(
        response(
            ("table-text-0003", "分数"),
            ("table-text-0001", "组分"),
            ("table-text-0002", "质量"),
        )
    )

    assert translated == (
        '<table class="source"><tr>'
        '<th rowspan="2"> 组分 </th>'
        '<td colspan="3"><em>质量</em> 分数</td>'
        '<td data-code="A1">100</td>'
        "</tr></table>"
    )
    assert 'rowspan="2"' in translated
    assert 'colspan="3"' in translated
    assert 'data-code="A1"' in translated
    assert "rowspan" not in request
    assert "colspan" not in request
    assert "data-code" not in request
    assert "A1" not in request


def test_escapes_translated_text_without_changing_structure():
    prepared = prepare_table_translation(
        "<table><tr><td>Salt and water</td></tr></table>"
    )

    translated = prepared.apply_response(
        response(("table-text-0001", "盐 < 水 & 乙醇"))
    )

    assert translated == (
        "<table><tr><td>盐 &lt; 水 &amp; 乙醇</td></tr></table>"
    )


@pytest.mark.parametrize(
    ("raw_response", "message"),
    [
        ("not json", "响应不是有效 JSON"),
        (
            json.dumps({"translations": []}),
            "结果数量不一致",
        ),
        (
            response(("table-text-9999", "错误节点")),
            "ID 集合不一致",
        ),
        (
            response(
                ("table-text-0001", "甲"),
                ("table-text-0001", "乙"),
            ),
            "存在重复 ID",
        ),
        (
            json.dumps(
                {
                    "translations": [
                        {"id": "table-text-0001", "text": "   "}
                    ]
                }
            ),
            "译文不能为空",
        ),
    ],
)
def test_rejects_invalid_batch_responses(raw_response, message):
    prepared = prepare_table_translation(
        "<table><tr><td>Alpha</td></tr></table>"
    )

    with pytest.raises(TableTranslationError, match=message):
        prepared.apply_response(raw_response)


@pytest.mark.parametrize(
    "source",
    [
        "",
        "<div>not a table</div>",
        "<table><tr><td>Alpha</tr></table>",
        "<table><tr><td>Alpha</td></tr>",
    ],
)
def test_rejects_blank_non_table_or_unbalanced_html(source):
    with pytest.raises(TableTranslationError, match="HTML"):
        prepare_table_translation(source)


def test_table_with_no_eligible_text_builds_no_model_payload():
    prepared = prepare_table_translation(
        "<table><tr><td>123.45</td><td>--</td><td>中文</td></tr></table>"
    )

    assert prepared.nodes == ()
    with pytest.raises(TableTranslationError, match="没有待翻译节点"):
        prepared.build_request()
