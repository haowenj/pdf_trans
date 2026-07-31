from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from pdf_trans.errors import FormulaAuditError
from pdf_trans.formula_rules import (
    DeterministicFormulaNormalizer,
    FormulaNormalizer,
    NormalizationResult,
    find_suspicious_formula,
)
from pdf_trans.formula_scanner import (
    FormulaCandidate,
    rebuild_raw_formula,
    scan_content_list,
)
from pdf_trans.formula_validation import (
    KATEX_CONFIG,
    KATEX_VERSION,
    FormulaValidator,
    KaTeXFormulaValidator,
    ValidationInput,
    ValidationResult,
)


LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = 2
_FORMULA_ID_RE = re.compile(r"formula_[0-9a-f]{24}")
_CONTENT_HASH_RE = re.compile(r"[0-9a-f]{64}")
_REPORT_KEYS = {
    "schema_version",
    "validator",
    "summary",
    "problem_page_indices",
    "invalid_syntax_page_indices",
    "suspicious_page_indices",
    "formulas",
    "issues",
}
_V1_SUMMARY_KEYS = {
    "total_formulas",
    "valid_count",
    "invalid_syntax_count",
    "suspicious_count",
    "issue_formula_count",
}
_SUMMARY_KEYS = _V1_SUMMARY_KEYS | {
    "scanned_formula_count",
    "matched_formula_count",
    "normalization_accepted_count",
    "normalization_rejected_count",
}
_V1_RECORD_KEYS = {
    "formula_id",
    "page_idx",
    "bbox",
    "source_type",
    "field_path",
    "table_row_idx",
    "table_col_idx",
    "cell_tag",
    "formula_index",
    "raw_formula",
    "is_block",
    "content_hash",
    "syntax_status",
    "suspicious",
    "statuses",
    "suspicion_rules",
    "validation_error",
    "normalized_formula",
    "normalization_rule",
    "confidence",
}
_RECORD_KEYS = (
    _V1_RECORD_KEYS
    - {
        "normalization_rule",
        "confidence",
    }
    | {
        "raw_validation_error",
        "normalization_rules",
        "normalization_status",
    }
)


class LegacyFormulaAuditError(FormulaAuditError):
    pass


@dataclass(frozen=True)
class FormulaAuditStats:
    total_formulas: int
    scanned_formula_count: int
    valid_count: int
    invalid_syntax_count: int
    suspicious_count: int
    issue_formula_count: int
    matched_formula_count: int
    normalization_accepted_count: int
    normalization_rejected_count: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaAuditReport:
    stats: FormulaAuditStats
    problem_page_indices: tuple[int, ...]
    invalid_syntax_page_indices: tuple[int, ...]
    suspicious_page_indices: tuple[int, ...]
    payload: dict[str, object]

    @property
    def accepted_replacements(self) -> dict[tuple[str, bool], str]:
        replacements: dict[tuple[str, bool], str] = {}
        decisions: dict[
            tuple[str, bool],
            tuple[str, str],
        ] = {}
        formulas = self.payload["formulas"]
        if not isinstance(formulas, list):
            raise FormulaAuditError("公式审计报告公式记录无效")
        for record in formulas:
            if not isinstance(record, dict):
                raise FormulaAuditError("公式审计报告公式记录无效")
            status = record.get("normalization_status")
            if status == "not_applicable":
                continue
            raw_formula = record.get("raw_formula")
            is_block = record.get("is_block")
            normalized_formula = record.get("normalized_formula")
            if (
                status not in {"accepted", "rejected"}
                or not isinstance(raw_formula, str)
                or type(is_block) is not bool
                or not isinstance(normalized_formula, str)
            ):
                raise FormulaAuditError("公式审计报告公式记录无效")
            key = (raw_formula, is_block)
            decision = (normalized_formula, status)
            existing = decisions.get(key)
            if existing is not None and existing != decision:
                raise FormulaAuditError("同身份公式修复决定存在冲突")
            decisions[key] = decision
            if status == "accepted":
                replacements[key] = normalized_formula
        return replacements


@dataclass(frozen=True)
class _NormalizationDecision:
    result: NormalizationResult
    normalized_raw_formula: str | None
    normalization_status: Literal[
        "not_applicable",
        "accepted",
        "rejected",
    ]
    validation_error: str | None


def _checked_validation_results(
    inputs: tuple[ValidationInput, ...],
    validator: FormulaValidator,
) -> tuple[ValidationResult, ...]:
    if not inputs:
        return ()
    results = validator.validate_batch(inputs)
    if len(results) != len(inputs):
        raise FormulaAuditError("公式校验结果数量不一致")
    for expected, result in zip(inputs, results):
        if (
            not isinstance(result, ValidationResult)
            or result.formula_id != expected.formula_id
        ):
            raise FormulaAuditError("公式校验结果顺序不一致")
        if result.syntax_status == "valid":
            if result.validation_error is not None:
                raise FormulaAuditError("有效公式包含校验错误")
        elif result.syntax_status == "invalid_syntax":
            if (
                not isinstance(result.validation_error, str)
                or not result.validation_error
            ):
                raise FormulaAuditError("无效公式缺少校验错误")
        else:
            raise FormulaAuditError("公式校验状态无效")
    return tuple(results)


def _normalization_decisions(
    candidates: tuple[FormulaCandidate, ...],
    normalizer: FormulaNormalizer,
    validator: FormulaValidator,
) -> tuple[_NormalizationDecision, ...]:
    normalization_results: list[NormalizationResult] = []
    normalized_inputs: list[ValidationInput] = []
    for candidate in candidates:
        result = normalizer.normalize(candidate.katex_formula)
        if (
            not isinstance(result, NormalizationResult)
            or not isinstance(result.normalized_formula, str)
            or not isinstance(result.normalization_rules, tuple)
            or any(
                not isinstance(rule, str) or not rule
                for rule in result.normalization_rules
            )
            or len(set(result.normalization_rules))
            != len(result.normalization_rules)
            or result.changed
            != (
                result.normalized_formula
                != candidate.katex_formula
            )
        ):
            raise FormulaAuditError("公式规范化结果无效")
        normalization_results.append(result)
        if result.changed:
            normalized_inputs.append(
                ValidationInput(
                    formula_id=candidate.formula_id,
                    formula=result.normalized_formula,
                    is_block=candidate.is_block,
                )
            )

    normalized_validations = _checked_validation_results(
        tuple(normalized_inputs),
        validator,
    )
    validations_by_id = {
        value.formula_id: value for value in normalized_validations
    }
    decisions: list[_NormalizationDecision] = []
    for candidate, result in zip(
        candidates,
        normalization_results,
    ):
        if not result.changed:
            decisions.append(
                _NormalizationDecision(
                    result=result,
                    normalized_raw_formula=None,
                    normalization_status="not_applicable",
                    validation_error=None,
                )
            )
            continue
        validation = validations_by_id[candidate.formula_id]
        decisions.append(
            _NormalizationDecision(
                result=result,
                normalized_raw_formula=rebuild_raw_formula(
                    candidate,
                    result.normalized_formula,
                ),
                normalization_status=(
                    "accepted"
                    if validation.syntax_status == "valid"
                    else "rejected"
                ),
                validation_error=validation.validation_error,
            )
        )
    return tuple(decisions)


def _record(
    candidate: FormulaCandidate,
    raw_validation: ValidationResult,
    decision: _NormalizationDecision,
) -> dict[str, object]:
    matches = find_suspicious_formula(candidate.katex_formula)
    statuses = [raw_validation.syntax_status]
    if matches:
        statuses.append("suspicious")
    return {
        "formula_id": candidate.formula_id,
        "page_idx": candidate.page_idx,
        "bbox": candidate.bbox,
        "source_type": candidate.source_type,
        "field_path": candidate.field_path,
        "table_row_idx": candidate.table_row_idx,
        "table_col_idx": candidate.table_col_idx,
        "cell_tag": candidate.cell_tag,
        "formula_index": candidate.formula_index,
        "raw_formula": candidate.raw_formula,
        "is_block": candidate.is_block,
        "content_hash": candidate.content_hash,
        "syntax_status": raw_validation.syntax_status,
        "suspicious": bool(matches),
        "statuses": statuses,
        "suspicion_rules": [
            {
                "rule": match.rule,
                "message": match.message,
                "matched_text": match.matched_text,
            }
            for match in matches
        ],
        "raw_validation_error": raw_validation.validation_error,
        "normalized_formula": decision.normalized_raw_formula,
        "normalization_rules": list(
            decision.result.normalization_rules
        ),
        "normalization_status": decision.normalization_status,
        "validation_error": decision.validation_error,
    }


def _page_indices(
    records: list[dict[str, object]],
    predicate,
) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                page
                for value in records
                if predicate(value)
                and type(page := value["page_idx"]) is int
            }
        )
    )


def _build_report(
    candidates: tuple[FormulaCandidate, ...],
    raw_validations: tuple[ValidationResult, ...],
    decisions: tuple[_NormalizationDecision, ...],
) -> FormulaAuditReport:
    records = [
        _record(candidate, validation, decision)
        for candidate, validation, decision in zip(
            candidates,
            raw_validations,
            decisions,
        )
    ]
    issues = [
        value
        for value in records
        if value["syntax_status"] == "invalid_syntax"
        or value["suspicious"]
        or value["normalization_status"] in {"accepted", "rejected"}
    ]
    invalid_pages = _page_indices(
        records,
        lambda value: value["syntax_status"] == "invalid_syntax",
    )
    suspicious_pages = _page_indices(
        records,
        lambda value: bool(value["suspicious"]),
    )
    problem_pages = _page_indices(
        records,
        lambda value: (
            value["syntax_status"] == "invalid_syntax"
            or bool(value["suspicious"])
            or value["normalization_status"] in {
                "accepted",
                "rejected",
            }
        ),
    )
    stats = FormulaAuditStats(
        total_formulas=len(records),
        scanned_formula_count=len(records),
        valid_count=sum(
            value["syntax_status"] == "valid" for value in records
        ),
        invalid_syntax_count=sum(
            value["syntax_status"] == "invalid_syntax"
            for value in records
        ),
        suspicious_count=sum(
            bool(value["suspicious"]) for value in records
        ),
        issue_formula_count=len(issues),
        matched_formula_count=sum(
            value["normalization_status"] in {"accepted", "rejected"}
            for value in records
        ),
        normalization_accepted_count=sum(
            value["normalization_status"] == "accepted"
            for value in records
        ),
        normalization_rejected_count=sum(
            value["normalization_status"] == "rejected"
            for value in records
        ),
    )
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "validator": {
            "name": "katex",
            "version": KATEX_VERSION,
            "config": dict(KATEX_CONFIG),
        },
        "summary": stats.to_dict(),
        "problem_page_indices": list(problem_pages),
        "invalid_syntax_page_indices": list(invalid_pages),
        "suspicious_page_indices": list(suspicious_pages),
        "formulas": records,
        "issues": issues,
    }
    report = FormulaAuditReport(
        stats=stats,
        problem_page_indices=problem_pages,
        invalid_syntax_page_indices=invalid_pages,
        suspicious_page_indices=suspicious_pages,
        payload=payload,
    )
    report.accepted_replacements
    return report


def _write_report_atomic(
    output: Path,
    payload: dict[str, object],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, FormulaAuditError):
            raise
        raise FormulaAuditError(
            f"无法写入公式审计报告：{exc}"
        ) from exc


def log_formula_audit_summary(
    report: FormulaAuditReport,
    logger: logging.Logger = LOGGER,
) -> None:
    logger.info(
        "公式审计统计：总公式 %d，语法失败 %d，可疑公式 %d",
        report.stats.total_formulas,
        report.stats.invalid_syntax_count,
        report.stats.suspicious_count,
    )
    logger.info(
        "公式语法失败 page_idx：%s",
        list(report.invalid_syntax_page_indices),
    )
    logger.info(
        "可疑公式 page_idx：%s",
        list(report.suspicious_page_indices),
    )
    logger.info(
        "公式修复统计：扫描 %d，命中 %d，accepted %d，rejected %d",
        report.stats.scanned_formula_count,
        report.stats.matched_formula_count,
        report.stats.normalization_accepted_count,
        report.stats.normalization_rejected_count,
    )
    formulas = report.payload["formulas"]
    if not isinstance(formulas, list):
        raise FormulaAuditError("公式审计报告公式记录无效")
    for record in formulas:
        if (
            not isinstance(record, dict)
            or record.get("normalization_status")
            == "not_applicable"
        ):
            continue
        logger.info(
            "公式修复明细：formula_id=%s page_idx=%s status=%s "
            "rules=%s raw=%s normalized=%s validation_error=%s",
            record.get("formula_id"),
            record.get("page_idx"),
            record.get("normalization_status"),
            record.get("normalization_rules"),
            record.get("raw_formula"),
            record.get("normalized_formula"),
            record.get("validation_error"),
        )


def audit_content_list_file(
    source: Path,
    output: Path,
    *,
    validator: FormulaValidator | None = None,
    normalizer: FormulaNormalizer | None = None,
) -> FormulaAuditReport:
    if source.resolve() == output.resolve():
        raise FormulaAuditError("公式审计报告不能覆盖 MinerU 原始文件")
    try:
        raw = source.read_bytes()
        items = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise FormulaAuditError(
            f"公式审计源文件不是 UTF-8：{exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise FormulaAuditError(
            f"公式审计源文件不是有效 JSON：{exc}"
        ) from exc
    except OSError as exc:
        raise FormulaAuditError(
            f"无法读取公式审计源文件：{exc}"
        ) from exc
    if not isinstance(items, list):
        raise FormulaAuditError(
            "公式审计源文件的 JSON 顶层必须是数组"
        )
    candidates = scan_content_list(items)
    formula_validator = validator or KaTeXFormulaValidator()
    raw_inputs = tuple(
        ValidationInput(
            formula_id=value.formula_id,
            formula=value.katex_formula,
            is_block=value.is_block,
        )
        for value in candidates
    )
    raw_validations = _checked_validation_results(
        raw_inputs,
        formula_validator,
    )
    decisions = _normalization_decisions(
        candidates,
        normalizer or DeterministicFormulaNormalizer(),
        formula_validator,
    )
    report = _build_report(
        candidates,
        raw_validations,
        decisions,
    )
    _write_report_atomic(output, report.payload)
    log_formula_audit_summary(report)
    return report


def _valid_page_indices(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(type(page) is int for page in value)
        and value == sorted(set(value))
    )


def _validate_record_location(
    value: object,
    expected_keys: set[str],
) -> bool:
    if not isinstance(value, dict) or set(value) != expected_keys:
        return False
    formula_id = value["formula_id"]
    content_hash = value["content_hash"]
    source_type = value["source_type"]
    formula_index = value["formula_index"]
    syntax_status = value["syntax_status"]
    suspicious = value["suspicious"]
    if (
        not isinstance(formula_id, str)
        or _FORMULA_ID_RE.fullmatch(formula_id) is None
        or not isinstance(content_hash, str)
        or _CONTENT_HASH_RE.fullmatch(content_hash) is None
        or source_type not in {"equation", "text", "table_cell"}
        or not isinstance(value["field_path"], str)
        or not value["field_path"].startswith("/")
        or type(formula_index) is not int
        or formula_index < 0
        or not isinstance(value["raw_formula"], str)
        or type(value["is_block"]) is not bool
        or syntax_status not in {"valid", "invalid_syntax"}
        or type(suspicious) is not bool
    ):
        return False
    if source_type == "table_cell":
        if (
            type(value["table_row_idx"]) is not int
            or value["table_row_idx"] < 0
            or type(value["table_col_idx"]) is not int
            or value["table_col_idx"] < 0
            or value["cell_tag"] not in {"td", "th"}
        ):
            return False
    elif any(
        value[name] is not None
        for name in (
            "table_row_idx",
            "table_col_idx",
            "cell_tag",
        )
    ):
        return False
    return True


def _validate_suspicion_fields(value: dict[str, object]) -> bool:
    syntax_status = value["syntax_status"]
    suspicious = value["suspicious"]
    expected_statuses = [syntax_status]
    if suspicious:
        expected_statuses.append("suspicious")
    if value["statuses"] != expected_statuses:
        return False
    rules = value["suspicion_rules"]
    if (
        not isinstance(rules, list)
        or bool(rules) != suspicious
        or any(
            not isinstance(rule, dict)
            or set(rule) != {"rule", "message", "matched_text"}
            or not all(isinstance(item, str) for item in rule.values())
            for rule in rules
        )
    ):
        return False
    return True


def _validate_v1_record(value: object) -> bool:
    if not _validate_record_location(value, _V1_RECORD_KEYS):
        return False
    assert isinstance(value, dict)
    if not _validate_suspicion_fields(value):
        return False
    syntax_status = value["syntax_status"]
    error = value["validation_error"]
    if (
        (syntax_status == "valid" and error is not None)
        or (
            syntax_status == "invalid_syntax"
            and (not isinstance(error, str) or not error)
        )
    ):
        return False
    return (
        value["normalized_formula"] is None
        and value["normalization_rule"] is None
        and value["confidence"] is None
    )


def _validate_v2_record(value: object) -> bool:
    if not _validate_record_location(value, _RECORD_KEYS):
        return False
    assert isinstance(value, dict)
    if not _validate_suspicion_fields(value):
        return False
    syntax_status = value["syntax_status"]
    raw_error = value["raw_validation_error"]
    if (
        (syntax_status == "valid" and raw_error is not None)
        or (
            syntax_status == "invalid_syntax"
            and (
                not isinstance(raw_error, str)
                or not raw_error
            )
        )
    ):
        return False

    status = value["normalization_status"]
    normalized = value["normalized_formula"]
    rules = value["normalization_rules"]
    error = value["validation_error"]
    if (
        status
        not in {"not_applicable", "accepted", "rejected"}
        or not isinstance(rules, list)
        or any(
            not isinstance(rule, str) or not rule
            for rule in rules
        )
        or len(rules) != len(set(rules))
    ):
        return False
    if status == "not_applicable":
        return normalized is None and rules == [] and error is None
    if (
        not isinstance(normalized, str)
        or not normalized
        or normalized == value["raw_formula"]
        or not rules
    ):
        return False
    if status == "accepted":
        return error is None
    return isinstance(error, str) and bool(error)


def _validate_report_header(
    payload: object,
    *,
    schema_version: int,
    summary_keys: set[str],
) -> tuple[dict[str, object], dict[str, int]]:
    if not isinstance(payload, dict) or set(payload) != _REPORT_KEYS:
        raise FormulaAuditError("公式审计报告顶层字段无效")
    if payload["schema_version"] != schema_version:
        raise FormulaAuditError("公式审计报告版本无效")
    if payload["validator"] != {
        "name": "katex",
        "version": KATEX_VERSION,
        "config": KATEX_CONFIG,
    }:
        raise FormulaAuditError("公式审计报告校验器信息无效")
    summary = payload["summary"]
    if (
        not isinstance(summary, dict)
        or set(summary) != summary_keys
        or any(
            type(value) is not int or value < 0
            for value in summary.values()
        )
    ):
        raise FormulaAuditError("公式审计报告汇总无效")
    return payload, summary


def _validate_collections(
    payload: dict[str, object],
    *,
    record_validator,
    issue_predicate,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    formulas = payload["formulas"]
    issues = payload["issues"]
    if (
        not isinstance(formulas, list)
        or not isinstance(issues, list)
        or not all(record_validator(value) for value in formulas)
        or not all(record_validator(value) for value in issues)
    ):
        raise FormulaAuditError("公式审计报告公式记录无效")
    formula_ids = [value["formula_id"] for value in formulas]
    if len(formula_ids) != len(set(formula_ids)):
        raise FormulaAuditError("公式审计报告 formula_id 重复")
    formulas_by_id = {
        value["formula_id"]: value for value in formulas
    }
    expected_issues = [
        value for value in formulas if issue_predicate(value)
    ]
    if issues != expected_issues or any(
        formulas_by_id.get(value["formula_id"]) != value
        for value in issues
    ):
        raise FormulaAuditError("公式审计报告异常公式记录无效")
    return formulas, issues


def _validate_page_fields(
    payload: dict[str, object],
    formulas: list[dict[str, object]],
    *,
    problem_predicate,
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    page_fields = (
        "problem_page_indices",
        "invalid_syntax_page_indices",
        "suspicious_page_indices",
    )
    if not all(_valid_page_indices(payload[name]) for name in page_fields):
        raise FormulaAuditError("公式审计报告问题页码无效")
    invalid_pages = _page_indices(
        formulas,
        lambda value: value["syntax_status"] == "invalid_syntax",
    )
    suspicious_pages = _page_indices(
        formulas,
        lambda value: bool(value["suspicious"]),
    )
    problem_pages = _page_indices(formulas, problem_predicate)
    if (
        payload["problem_page_indices"] != list(problem_pages)
        or payload["invalid_syntax_page_indices"]
        != list(invalid_pages)
        or payload["suspicious_page_indices"]
        != list(suspicious_pages)
    ):
        raise FormulaAuditError(
            "公式审计报告问题页码与公式记录不一致"
        )
    return problem_pages, invalid_pages, suspicious_pages


def _validate_v1_payload(payload: object) -> None:
    checked, summary = _validate_report_header(
        payload,
        schema_version=1,
        summary_keys=_V1_SUMMARY_KEYS,
    )
    formulas, issues = _validate_collections(
        checked,
        record_validator=_validate_v1_record,
        issue_predicate=lambda value: (
            value["syntax_status"] == "invalid_syntax"
            or bool(value["suspicious"])
        ),
    )
    expected_summary = {
        "total_formulas": len(formulas),
        "valid_count": sum(
            value["syntax_status"] == "valid"
            for value in formulas
        ),
        "invalid_syntax_count": sum(
            value["syntax_status"] == "invalid_syntax"
            for value in formulas
        ),
        "suspicious_count": sum(
            bool(value["suspicious"]) for value in formulas
        ),
        "issue_formula_count": len(issues),
    }
    if summary != expected_summary:
        raise FormulaAuditError(
            "公式审计报告汇总与公式记录不一致"
        )
    _validate_page_fields(
        checked,
        formulas,
        problem_predicate=lambda value: (
            value["syntax_status"] == "invalid_syntax"
            or bool(value["suspicious"])
        ),
    )


def _report_from_v2_payload(
    payload: object,
) -> FormulaAuditReport:
    checked, summary = _validate_report_header(
        payload,
        schema_version=SCHEMA_VERSION,
        summary_keys=_SUMMARY_KEYS,
    )
    formulas, issues = _validate_collections(
        checked,
        record_validator=_validate_v2_record,
        issue_predicate=lambda value: (
            value["syntax_status"] == "invalid_syntax"
            or bool(value["suspicious"])
            or value["normalization_status"]
            in {"accepted", "rejected"}
        ),
    )
    stats = FormulaAuditStats(
        total_formulas=len(formulas),
        scanned_formula_count=len(formulas),
        valid_count=sum(
            value["syntax_status"] == "valid"
            for value in formulas
        ),
        invalid_syntax_count=sum(
            value["syntax_status"] == "invalid_syntax"
            for value in formulas
        ),
        suspicious_count=sum(
            bool(value["suspicious"]) for value in formulas
        ),
        issue_formula_count=len(issues),
        matched_formula_count=sum(
            value["normalization_status"]
            in {"accepted", "rejected"}
            for value in formulas
        ),
        normalization_accepted_count=sum(
            value["normalization_status"] == "accepted"
            for value in formulas
        ),
        normalization_rejected_count=sum(
            value["normalization_status"] == "rejected"
            for value in formulas
        ),
    )
    if summary != stats.to_dict():
        raise FormulaAuditError(
            "公式审计报告汇总与公式记录不一致"
        )
    problem_pages, invalid_pages, suspicious_pages = (
        _validate_page_fields(
            checked,
            formulas,
            problem_predicate=lambda value: (
                value["syntax_status"] == "invalid_syntax"
                or bool(value["suspicious"])
                or value["normalization_status"]
                in {"accepted", "rejected"}
            ),
        )
    )
    report = FormulaAuditReport(
        stats=stats,
        problem_page_indices=problem_pages,
        invalid_syntax_page_indices=invalid_pages,
        suspicious_page_indices=suspicious_pages,
        payload=checked,
    )
    report.accepted_replacements
    return report


def _report_from_payload(
    payload: object,
) -> FormulaAuditReport:
    if isinstance(payload, dict) and payload.get("schema_version") == 1:
        _validate_v1_payload(payload)
        raise LegacyFormulaAuditError(
            "公式审计报告 schema v1 需要重建"
        )
    return _report_from_v2_payload(payload)


def read_formula_audit_file(path: Path) -> FormulaAuditReport:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormulaAuditError(
            f"无法读取公式审计报告：{exc}"
        ) from exc
    return _report_from_payload(payload)
