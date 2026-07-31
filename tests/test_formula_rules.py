import pytest

from pdf_trans.formula_rules import (
    DeterministicFormulaNormalizer,
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
    result = DeterministicFormulaNormalizer().normalize(raw)
    assert result.normalized_formula == raw
    assert result.normalization_rules == ()
    assert result.changed is False
    assert raw == "C_7 \\neq C_6"


@pytest.mark.parametrize(
    ("raw", "expected", "rules"),
    [
        (
            r"{ \complement _ { 4 } } ^ { = }",
            r"{ \mathrm{C}_{4} } ^ { = }",
            ("mineru_complement_subscript_c",),
        ),
        (
            r"20 5 ^ { \circ } \complement",
            r"20 5 ^ { \circ } \mathrm{C}",
            ("mineru_degree_complement_c",),
        ),
        (
            r"\complement _ { 2 } . \complement _ { 7 }",
            r"\mathrm{C}_{2} . \mathrm{C}_{7}",
            ("mineru_complement_subscript_c",),
        ),
        (
            r"\scriptstyle \mathsf { C } _ { 7 } \neqq / "
            r"\mathsf { C } _ { 8 } \neq",
            r"\scriptstyle \mathrm{C}_{7}^{=} / "
            r"\mathrm{C}_{8}^{=}",
            ("alkene_carbon_count_equality_pair",),
        ),
        (
            r"\text {M e t e r M a \max}",
            r"\mathrm{MeterMax}",
            ("fixed_process_control_field",),
        ),
        (
            r"\text {M e t e r M a ^ {\max}}",
            r"\mathrm{MeterMax}",
            ("fixed_process_control_field",),
        ),
        (
            r"\text {M e t e r M a ^ {m a x}}",
            r"\mathrm{MeterMax}",
            ("fixed_process_control_field",),
        ),
        (
            r"F I C 0 0 1 2 + H I C 0 0 2 1",
            r"\mathrm{FIC0012} + \mathrm{HIC0021}",
            ("numbered_instrument_tag",),
        ),
        (
            r"\frac {m 3}{h}",
            r"\frac{\mathrm{m}^{3}}{\mathrm{h}}",
            ("cubic_metre_per_hour",),
        ),
    ],
)
def test_deterministic_normalizer_applies_whitelist(
    raw,
    expected,
    rules,
):
    result = DeterministicFormulaNormalizer().normalize(raw)

    assert result.normalized_formula == expected
    assert result.normalization_rules == rules
    assert result.changed is True


def test_normalizer_applies_multiple_rules_in_order():
    raw = (
        r"F I C 0 0 1 2 _ {\text {S e t P o i n t}} "
        r"\frac {m 3}{h} + "
        r"H I C 0 0 2 1 _ {\text {O u t p u t}} + "
        r"\text {E q u a t i o n 1} + "
        r"\text {M e t e r M a ^ {\max}}"
    )

    result = DeterministicFormulaNormalizer().normalize(raw)

    assert result.normalized_formula == (
        r"\mathrm{FIC0012} _ {\mathrm{SetPoint}} "
        r"\frac{\mathrm{m}^{3}}{\mathrm{h}} + "
        r"\mathrm{HIC0021} _ {\mathrm{Output}} + "
        r"\mathrm{Equation\,1} + \mathrm{MeterMax}"
    )
    assert result.normalization_rules == (
        "fixed_process_control_field",
        "numbered_instrument_tag",
        "cubic_metre_per_hour",
    )


@pytest.mark.parametrize(
    "formula",
    [
        r"A \complement B",
        r"x \neq y",
        r"C_7 \neqq C_8",
        r"F I C",
        r"Range of F I C",
        r"\frac{\mathrm{m}^{3}}{\mathrm{h}}",
        r"\bar{\Pi} + \mathfrak{A}",
    ],
)
def test_normalizer_does_not_change_outside_whitelist(formula):
    result = DeterministicFormulaNormalizer().normalize(formula)

    assert result.normalized_formula == formula
    assert result.normalization_rules == ()
    assert result.changed is False


def test_normalizer_is_idempotent():
    normalizer = DeterministicFormulaNormalizer()
    first = normalizer.normalize(
        r"F I C 0 0 1 2 + \frac {m 3}{h}"
    )

    second = normalizer.normalize(first.normalized_formula)

    assert second.normalized_formula == first.normalized_formula
    assert second.normalization_rules == ()
