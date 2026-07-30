from copy import deepcopy

import pytest

from pdf_trans.errors import FormulaAuditError
from pdf_trans.formula_scanner import scan_content_list


def test_scans_equations_and_all_frontend_text_delimiters_without_mutation():
    items = [
        {
            "type": "equation",
            "page_idx": 2,
            "bbox": [1, 2, 3, 4],
            "text": "$$\nC_7 \\neqq C_6\n$$",
        },
        {
            "type": "text",
            "page_idx": 3,
            "bbox": [5, 6, 7, 8],
            "text": "a $x$ b $$y$$ c \\(z\\) d \\[w\\]",
        },
    ]
    original = deepcopy(items)

    formulas = scan_content_list(items)

    assert [value.raw_formula for value in formulas] == [
        "$$\nC_7 \\neqq C_6\n$$",
        "$x$",
        "$$y$$",
        "\\(z\\)",
        "\\[w\\]",
    ]
    assert [value.katex_formula for value in formulas] == [
        "\nC_7 \\neqq C_6\n",
        "x",
        "y",
        "z",
        "w",
    ]
    assert [value.is_block for value in formulas] == [
        True,
        False,
        True,
        False,
        True,
    ]
    assert formulas[0].field_path == "/0/text"
    assert formulas[1].field_path == "/1/text"
    assert formulas[0].page_idx == 2
    assert formulas[0].bbox == [1, 2, 3, 4]
    assert items == original


def test_equation_without_outer_delimiters_is_one_display_formula():
    formulas = scan_content_list(
        [{"type": "equation", "text": "E = mc^2", "page_idx": 0}]
    )

    assert len(formulas) == 1
    assert formulas[0].raw_formula == "E = mc^2"
    assert formulas[0].katex_formula == "E = mc^2"
    assert formulas[0].is_block is True
    assert formulas[0].source_type == "equation"


def test_scans_multiple_formulas_in_table_cell_and_expands_spans():
    items = [
        {
            "type": "table",
            "page_idx": 7,
            "bbox": [10, 20, 30, 40],
            "table_body": (
                "<table>"
                '<tr><th rowspan="2">Name</th>'
                '<th colspan="2">Values</th></tr>'
                "<tr><td>$x$ and $$y$$</td><td>\\(z\\)</td></tr>"
                "</table>"
            ),
        }
    ]

    formulas = scan_content_list(items)

    assert [value.raw_formula for value in formulas] == [
        "$x$",
        "$$y$$",
        "\\(z\\)",
    ]
    assert [
        (value.table_row_idx, value.table_col_idx) for value in formulas
    ] == [
        (1, 1),
        (1, 1),
        (1, 2),
    ]
    assert [value.formula_index for value in formulas] == [0, 1, 0]
    assert all(value.field_path == "/0/table_body" for value in formulas)
    assert all(value.source_type == "table_cell" for value in formulas)
    assert [value.cell_tag for value in formulas] == ["td", "td", "td"]


def test_scans_td_and_th_text_nodes_without_joining_across_tags():
    items = [
        {
            "type": "table",
            "table_body": (
                "<table><tr>"
                "<th>$h$</th>"
                "<td>$x<span>middle</span>y$ then <em>$z$</em></td>"
                "</tr></table>"
            ),
        }
    ]

    formulas = scan_content_list(items)

    assert [value.raw_formula for value in formulas] == ["$h$", "$z$"]
    assert [value.cell_tag for value in formulas] == ["th", "td"]


def test_escaped_and_unclosed_delimiters_are_plain_text():
    formulas = scan_content_list(
        [
            {
                "type": "text",
                "text": (
                    r"cost \$5, escaped \\(plain\\), "
                    r"unclosed $x and \[y"
                ),
            }
        ]
    )

    assert formulas == ()


def test_ids_are_repeatable_but_include_location():
    items = [{"type": "text", "page_idx": 0, "text": "$x$ and $x$"}]

    first = scan_content_list(items)
    second = scan_content_list(deepcopy(items))

    assert [value.formula_id for value in first] == [
        value.formula_id for value in second
    ]
    assert first[0].formula_id != first[1].formula_id
    assert first[0].content_hash == first[1].content_hash
    assert len(first[0].content_hash) == 64


def test_ignores_non_objects_and_non_string_formula_fields():
    formulas = scan_content_list(
        [
            None,
            "text",
            {"type": "text", "text": None},
            {"type": "equation", "text": 123},
            {"type": "table", "table_body": []},
        ]
    )

    assert formulas == ()


@pytest.mark.parametrize(
    "table_body",
    [
        "<table><tr><td>$x$</tr></table>",
        "<table><tr><td rowspan='0'>$x$</td></tr></table>",
        "<table><tr><td><th>$x$</th></td></tr></table>",
        "<table><tr><td>$x$</td></table>",
    ],
)
def test_rejects_malformed_table_html_with_field_path(table_body):
    with pytest.raises(FormulaAuditError, match=r"/0/table_body"):
        scan_content_list(
            [{"type": "table", "table_body": table_body}]
        )
