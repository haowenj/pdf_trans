import json
import re

import pytest

from pdf_trans.table_translation import (
    CellWorkItem,
    TableTranslationError,
    plan_cell_batches,
    prepare_table_translation,
)


def test_extracts_stable_cells_and_never_sends_outer_html_or_attributes():
    source = (
        '<table class="source"><caption>Operating Data</caption>'
        '<tr><th rowspan="2">Component</th><th>123</th></tr>'
        '<tr><td colspan="3"><em>Mass</em> Fraction</td>'
        '<td data-code="A1">D1298 / IP 160</td></tr></table>'
    )

    prepared = prepare_table_translation(source)

    assert [cell.cell_id for cell in prepared.cells] == [
        "cell-0001",
        "cell-0002",
        "cell-0003",
        "cell-0004",
        "cell-0005",
    ]
    assert [work.work_id for work in prepared.work_items] == [
        "cell-0001",
        "cell-0002",
        "cell-0004",
    ]
    serialized = " ".join(
        f"{work.work_id}:{work.model_text}"
        for work in prepared.work_items
    )
    assert "<table" not in serialized
    assert "<em" not in serialized
    assert "rowspan" not in serialized
    assert "colspan" not in serialized
    assert "data-code" not in serialized
    assert "⟦H0⟧Mass⟦H1⟧ Fraction" in serialized


def test_formula_ids_restart_in_each_cell_and_latex_is_hidden():
    first = r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    second = r"$x^{2}+\frac{a}{b}$ and $y_{1}$"
    source = (
        "<table><tr>"
        f"<td>Density {first}</td>"
        f"<td>Values {second}</td>"
        "</tr></table>"
    )

    prepared = prepare_table_translation(source)

    assert [work.model_text for work in prepared.work_items] == [
        "Density ⟦M0⟧",
        "Values ⟦M0⟧ and ⟦M1⟧",
    ]
    request_text = " ".join(
        work.model_text for work in prepared.work_items
    )
    assert r"\circ" not in request_text
    assert r"\frac" not in request_text


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


def test_rebuilds_only_successful_cells_and_preserves_exact_outer_structure():
    formula = r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    source = (
        '<table class="source"><tr>'
        f'<td rowspan="2">Density {formula}</td>'
        '<td colspan="3"><em>Mass</em> Fraction</td>'
        '<td data-code="A1">Failure $x$</td>'
        "</tr></table>"
    )
    prepared = prepare_table_translation(source)

    result = prepared.rebuild(
        {
            "cell-0001": "密度 ⟦M0⟧",
            "cell-0002": "⟦H0⟧质量⟦H1⟧ 分数",
        },
        {"cell-0003": "公式占位符被篡改"},
    )

    assert result.translated_html == (
        '<table class="source"><tr>'
        f'<td rowspan="2">密度 {formula}</td>'
        '<td colspan="3"><em>质量</em> 分数</td>'
        '<td data-code="A1">Failure $x$</td>'
        "</tr></table>"
    )
    assert result.success_cell_count == 2
    assert result.fallback_cell_count == 1
    assert result.fallbacks[0].cell_id == "cell-0003"
    assert result.fallbacks[0].error == "公式占位符被篡改"
    assert 'rowspan="2"' in result.translated_html
    assert 'colspan="3"' in result.translated_html
    assert 'data-code="A1"' in result.translated_html


def test_escapes_model_text_but_restores_original_inline_html_and_entities():
    source = (
        '<table><tr><td><strong class="x">Salt</strong>'
        " &amp; water</td></tr></table>"
    )
    prepared = prepare_table_translation(source)

    work = prepared.work_items[0]
    assert work.model_text == "⟦H0⟧Salt⟦H1⟧ ⟦H2⟧ water"
    result = prepared.rebuild(
        {
            work.work_id: (
                "⟦H0⟧盐⟦H1⟧ ⟦H2⟧ 水 < 乙醇"
            )
        },
        {},
    )

    assert result.translated_html == (
        '<table><tr><td><strong class="x">盐</strong>'
        " &amp; 水 &lt; 乙醇</td></tr></table>"
    )


def test_skips_empty_numeric_formula_hidden_and_standard_code_cells():
    prepared = prepare_table_translation(
        "<table><tr>"
        "<td> </td><td>775 to 840</td><td>$x^{2}$</td>"
        "<td>D1298 / IP 160</td><td>D1298 or IP 160</td>"
        "<td><script>Ignored English</script>123</td>"
        "<td>中文</td>"
        "</tr></table>"
    )

    assert [work.work_id for work in prepared.work_items] == ["cell-0005"]


@pytest.mark.parametrize(
    "value",
    [
        "775 to 840",
        "10.5 through 12.5",
        "-20 to +40 °C",
        "15 to 20%",
    ],
)
def test_skips_pure_numeric_ranges_with_ascii_connectors(value):
    prepared = prepare_table_translation(
        f"<table><tr><td>{value}</td></tr></table>"
    )

    assert prepared.work_items == ()


def test_standard_codes_are_skipped_only_when_the_whole_cell_matches():
    prepared = prepare_table_translation(
        "<table><tr>"
        "<td>ASTM D1298</td><td>IP 160, UOP 365</td>"
        "<td>Use ASTM D1298</td><td>D1298 and IP 160</td>"
        "</tr></table>"
    )

    assert [work.work_id for work in prepared.work_items] == [
        "cell-0003",
        "cell-0004",
    ]


def test_any_failed_segment_falls_back_the_whole_cell():
    long_text = "First sentence. " * 700
    source = f"<table><tr><td>{long_text}</td></tr></table>"
    prepared = prepare_table_translation(source)
    assert len(prepared.work_items) > 1
    translations = {
        work.work_id: "第一句。"
        for work in prepared.work_items[:-1]
    }
    last = prepared.work_items[-1]

    result = prepared.rebuild(
        translations,
        {last.work_id: "模型返回空译文"},
    )

    assert result.translated_html == source
    assert result.success_cell_count == 0
    assert result.fallback_cell_count == 1
    assert result.fallbacks == (
        type(result.fallbacks[0])("cell-0001", "模型返回空译文"),
    )


def test_segment_lookup_returns_local_context_and_rejects_unknown_id():
    prepared = prepare_table_translation(
        "<table><tr><td>Alpha $x$</td></tr></table>"
    )

    segment = prepared.segment_for("cell-0001")

    assert segment.model_text == "Alpha ⟦M0⟧"
    with pytest.raises(KeyError, match="cell-9999"):
        prepared.segment_for("cell-9999")


def test_rebuild_rejects_marker_tampering_for_only_that_cell():
    source = (
        "<table><tr><td>Alpha $x$</td><td>Beta $y$</td></tr></table>"
    )
    prepared = prepare_table_translation(source)

    result = prepared.rebuild(
        {
            "cell-0001": "甲 ⟦M0⟧",
            "cell-0002": "乙 ⟦M1⟧",
        },
        {},
    )

    assert result.translated_html == (
        "<table><tr><td>甲 $x$</td><td>Beta $y$</td></tr></table>"
    )
    assert result.fallback_cell_count == 1
    assert re.search("新增|缺失", result.fallbacks[0].error)


def test_reserved_source_marker_falls_back_only_colliding_cell():
    source = (
        "<table><tr><td>Alpha</td>"
        "<td>Literal ⟦M0⟧ marker</td></tr></table>"
    )
    prepared = prepare_table_translation(source)

    assert [work.work_id for work in prepared.work_items] == [
        "cell-0001"
    ]
    result = prepared.rebuild({"cell-0001": "甲"}, {})

    assert result.translated_html == (
        "<table><tr><td>甲</td>"
        "<td>Literal ⟦M0⟧ marker</td></tr></table>"
    )
    assert result.success_cell_count == 1
    assert result.fallback_cell_count == 1
    assert result.fallbacks[0].cell_id == "cell-0002"
    assert "保留的公式占位符" in result.fallbacks[0].error


def test_rebuild_preserves_inline_cdata_exactly():
    source = (
        "<table><tr><td>Alpha "
        "<![CDATA[value > other]]> Beta</td></tr></table>"
    )
    prepared = prepare_table_translation(source)

    assert prepared.work_items[0].model_text == (
        "Alpha ⟦H0⟧ Beta"
    )
    result = prepared.rebuild(
        {"cell-0001": "甲 ⟦H0⟧ 乙"},
        {},
    )

    assert result.translated_html == (
        "<table><tr><td>甲 "
        "<![CDATA[value > other]]> 乙</td></tr></table>"
    )


def work_item(
    number: int,
    *,
    tokens: int = 10,
    formulas: int = 0,
) -> CellWorkItem:
    return CellWorkItem(
        work_id=f"cell-{number:04d}",
        cell_id=f"cell-{number:04d}",
        segment_index=0,
        model_text=f"Text {number}",
        estimated_tokens=tokens,
        formula_count=formulas,
    )


def test_batch_planner_enforces_item_token_and_formula_limits():
    by_items = plan_cell_batches(
        tuple(work_item(number) for number in range(1, 14)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )
    by_tokens = plan_cell_batches(
        (work_item(1, tokens=1_500), work_item(2, tokens=600)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )
    by_formulas = plan_cell_batches(
        (work_item(1, formulas=20), work_item(2, formulas=5)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )

    assert [len(batch.items) for batch in by_items] == [12, 1]
    assert [len(batch.items) for batch in by_tokens] == [1, 1]
    assert [len(batch.items) for batch in by_formulas] == [1, 1]


def test_batch_planner_keeps_one_individually_oversized_item():
    batches = plan_cell_batches(
        (work_item(1, tokens=2_500), work_item(2)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )

    assert [batch.items[0].work_id for batch in batches] == [
        "cell-0001",
        "cell-0002",
    ]


def test_batch_request_and_schema_only_contain_current_work_items():
    batch = plan_cell_batches(
        (work_item(1), work_item(2)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )[0]

    request = json.loads(batch.build_request())
    schema = batch.build_response_format()

    assert request["cells"] == [
        {"cell_id": "cell-0001", "text": "Text 1"},
        {"cell_id": "cell-0002", "text": "Text 2"},
    ]
    assert schema["json_schema"]["schema"]["properties"]["cells"][
        "items"
    ]["properties"]["cell_id"]["enum"] == ["cell-0001", "cell-0002"]
    serialized = json.dumps(request, ensure_ascii=False)
    assert "translated_text" not in serialized


def test_partial_response_keeps_valid_item_and_isolates_missing_duplicate():
    batch = plan_cell_batches(
        (work_item(1), work_item(2), work_item(3)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )[0]
    response = json.dumps(
        {
            "cells": [
                {"cell_id": "cell-0001", "translated_text": "甲"},
                {"cell_id": "cell-0002", "translated_text": "乙"},
                {"cell_id": "cell-0002", "translated_text": "重复"},
                {"cell_id": "cell-9999", "translated_text": "未知"},
            ]
        },
        ensure_ascii=False,
    )

    parsed = batch.parse_response(response)

    assert parsed.translations == {"cell-0001": "甲"}
    assert parsed.errors == {
        "cell-0002": "模型结果存在重复 cell_id",
        "cell-0003": "模型结果缺少 cell_id",
    }
    assert parsed.warnings == ("模型结果包含未知 cell_id: cell-9999",)


def test_invalid_item_fields_only_fail_that_expected_cell():
    batch = plan_cell_batches(
        (work_item(1), work_item(2)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )[0]

    parsed = batch.parse_response(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_id": "cell-0001",
                        "translated_text": "甲",
                        "extra": True,
                    },
                    {
                        "cell_id": "cell-0002",
                        "translated_text": "乙",
                    },
                ]
            },
            ensure_ascii=False,
        )
    )

    assert parsed.translations == {"cell-0002": "乙"}
    assert parsed.errors == {"cell-0001": "模型翻译项字段不一致"}


def test_batch_response_preserves_model_boundary_whitespace():
    batch = plan_cell_batches(
        (work_item(1),),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )[0]

    parsed = batch.parse_response(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_id": "cell-0001",
                        "translated_text": " 前缀与后缀 ",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )

    assert parsed.translations == {
        "cell-0001": " 前缀与后缀 "
    }


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        "[]",
        '{"cells":{}}',
        '{"cells":[],"extra":true}',
    ],
)
def test_invalid_top_level_response_fails_the_batch(response):
    batch = plan_cell_batches(
        (work_item(1), work_item(2)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )[0]

    with pytest.raises(TableTranslationError):
        batch.parse_response(response)
