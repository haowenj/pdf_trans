import re

import pytest

from pdf_trans.table_translation import (
    TableTranslationError,
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

    assert [work.work_id for work in prepared.work_items] == [
        "cell-0002",
        "cell-0005",
    ]


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
