from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SuspicionMatch:
    rule: str
    message: str
    matched_text: str


@dataclass(frozen=True)
class NormalizationResult:
    normalized_formula: str
    normalization_rules: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.normalization_rules)


class FormulaNormalizer(Protocol):
    def normalize(self, formula: str) -> NormalizationResult:
        ...


_NEQQ_RE = re.compile(r"\\neqq(?![A-Za-z])")
_COMPLEMENT_RE = re.compile(r"\\complement(?![A-Za-z])")
_MAX_RE = re.compile(r"\\max(?![A-Za-z])")
_TEXT_START_RE = re.compile(r"\\text(?![A-Za-z])\s*\{")
_METER_MAX_RE = re.compile(
    r"Meter\s+Ma\s*(?:\^\s*\{\s*)?"
    r"\\max(?![A-Za-z])(?:\s*\})?"
)
_INSTRUMENT_RE = re.compile(
    r"(?<![A-Za-z])(?:F|H)\s+I\s+C(?![A-Za-z])"
)
_FRACTION_RE = re.compile(
    r"\\frac\s*\{\s*m\s+3\s*\}\s*\{\s*h\s*\}"
)

_MESSAGES = {
    "mineru_neqq": "疑似 MinerU 将 C7 等内容误识别为 \\neqq",
    "mineru_complement": (
        "疑似 MinerU 将 C4 等内容误识别为 \\complement"
    ),
    "max_inside_text": "\\text{...} 内出现可疑的 \\max",
    "split_meter_max": "疑似 MeterMax 被拆分并误识别为 \\max",
    "split_instrument_tag": "疑似仪表位号被拆成带空格的字符",
    "split_cubic_meter_fraction": (
        "疑似立方米每小时单位被误识别为 \\frac{m 3}{h}"
    ),
}

_NORMALIZE_COMPLEMENT_SUBSCRIPT_RE = re.compile(
    r"\\complement(?![A-Za-z])\s*_\s*"
    r"\{\s*(?P<number>\d+)\s*\}"
)
_NORMALIZE_DEGREE_COMPLEMENT_RE = re.compile(
    r"(?P<degree>\^\s*\{\s*\\circ(?![A-Za-z])\s*\})"
    r"\s*\\complement(?![A-Za-z])"
)


def _carbon_with_subscript(name: str) -> str:
    return (
        rf"(?:\\mathsf\s*\{{\s*C\s*\}}|C)\s*_\s*"
        rf"\{{\s*(?P<{name}>\d+)\s*\}}"
    )


_NORMALIZE_ALKENE_PAIR_RE = re.compile(
    _carbon_with_subscript("left")
    + r"\s*\\neqq(?![A-Za-z])\s*/\s*"
    + _carbon_with_subscript("right")
    + r"\s*\\neq(?:q)?(?![A-Za-z])"
)


def _split_letters(value: str) -> str:
    return r"\s+".join(re.escape(character) for character in value)


def _text_field_pattern(body: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![A-Za-z])\\text\s*\{\s*(?:"
        + body
        + r")\s*\}(?![A-Za-z])"
    )


def _bare_field_pattern(body: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![A-Za-z])(?:" + body + r")(?![A-Za-z])"
    )


_METER_MAX_BODY = (
    _split_letters("MeterMa")
    + r"\s+(?:x|\\max(?![A-Za-z])|"
    r"\^\s*\{\s*(?:\\max(?![A-Za-z])|"
    + _split_letters("max")
    + r")\s*\})"
)
_NORMALIZE_EQUATION_LABEL_RE = re.compile(
    r"(?<![A-Za-z])\\text\s*\{\s*"
    + _split_letters("Equation")
    + r"\s+(?P<number>\d+)\s*\}(?![A-Za-z])"
)
_NORMALIZE_TEXT_FIELD_PATTERNS = (
    (
        _text_field_pattern(_METER_MAX_BODY),
        r"\mathrm{MeterMax}",
    ),
    (
        _text_field_pattern(_split_letters("SetPoint")),
        r"\mathrm{SetPoint}",
    ),
    (
        _text_field_pattern(_split_letters("Output")),
        r"\mathrm{Output}",
    ),
    (
        _text_field_pattern(_split_letters("Equation")),
        r"\mathrm{Equation}",
    ),
)
_NORMALIZE_BARE_FIELD_PATTERNS = (
    (
        _bare_field_pattern(_METER_MAX_BODY),
        r"\mathrm{MeterMax}",
    ),
    (
        _bare_field_pattern(_split_letters("SetPoint")),
        r"\mathrm{SetPoint}",
    ),
    (
        _bare_field_pattern(_split_letters("Output")),
        r"\mathrm{Output}",
    ),
    (
        _bare_field_pattern(_split_letters("Equation")),
        r"\mathrm{Equation}",
    ),
)
_NORMALIZE_INSTRUMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<tag>[FH])\s+I\s+C\s+"
    r"(?P<digits>\d(?:[ \t\r\n]*\d)*)(?![A-Za-z0-9])"
)
_NORMALIZE_UNIT_RE = re.compile(
    r"\\frac(?![A-Za-z])\s*\{\s*m\s+3\s*\}"
    r"\s*\{\s*h\s*\}"
)


def _apply_patterns(
    source: str,
    patterns: tuple[tuple[re.Pattern[str], str], ...],
) -> tuple[str, int]:
    result = source
    total = 0
    for pattern, replacement in patterns:
        result, count = pattern.subn(
            lambda _match, value=replacement: value,
            result,
        )
        total += count
    return result, total


def _text_group_ranges(
    formula: str,
) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    covered_until = 0
    for match in _TEXT_START_RE.finditer(formula):
        if match.start() < covered_until:
            continue
        opening = match.end() - 1
        depth = 1
        position = opening + 1
        while position < len(formula):
            character = formula[position]
            if character == "{" and not _is_escaped(formula, position):
                depth += 1
            elif (
                character == "}"
                and not _is_escaped(formula, position)
            ):
                depth -= 1
                if depth == 0:
                    ranges.append((match.start(), position + 1))
                    covered_until = position + 1
                    break
            position += 1
    return tuple(ranges)


def _replace_bare_fields_outside_text(
    formula: str,
) -> tuple[str, int]:
    parts: list[str] = []
    total = 0
    position = 0
    for start, end in _text_group_ranges(formula):
        replaced, count = _apply_patterns(
            formula[position:start],
            _NORMALIZE_BARE_FIELD_PATTERNS,
        )
        parts.extend((replaced, formula[start:end]))
        total += count
        position = end
    replaced, count = _apply_patterns(
        formula[position:],
        _NORMALIZE_BARE_FIELD_PATTERNS,
    )
    parts.append(replaced)
    total += count
    return "".join(parts), total


class DeterministicFormulaNormalizer:
    def normalize(self, formula: str) -> NormalizationResult:
        normalized = formula
        rules: list[str] = []

        normalized, count = _NORMALIZE_COMPLEMENT_SUBSCRIPT_RE.subn(
            lambda match: rf"\mathrm{{C}}_{{{match['number']}}}",
            normalized,
        )
        if count:
            rules.append("mineru_complement_subscript_c")

        normalized, count = _NORMALIZE_DEGREE_COMPLEMENT_RE.subn(
            lambda match: match["degree"] + r" \mathrm{C}",
            normalized,
        )
        if count:
            rules.append("mineru_degree_complement_c")

        normalized, count = _NORMALIZE_ALKENE_PAIR_RE.subn(
            lambda match: (
                rf"\mathrm{{C}}_{{{match['left']}}}^{{=}} / "
                rf"\mathrm{{C}}_{{{match['right']}}}^{{=}}"
            ),
            normalized,
        )
        if count:
            rules.append("alkene_carbon_count_equality_pair")

        normalized, field_count = _NORMALIZE_EQUATION_LABEL_RE.subn(
            lambda match: (
                r"\mathrm{Equation\,"
                + match["number"]
                + "}"
            ),
            normalized,
        )
        normalized, count = _apply_patterns(
            normalized,
            _NORMALIZE_TEXT_FIELD_PATTERNS,
        )
        field_count += count
        normalized, count = _replace_bare_fields_outside_text(
            normalized
        )
        field_count += count
        if field_count:
            rules.append("fixed_process_control_field")

        normalized, count = _NORMALIZE_INSTRUMENT_RE.subn(
            lambda match: (
                r"\mathrm{"
                + match["tag"]
                + "IC"
                + re.sub(r"\s+", "", match["digits"])
                + "}"
            ),
            normalized,
        )
        if count:
            rules.append("numbered_instrument_tag")

        normalized, count = _NORMALIZE_UNIT_RE.subn(
            lambda _match: r"\frac{\mathrm{m}^{3}}{\mathrm{h}}",
            normalized,
        )
        if count:
            rules.append("cubic_metre_per_hour")

        return NormalizationResult(
            normalized_formula=normalized,
            normalization_rules=tuple(rules),
        )


def _is_escaped(source: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and source[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _text_groups(formula: str) -> tuple[str, ...]:
    return tuple(
        formula[start:end]
        for start, end in _text_group_ranges(formula)
    )


def _matches(
    formula: str,
    pattern: re.Pattern[str],
    rule: str,
) -> list[SuspicionMatch]:
    return [
        SuspicionMatch(
            rule=rule,
            message=_MESSAGES[rule],
            matched_text=match.group(0),
        )
        for match in pattern.finditer(formula)
    ]


def find_suspicious_formula(
    formula: str,
) -> tuple[SuspicionMatch, ...]:
    matches: list[SuspicionMatch] = []
    matches.extend(_matches(formula, _NEQQ_RE, "mineru_neqq"))
    matches.extend(
        _matches(
            formula,
            _COMPLEMENT_RE,
            "mineru_complement",
        )
    )
    for group in _text_groups(formula):
        for maximum in _MAX_RE.finditer(group):
            matches.append(
                SuspicionMatch(
                    rule="max_inside_text",
                    message=_MESSAGES["max_inside_text"],
                    matched_text=maximum.group(0),
                )
            )
    matches.extend(
        _matches(formula, _METER_MAX_RE, "split_meter_max")
    )
    matches.extend(
        _matches(
            formula,
            _INSTRUMENT_RE,
            "split_instrument_tag",
        )
    )
    matches.extend(
        _matches(
            formula,
            _FRACTION_RE,
            "split_cubic_meter_fraction",
        )
    )
    return tuple(matches)
