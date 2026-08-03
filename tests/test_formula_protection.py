import re

import pytest

from pdf_trans.formula_protection import (
    FormulaProtectionContext,
    FormulaProtectionError,
)


PLACEHOLDER_RE = re.compile(
    r"⟪PDFTRANS_FORMULA:[0-9a-f]{16}:\d{4}:[0-9a-f]{64}⟫"
)


def test_protects_single_and_multiple_inline_formulas_and_restores_exactly():
    source = (
        r"At $x^{2}+\mathrm{kg}/\mathrm{m}^{3}$ and "
        r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$."
    )
    context = FormulaProtectionContext()

    protected = context.protect(source)

    assert "$x" not in protected.model_text
    assert r"\mathrm" not in protected.model_text
    assert len(PLACEHOLDER_RE.findall(protected.model_text)) == 2
    translated = protected.model_text.replace("At ", "在 ").replace(
        " and ", " 和 "
    )
    assert context.restore(translated, protected) == (
        r"在 $x^{2}+\mathrm{kg}/\mathrm{m}^{3}$ 和 "
        r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$."
    )


def test_protects_multiline_block_formula_and_preserves_every_character():
    formula = "$$\nE = mc^{2} + \\frac{a}{b}\n$$"
    source = f"Before\n{formula}\nAfter"
    context = FormulaProtectionContext()

    protected = context.protect(source)
    restored = context.restore(
        protected.model_text.replace("Before", "之前").replace("After", "之后"),
        protected,
    )

    assert formula not in protected.model_text
    assert restored == f"之前\n{formula}\n之后"


def test_body_protection_leaves_amounts_and_protects_a_neighboring_formula():
    source = r"Cost rose from $5 million to $10 million; formula $x$."
    context = FormulaProtectionContext(nonce="0123456789abcdef")

    protected = context.protect(source)

    assert protected.model_text.count("$5 million") == 1
    assert protected.model_text.count("$10 million") == 1
    assert len(PLACEHOLDER_RE.findall(protected.model_text)) == 1
    assert context.restore(protected.model_text, protected) == source


def test_protects_and_restores_all_supported_formula_boundaries_exactly():
    source = r"Inline $a_b$, block $$c_d$$, paren \(e_f\), bracket \[g_h\]."
    context = FormulaProtectionContext(nonce="0123456789abcdef")

    protected = context.protect(source)

    assert len(PLACEHOLDER_RE.findall(protected.model_text)) == 4
    assert all(
        formula not in protected.model_text
        for formula in ("$a_b$", "$$c_d$$", r"\(e_f\)", r"\[g_h\]")
    )
    translated = protected.model_text.replace("Inline", "行内").replace(
        "block", "块级"
    )
    assert context.restore(translated, protected) == source.replace(
        "Inline", "行内"
    ).replace("block", "块级")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda text, values: text.replace(values[0], "", 1), "缺失"),
        (
            lambda text, values: text.replace(
                values[0],
                values[0].replace(
                    "0123456789abcdef", "fedcba9876543210"
                ),
                1,
            ),
            "篡改",
        ),
    ],
)
def test_rejects_invalid_backslash_formula_placeholders(mutate, message):
    context = FormulaProtectionContext(nonce="0123456789abcdef")
    protected = context.protect(r"Left \(x\) middle \[y\] right")
    placeholders = PLACEHOLDER_RE.findall(protected.model_text)

    assert len(placeholders) == 2
    with pytest.raises(FormulaProtectionError, match=message):
        context.restore(mutate(protected.model_text, placeholders), protected)


def test_escaped_dollar_and_unclosed_delimiter_remain_plain_text():
    source = r"Cost \$5 and an unclosed $value"
    context = FormulaProtectionContext()

    protected = context.protect(source)

    assert protected.model_text == source
    assert protected.formula_ids == ()
    assert context.restore(source, protected) == source


@pytest.mark.parametrize(
    "source",
    [
        r"escaped \$x\$",
        r"escaped \$\$x\$\$",
        r"escaped \\(x\\)",
        r"escaped \\[x\\]",
        "unclosed $x",
        "unclosed $$x",
        r"unclosed \(x",
        r"unclosed \[x",
    ],
)
def test_body_protection_leaves_escaped_and_unclosed_boundaries_as_text(source):
    context = FormulaProtectionContext()

    protected = context.protect(source)

    assert protected.model_text == source
    assert protected.formula_ids == ()
    assert context.restore(source, protected) == source


def _protected_pair():
    context = FormulaProtectionContext(nonce="0123456789abcdef")
    protected = context.protect("Left $x$ middle $y$ right")
    placeholders = PLACEHOLDER_RE.findall(protected.model_text)
    return context, protected, placeholders


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda text, values: text.replace(values[0], ""), "缺失"),
        (lambda text, values: text + values[0], "重复"),
        (
            lambda text, values: text
            + (
                "⟪PDFTRANS_FORMULA:0123456789abcdef:9999:"
                + "0" * 64
                + "⟫"
            ),
            "新增",
        ),
        (
            lambda text, values: text.replace(
                values[0],
                values[0].replace(
                    "0123456789abcdef", "fedcba9876543210"
                ),
            ),
            "篡改",
        ),
    ],
)
def test_rejects_invalid_placeholder_responses(mutate, message):
    context, protected, placeholders = _protected_pair()

    with pytest.raises(FormulaProtectionError, match=message):
        context.restore(mutate(protected.model_text, placeholders), protected)


def test_allows_placeholder_order_change_and_restores_by_id():
    context = FormulaProtectionContext(nonce="0123456789abcdef")
    protected = context.protect(r"Left \(x\) middle \[y\] right")
    placeholders = PLACEHOLDER_RE.findall(protected.model_text)
    reordered = (
        protected.model_text.replace(placeholders[0], "__FIRST__", 1)
        .replace(placeholders[1], placeholders[0], 1)
        .replace("__FIRST__", placeholders[1], 1)
    )

    assert context.restore(reordered, protected) == (
        r"Left \[y\] middle \(x\) right"
    )


def test_placeholder_validation_error_exposes_diagnostics():
    context = FormulaProtectionContext(nonce="0123456789abcdef")
    protected = context.protect("Left $x$ middle $y$")
    placeholders = PLACEHOLDER_RE.findall(protected.model_text)
    unknown = (
        "⟪PDFTRANS_FORMULA:0123456789abcdef:9999:"
        + "0" * 64
        + "⟫"
    )

    with pytest.raises(FormulaProtectionError) as error:
        context.restore(
            protected.model_text.replace(placeholders[0], "", 1) + unknown,
            protected,
        )

    assert error.value.expected_placeholders == tuple(placeholders)
    assert error.value.actual_placeholders == (placeholders[1], unknown)
    assert error.value.missing_placeholders == (placeholders[0],)
    assert error.value.unknown_placeholders == (unknown,)


def test_rejects_model_generated_formula_before_restoration():
    context = FormulaProtectionContext(nonce="0123456789abcdef")
    protected = context.protect("Value $x$")

    with pytest.raises(FormulaProtectionError, match="新增公式"):
        context.restore(protected.model_text + " and $y$", protected)


def test_rejects_reserved_sentinel_in_source():
    context = FormulaProtectionContext()

    with pytest.raises(FormulaProtectionError, match="保留哨兵"):
        context.protect("source PDFTRANS_FORMULA marker")
