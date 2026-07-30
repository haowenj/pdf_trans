import pytest

from pdf_trans.formula_rules import (
    NormalizationResult,
    NoOpFormulaNormalizer,
    find_suspicious_formula,
)


@pytest.mark.parametrize(
    ("formula", "rule"),
    [
        ("C_7 \\neqq C_6", "mineru_neqq"),
        ("C_4 \\complement C_3", "mineru_complement"),
        ("\\text{Meter Ma \\max}", "max_inside_text"),
        ("Meter Ma \\max", "split_meter_max"),
        ("Meter Ma ^{\\max}", "split_meter_max"),
        ("F I C / H I C", "split_instrument_tag"),
        ("\\frac{m 3}{h}", "split_cubic_meter_fraction"),
    ],
)
def test_marks_known_mineru_corruptions(formula, rule):
    assert rule in {
        match.rule for match in find_suspicious_formula(formula)
    }


@pytest.mark.parametrize(
    "formula",
    [
        "Meter   Ma   \\max",
        "Meter Ma ^ { \\max }",
        "Meter\nMa^{\\max}",
    ],
)
def test_split_meter_max_accepts_whitespace_variants(formula):
    assert "split_meter_max" in {
        match.rule for match in find_suspicious_formula(formula)
    }


def test_text_rule_finds_max_in_balanced_nested_group():
    matches = find_suspicious_formula(
        "\\text{Meter {maximum} setting \\max} + x"
    )

    assert "max_inside_text" in {match.rule for match in matches}


def test_instrument_rule_records_fic_and_hic_separately():
    matches = [
        match
        for match in find_suspicious_formula("F I C / H I C")
        if match.rule == "split_instrument_tag"
    ]

    assert [match.matched_text for match in matches] == ["F I C", "H I C"]


@pytest.mark.parametrize(
    "formula",
    [
        "\\frac { m   3 } { h }",
        "\\frac{m\n3}{ h}",
    ],
)
def test_fraction_rule_accepts_whitespace_variants(formula):
    assert "split_cubic_meter_fraction" in {
        match.rule for match in find_suspicious_formula(formula)
    }


@pytest.mark.parametrize(
    "formula",
    [
        "C_7 \\neq C_6",
        "\\neqquality",
        "\\neqqquality",
        "\\complementary",
        "\\text{maximum}",
        "\\text{unclosed \\max",
        "AF I C",
        "F I CX",
        "\\frac{m^3}{h}",
    ],
)
def test_does_not_mark_safe_or_out_of_boundary_content(formula):
    assert find_suspicious_formula(formula) == ()


def test_genuine_neq_is_not_marked_or_normalized():
    raw = "C_7 \\neq C_6"

    assert find_suspicious_formula(raw) == ()
    assert NoOpFormulaNormalizer().normalize(raw) == NormalizationResult(
        normalized_formula=None,
        normalization_rule=None,
        confidence=None,
    )
    assert raw == "C_7 \\neq C_6"
