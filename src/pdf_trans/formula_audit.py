from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from pdf_trans.errors import FormulaAuditError
from pdf_trans.formula_rules import (
    FormulaNormalizer,
    NoOpFormulaNormalizer,
    find_suspicious_formula,
)
from pdf_trans.formula_scanner import FormulaCandidate, scan_content_list
from pdf_trans.formula_validation import (
    KATEX_CONFIG,
    KATEX_VERSION,
    FormulaValidator,
    KaTeXFormulaValidator,
    ValidationInput,
    ValidationResult,
)


LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = 1
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
_SUMMARY_KEYS = {
    "total_formulas",
    "valid_count",
    "invalid_syntax_count",
    "suspicious_count",
    "issue_formula_count",
}
_RECORD_KEYS = {
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


@dataclass(frozen=True)
class FormulaAuditStats:
    total_formulas: int
    valid_count: int
    invalid_syntax_count: int
    suspicious_count: int
    issue_formula_count: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaAuditReport:
    stats: FormulaAuditStats
    problem_page_indices: tuple[int, ...]
    invalid_syntax_page_indices: tuple[int, ...]
    suspicious_page_indices: tuple[int, ...]
    payload: dict[str, object]


def _validation_results(
    candidates: tuple[FormulaCandidate, ...],
    validator: FormulaValidator,
) -> tuple[ValidationResult, ...]:
    if not candidates:
        return ()
    results = validator.validate_batch(
        tuple(
            ValidationInput(
                formula_id=value.formula_id,
                formula=value.katex_formula,
                is_block=value.is_block,
            )
            for value in candidates
        )
    )
    if len(results) != len(candidates):
        raise FormulaAuditError("公式校验结果数量不一致")
    for candidate, result in zip(candidates, results):
        if (
            not isinstance(result, ValidationResult)
            or result.formula_id != candidate.formula_id
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
    return results


def _record(
    candidate: FormulaCandidate,
    validation: ValidationResult,
    normalizer: FormulaNormalizer,
) -> dict[str, object]:
    matches = find_suspicious_formula(candidate.katex_formula)
    normalization = normalizer.normalize(candidate.raw_formula)
    if any(
        value is not None
        for value in (
            normalization.normalized_formula,
            normalization.normalization_rule,
            normalization.confidence,
        )
    ):
        raise FormulaAuditError("第一版公式审计禁止自动规范化")
    statuses = [validation.syntax_status]
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
        "syntax_status": validation.syntax_status,
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
        "validation_error": validation.validation_error,
        "normalized_formula": normalization.normalized_formula,
        "normalization_rule": normalization.normalization_rule,
        "confidence": normalization.confidence,
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
    validations: tuple[ValidationResult, ...],
    normalizer: FormulaNormalizer,
) -> FormulaAuditReport:
    records = [
        _record(candidate, validation, normalizer)
        for candidate, validation in zip(candidates, validations)
    ]
    issues = [
        value
        for value in records
        if value["syntax_status"] == "invalid_syntax"
        or value["suspicious"]
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
        ),
    )
    stats = FormulaAuditStats(
        total_formulas=len(records),
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
    return FormulaAuditReport(
        stats=stats,
        problem_page_indices=problem_pages,
        invalid_syntax_page_indices=invalid_pages,
        suspicious_page_indices=suspicious_pages,
        payload=payload,
    )


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
    validations = _validation_results(
        candidates,
        validator or KaTeXFormulaValidator(),
    )
    report = _build_report(
        candidates,
        validations,
        normalizer or NoOpFormulaNormalizer(),
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


def _validate_record(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _RECORD_KEYS:
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


def _report_from_payload(
    payload: object,
) -> FormulaAuditReport:
    if not isinstance(payload, dict) or set(payload) != _REPORT_KEYS:
        raise FormulaAuditError("公式审计报告顶层字段无效")
    if payload["schema_version"] != SCHEMA_VERSION:
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
        or set(summary) != _SUMMARY_KEYS
        or any(
            type(value) is not int or value < 0
            for value in summary.values()
        )
    ):
        raise FormulaAuditError("公式审计报告汇总无效")
    formulas = payload["formulas"]
    issues = payload["issues"]
    if (
        not isinstance(formulas, list)
        or not isinstance(issues, list)
        or not all(_validate_record(value) for value in formulas)
        or not all(_validate_record(value) for value in issues)
    ):
        raise FormulaAuditError("公式审计报告公式记录无效")
    formula_ids = [value["formula_id"] for value in formulas]
    if len(formula_ids) != len(set(formula_ids)):
        raise FormulaAuditError("公式审计报告 formula_id 重复")
    formulas_by_id = {
        value["formula_id"]: value for value in formulas
    }
    issue_ids = [value["formula_id"] for value in issues]
    expected_issue_ids = [
        value["formula_id"]
        for value in formulas
        if value["syntax_status"] == "invalid_syntax"
        or value["suspicious"]
    ]
    if (
        issue_ids != expected_issue_ids
        or any(
            formulas_by_id.get(value["formula_id"]) != value
            for value in issues
        )
    ):
        raise FormulaAuditError("公式审计报告异常公式记录无效")

    stats = FormulaAuditStats(
        total_formulas=len(formulas),
        valid_count=sum(
            value["syntax_status"] == "valid" for value in formulas
        ),
        invalid_syntax_count=sum(
            value["syntax_status"] == "invalid_syntax"
            for value in formulas
        ),
        suspicious_count=sum(
            bool(value["suspicious"]) for value in formulas
        ),
        issue_formula_count=len(issues),
    )
    if summary != stats.to_dict():
        raise FormulaAuditError("公式审计报告汇总与公式记录不一致")

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
    problem_pages = _page_indices(
        formulas,
        lambda value: (
            value["syntax_status"] == "invalid_syntax"
            or bool(value["suspicious"])
        ),
    )
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
    return FormulaAuditReport(
        stats=stats,
        problem_page_indices=problem_pages,
        invalid_syntax_page_indices=invalid_pages,
        suspicious_page_indices=suspicious_pages,
        payload=payload,
    )


def read_formula_audit_file(path: Path) -> FormulaAuditReport:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormulaAuditError(
            f"无法读取公式审计报告：{exc}"
        ) from exc
    return _report_from_payload(payload)
