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


def test_escaped_dollar_and_unclosed_delimiter_remain_plain_text():
    source = r"Cost \$5 and an unclosed $value"
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
        (
            lambda text, values: text.replace(values[0], "__FIRST__", 1)
            .replace(values[1], values[0], 1)
            .replace("__FIRST__", values[1], 1),
            "顺序",
        ),
    ],
)
def test_rejects_invalid_placeholder_responses(mutate, message):
    context, protected, placeholders = _protected_pair()

    with pytest.raises(FormulaProtectionError, match=message):
        context.restore(mutate(protected.model_text, placeholders), protected)


def test_rejects_model_generated_formula_before_restoration():
    context = FormulaProtectionContext(nonce="0123456789abcdef")
    protected = context.protect("Value $x$")

    with pytest.raises(FormulaProtectionError, match="新增公式"):
        context.restore(protected.model_text + " and $y$", protected)


def test_rejects_reserved_sentinel_in_source():
    context = FormulaProtectionContext()

    with pytest.raises(FormulaProtectionError, match="保留哨兵"):
        context.protect("source PDFTRANS_FORMULA marker")
