from copy import deepcopy

import pytest

from pdf_trans.errors import FormulaAuditError
from pdf_trans.formula_scanner import (
    FormulaSpan,
    rebuild_raw_formula,
    replace_equation_formula,
    replace_formula_spans,
    replace_table_formula_spans,
    scan_content_list,
    scan_formula_spans,
)


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


def test_public_scanner_returns_all_supported_formula_spans():
    source = r"a $x$ b $$y$$ c \(z\) d \[w\]"

    spans = scan_formula_spans(source)

    assert all(isinstance(span, FormulaSpan) for span in spans)
    assert [span.raw_formula for span in spans] == [
        "$x$",
        "$$y$$",
        r"\(z\)",
        r"\[w\]",
    ]
    assert [span.katex_formula for span in spans] == ["x", "y", "z", "w"]
    assert [span.is_block for span in spans] == [False, True, False, True]
    assert [source[span.start : span.end] for span in spans] == [
        span.raw_formula for span in spans
    ]
    assert [source[span.katex_start : span.katex_end] for span in spans] == [
        span.katex_formula for span in spans
    ]


@pytest.mark.parametrize(
    "source",
    [
        "$5",
        "$10.50",
        "$5 million",
        "$10 billion",
        "US$5",
        "USD $5",
        "US$5 and US$10",
        "USD $5 and USD $10",
        "from $5 million to $10 million",
        "$5–$10",
        "$5-$10",
        "price is $5 and $10",
    ],
)
def test_public_scanner_leaves_ordinary_dollar_amounts_as_text(source):
    assert scan_formula_spans(source) == ()


def test_public_scanner_ignores_amounts_but_keeps_real_formulas():
    source = (
        "from $5 million to $10 billion; "
        r"$x$ $C_7$ $x + y = 10$ "
        r"$\mathrm{C}_{8}$ $x < y >$ $$x^2+y^2$$"
    )

    assert [span.raw_formula for span in scan_formula_spans(source)] == [
        "$x$",
        "$C_7$",
        "$x + y = 10$",
        r"$\mathrm{C}_{8}$",
        "$x < y >$",
        "$$x^2+y^2$$",
    ]


@pytest.mark.parametrize(
    "source",
    [
        "<table><tr><td>$5</td><td>$10</td></tr></table>",
        "<span>$x</span><span>y$</span>",
    ],
)
def test_public_scanner_does_not_pair_dollars_across_html_tags(source):
    assert scan_formula_spans(source) == ()


def test_table_scanner_does_not_pair_amounts_from_different_cells():
    items = [
        {
            "type": "table",
            "table_body": (
                "<table><tr><td>$5</td><td>$10</td></tr></table>"
            ),
        }
    ]

    assert scan_content_list(items) == ()


@pytest.mark.parametrize(
    "source",
    [
        r"\$x\$",
        r"\$\$x\$\$",
        r"\\(x\\)",
        r"\\[x\\]",
        "$x",
        "$$x",
        r"\(x",
        r"\[x",
    ],
)
def test_public_scanner_treats_escaped_and_unclosed_boundaries_as_text(source):
    assert scan_formula_spans(source) == ()


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


def test_rebuilds_formula_without_changing_delimiters_or_outer_space():
    candidates = scan_content_list(
        [
            {
                "type": "equation",
                "text": "  $$\n\\complement _ {4}\n$$  ",
            },
            {
                "type": "text",
                "text": r"before \(\frac {m 3}{h}\) after",
            },
        ]
    )

    assert rebuild_raw_formula(
        candidates[0],
        "\n\\mathrm{C}_{4}\n",
    ) == "  $$\n\\mathrm{C}_{4}\n$$  "
    assert rebuild_raw_formula(
        candidates[1],
        r"\frac{\mathrm{m}^{3}}{\mathrm{h}}",
    ) == r"\(\frac{\mathrm{m}^{3}}{\mathrm{h}}\)"


def test_replaces_only_complete_delimited_formula_spans():
    replacements = {
        (r"$x \neq y$", False): r"$x = y$",
    }
    source = r"literal x \neq y; formula $x \neq y$"

    assert replace_formula_spans(source, replacements) == (
        r"literal x \neq y; formula $x = y$"
    )


def test_table_replacement_ignores_attributes_and_hidden_nodes():
    raw = r"$\frac {m 3}{h}$"
    normalized = r"$\frac{\mathrm{m}^{3}}{\mathrm{h}}$"
    html = (
        f'<table data-formula="{raw}"><tr><td>{raw}</td>'
        f"<td><script>{raw}</script></td></tr></table>"
    )

    assert replace_table_formula_spans(
        html,
        {(raw, False): normalized},
    ) == (
        f'<table data-formula="{raw}"><tr><td>{normalized}</td>'
        f"<td><script>{raw}</script></td></tr></table>"
    )


def test_table_replacement_preserves_multiline_offsets():
    raw = "$x$"
    html = (
        "<table>\n"
        "  <tr>\n"
        f"    <td>first {raw}</td>\n"
        f"    <td>second {raw}</td>\n"
        "  </tr>\n"
        "</table>"
    )

    assert replace_table_formula_spans(
        html,
        {(raw, False): "$y$"},
    ) == html.replace(raw, "$y$")


def test_equation_replacement_requires_exact_raw_and_display_mode():
    source = "$$x$$"

    assert replace_equation_formula(
        source,
        {(source, False): "$$y$$"},
    ) == source
    assert replace_equation_formula(
        source,
        {(source, True): "$$y$$"},
    ) == "$$y$$"
