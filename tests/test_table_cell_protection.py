import hashlib

import pytest

from pdf_trans.table_cell_protection import (
    CellMarker,
    CellProtectionError,
    estimate_model_tokens,
    protect_cell_text,
    restore_cell_segment,
    split_protected_segment,
)


def marker(marker_id: str, original: str) -> CellMarker:
    return CellMarker(
        marker_id=marker_id,
        placeholder=f"⟦{marker_id}⟧",
        original=original,
        sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
    )


def test_protects_inline_and_block_formulas_with_cell_local_ids():
    source = (
        r"Density ${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$, "
        r"then $x^{2}+\frac{a}{b}$ and $$\max_{i \neq j} a_{ij}$$"
    )

    protected = protect_cell_text(source)

    assert protected.model_text == (
        "Density ⟦M0⟧, then ⟦M1⟧ and ⟦M2⟧"
    )
    assert [value.original for value in protected.formula_markers] == [
        r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$",
        r"$x^{2}+\frac{a}{b}$",
        r"$$\max_{i \neq j} a_{ij}$$",
    ]
    assert restore_cell_segment(
        "密度 ⟦M0⟧，然后 ⟦M1⟧ 和 ⟦M2⟧",
        protected,
    ) == (
        r"密度 ${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$，"
        r"然后 $x^{2}+\frac{a}{b}$ 和 $$\max_{i \neq j} a_{ij}$$"
    )


def test_formula_ids_restart_for_each_cell():
    first = protect_cell_text(r"Alpha $x$")
    second = protect_cell_text(r"Beta $y$")

    assert first.model_text == "Alpha ⟦M0⟧"
    assert second.model_text == "Beta ⟦M0⟧"
    assert first.formula_markers[0].original == "$x$"
    assert second.formula_markers[0].original == "$y$"


@pytest.mark.parametrize(
    ("translated", "message"),
    [
        ("甲", "缺失"),
        ("甲 ⟦M0⟧ ⟦M0⟧", "重复"),
        ("甲 ⟦M0⟧ ⟦M1⟧", "新增"),
        ("甲 ⟦M-0⟧", "篡改"),
        ("甲 $x$", "新增了未保护公式"),
    ],
)
def test_rejects_missing_duplicate_added_or_mutated_formula_marker(
    translated,
    message,
):
    protected = protect_cell_text(r"Alpha $x$")

    with pytest.raises(CellProtectionError, match=message):
        restore_cell_segment(translated, protected)


def test_rejects_formula_marker_order_change():
    protected = protect_cell_text(r"Alpha $x$ Beta $y$")

    with pytest.raises(CellProtectionError, match="顺序"):
        restore_cell_segment("甲 ⟦M1⟧ 乙 ⟦M0⟧", protected)


def test_preserves_backslashes_braces_commands_and_ampersands_exactly():
    formula = (
        r"$$\left\{\frac{\complement A}{"
        r"\max_{i\neqq j}x_{ij}}\right\}&B$$"
    )
    protected = protect_cell_text(f"Value {formula}")

    restored = restore_cell_segment("值 < ⟦M0⟧", protected)

    assert restored == f"值 &lt; {formula}"
    assert restored.encode("utf-8").endswith(formula.encode("utf-8"))


def test_restores_html_markers_exactly_and_rejects_reordering():
    html_markers = (
        marker("H0", '<strong class="x">'),
        marker("H1", "</strong>"),
    )
    protected = protect_cell_text(
        r"⟦H0⟧Value $x$⟦H1⟧",
        html_markers=html_markers,
    )

    assert restore_cell_segment(
        "⟦H0⟧值 ⟦M0⟧⟦H1⟧",
        protected,
    ) == '<strong class="x">值 $x$</strong>'
    with pytest.raises(CellProtectionError, match="结构占位符顺序"):
        restore_cell_segment("⟦H1⟧值 ⟦M0⟧⟦H0⟧", protected)


def test_rejects_reserved_markers_in_source_text():
    with pytest.raises(CellProtectionError, match="保留的公式占位符"):
        protect_cell_text("Literal ⟦M0⟧")

    with pytest.raises(CellProtectionError, match="结构占位符.*新增"):
        protect_cell_text("Literal ⟦H0⟧")


def test_estimates_ascii_and_cjk_tokens_conservatively():
    assert estimate_model_tokens("abcd") == 1
    assert estimate_model_tokens("中文") == 2
    assert estimate_model_tokens("ab中文") == 3


def test_splits_at_break_marker_without_cutting_formula_or_marker():
    protected = protect_cell_text(
        "First sentence ⟦H0⟧Second $x^{2}$ sentence.",
        html_markers=(marker("H0", '<br data-kind="source">'),),
    )

    segments = split_protected_segment(protected, max_tokens=8)

    assert [segment.model_text for segment in segments] == [
        "First sentence ⟦H0⟧",
        "Second ⟦M0⟧ sentence.",
    ]
    assert segments[0].html_markers[0].placeholder == "⟦H0⟧"
    assert segments[1].formula_markers[0].placeholder == "⟦M0⟧"
    assert all(
        not segment.model_text.count("⟦") or "⟧" in segment.model_text
        for segment in segments
    )


def test_does_not_cut_an_unbreakable_formula_marker():
    protected = protect_cell_text(r"Prefix $abcdefghijklmnop$ suffix")

    segments = split_protected_segment(protected, max_tokens=1)
    formula_segment = next(
        segment for segment in segments if "⟦M0⟧" in segment.model_text
    )

    assert restore_cell_segment(
        formula_segment.model_text,
        formula_segment,
    ) == "$abcdefghijklmnop$"


def test_splits_before_exceeding_formula_budget():
    protected = protect_cell_text(
        r"One $a$ two $b$ three $c$ four $d$ five $e$"
    )

    segments = split_protected_segment(
        protected,
        max_tokens=1_000,
        max_formulas=2,
    )

    assert [len(segment.formula_markers) for segment in segments] == [2, 2, 1]
