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
    normalized_formula: str | None
    normalization_rule: str | None
    confidence: float | None


class FormulaNormalizer(Protocol):
    def normalize(self, raw_formula: str) -> NormalizationResult:
        ...


class NoOpFormulaNormalizer:
    def normalize(self, raw_formula: str) -> NormalizationResult:
        return NormalizationResult(
            normalized_formula=None,
            normalization_rule=None,
            confidence=None,
        )


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


def _is_escaped(source: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and source[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _text_groups(formula: str) -> tuple[str, ...]:
    groups: list[str] = []
    for match in _TEXT_START_RE.finditer(formula):
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
                    groups.append(formula[match.start() : position + 1])
                    break
            position += 1
    return tuple(groups)


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
