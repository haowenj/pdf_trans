import json
import logging

import pytest

from pdf_trans import formula_audit
from pdf_trans.errors import FormulaAuditError
from pdf_trans.formula_audit import (
    LegacyFormulaAuditError,
    audit_content_list_file,
    read_formula_audit_file,
)
from pdf_trans.formula_rules import NormalizationResult
from pdf_trans.formula_scanner import scan_content_list
from pdf_trans.formula_validation import ValidationResult


class FakeValidator:
    def __init__(self, *, reject_normalized_ids=()):
        self.calls = []
        self.reject_normalized_ids = set(reject_normalized_ids)

    def validate_batch(self, formulas):
        self.calls.append(formulas)
        normalized_call = len(self.calls) == 2
        return tuple(
            ValidationResult(
                value.formula_id,
                (
                    "invalid_syntax"
                    if (
                        normalized_call
                        and value.formula_id
                        in self.reject_normalized_ids
                    )
                    or (
                        not normalized_call
                        and (
                            "\\neqq" in value.formula
                            or "\\complement" in value.formula
                        )
                    )
                    else "valid"
                ),
                (
                    "normalized parse error"
                    if (
                        normalized_call
                        and value.formula_id
                        in self.reject_normalized_ids
                    )
                    else (
                        "fake parse error"
                        if not normalized_call
                        and (
                            "\\neqq" in value.formula
                            or "\\complement" in value.formula
                        )
                        else None
                    )
                ),
            )
            for value in formulas
        )


def _valid_empty_v1_payload():
    return {
        "schema_version": 1,
        "validator": {
            "name": "katex",
            "version": "0.18.1",
            "config": {
                "throwOnError": False,
                "trust": False,
                "maxSize": 20,
                "maxExpand": 500,
            },
        },
        "summary": {
            "total_formulas": 0,
            "valid_count": 0,
            "invalid_syntax_count": 0,
            "suspicious_count": 0,
            "issue_formula_count": 0,
        },
        "problem_page_indices": [],
        "invalid_syntax_page_indices": [],
        "suspicious_page_indices": [],
        "formulas": [],
        "issues": [],
    }


def _source_items():
    return [
        {
            "type": "equation",
            "page_idx": 5,
            "bbox": [1, 2, 3, 4],
            "text": "$$C_7 \\neqq C_6$$",
        },
        {
            "type": "text",
            "page_idx": 2,
            "bbox": [5, 6, 7, 8],
            "text": (
                "C4 $C_4 \\complement C_3$ "
                "MeterMax $\\text{Meter Ma \\max}$ "
                "temperature ${15}^{\\circ}\\mathrm{C}$ "
                "real $C_7 \\neq C_6$"
            ),
        },
        {
            "type": "table",
            "page_idx": 7,
            "bbox": [9, 10, 11, 12],
            "table_body": (
                "<table><tr><td>"
                "$F I C$ / $H I C$ / $\\frac{m 3}{h}$"
                "</td></tr></table>"
            ),
        },
    ]


def _write_source(tmp_path, items=None):
    source = tmp_path / "paper_content_list.json"
    source.write_text(
        json.dumps(_source_items() if items is None else items),
        encoding="utf-8",
    )
    return source


def test_writes_complete_read_only_audit_report(tmp_path):
    source = _write_source(tmp_path)
    output = tmp_path / "formula_audit.json"
    validator = FakeValidator()
    before = source.read_bytes()

    report = audit_content_list_file(
        source,
        output,
        validator=validator,
    )

    assert source.read_bytes() == before
    assert len(validator.calls) == 2
    assert len(validator.calls[0]) == 8
    assert len(validator.calls[1]) == 1
    assert report.stats.total_formulas == 8
    assert report.stats.scanned_formula_count == 8
    assert report.stats.valid_count == 6
    assert report.stats.invalid_syntax_count == 2
    assert report.stats.suspicious_count == 6
    assert report.stats.issue_formula_count == 6
    assert report.stats.matched_formula_count == 1
    assert report.stats.normalization_accepted_count == 1
    assert report.stats.normalization_rejected_count == 0
    assert report.problem_page_indices == (2, 5, 7)
    assert report.invalid_syntax_page_indices == (2, 5)
    assert report.suspicious_page_indices == (2, 5, 7)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == report.payload
    assert payload["schema_version"] == 2
    assert payload["validator"] == {
        "name": "katex",
        "version": "0.18.1",
        "config": {
            "throwOnError": False,
            "trust": False,
            "maxSize": 20,
            "maxExpand": 500,
        },
    }
    assert payload["summary"] == report.stats.to_dict()
    assert len(payload["formulas"]) == 8
    assert len(payload["issues"]) == 6

    c7 = next(
        value
        for value in payload["formulas"]
        if value["raw_formula"] == "$$C_7 \\neqq C_6$$"
    )
    assert c7["statuses"] == ["invalid_syntax", "suspicious"]
    assert c7["field_path"] == "/0/text"
    assert c7["raw_validation_error"] == "fake parse error"
    assert c7["normalization_status"] == "not_applicable"
    assert c7["normalization_rules"] == []
    assert c7["validation_error"] is None

    meter = next(
        value
        for value in payload["formulas"]
        if value["raw_formula"] == "$\\text{Meter Ma \\max}$"
    )
    assert meter["statuses"] == ["valid", "suspicious"]
    assert {value["rule"] for value in meter["suspicion_rules"]} == {
        "max_inside_text",
        "split_meter_max",
    }

    neq = next(
        value
        for value in payload["formulas"]
        if value["raw_formula"] == "$C_7 \\neq C_6$"
    )
    assert neq["statuses"] == ["valid"]
    assert neq["suspicious"] is False
    assert neq["raw_formula"] == "$C_7 \\neq C_6$"
    assert neq["normalized_formula"] is None
    assert neq["normalization_rules"] == []
    assert neq["normalization_status"] == "not_applicable"
    assert neq["validation_error"] is None

    fraction = next(
        value
        for value in payload["formulas"]
        if value["raw_formula"] == "$\\frac{m 3}{h}$"
    )
    assert fraction["normalized_formula"] == (
        "$\\frac{\\mathrm{m}^{3}}{\\mathrm{h}}$"
    )
    assert fraction["normalization_rules"] == [
        "cubic_metre_per_hour"
    ]
    assert fraction["normalization_status"] == "accepted"
    assert fraction["validation_error"] is None
    assert report.accepted_replacements == {
        (
            "$\\frac{m 3}{h}$",
            False,
        ): "$\\frac{\\mathrm{m}^{3}}{\\mathrm{h}}$"
    }

    table_values = [
        value
        for value in payload["formulas"]
        if value["source_type"] == "table_cell"
    ]
    assert [value["formula_index"] for value in table_values] == [0, 1, 2]
    assert {
        (value["table_row_idx"], value["table_col_idx"])
        for value in table_values
    } == {(0, 0)}


def test_empty_content_writes_zero_report_without_calling_validator(tmp_path):
    class FailingValidator:
        def validate_batch(self, formulas):
            raise AssertionError("empty audit must not call validator")

    source = _write_source(tmp_path, [])
    output = tmp_path / "formula_audit.json"

    report = audit_content_list_file(
        source,
        output,
        validator=FailingValidator(),
    )

    assert report.stats.to_dict() == {
        "total_formulas": 0,
        "scanned_formula_count": 0,
        "valid_count": 0,
        "invalid_syntax_count": 0,
        "suspicious_count": 0,
        "issue_formula_count": 0,
        "matched_formula_count": 0,
        "normalization_accepted_count": 0,
        "normalization_rejected_count": 0,
    }
    assert report.payload["formulas"] == []
    assert report.payload["issues"] == []


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not json", "JSON"),
        ('{"type":"text"}', "顶层必须是数组"),
    ],
)
def test_rejects_invalid_content_list(tmp_path, content, message):
    source = tmp_path / "paper_content_list.json"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(FormulaAuditError, match=message):
        audit_content_list_file(
            source,
            tmp_path / "formula_audit.json",
            validator=FakeValidator(),
        )


def test_rejects_report_path_that_would_overwrite_source(tmp_path):
    source = _write_source(tmp_path, [])
    before = source.read_bytes()

    with pytest.raises(FormulaAuditError, match="不能覆盖"):
        audit_content_list_file(
            source,
            source,
            validator=FakeValidator(),
        )

    assert source.read_bytes() == before


def test_removes_temporary_report_when_atomic_replace_fails(
    tmp_path,
    monkeypatch,
):
    source = _write_source(tmp_path, [])
    output = tmp_path / "formula_audit.json"

    def fail_replace(source_path, output_path):
        raise OSError("replace failed")

    monkeypatch.setattr(formula_audit.os, "replace", fail_replace)

    with pytest.raises(FormulaAuditError, match="写入"):
        audit_content_list_file(
            source,
            output,
            validator=FakeValidator(),
        )

    assert not output.exists()
    assert not (tmp_path / ".formula_audit.json.tmp").exists()


def test_uses_injected_normalizer_for_delimiter_free_formula(tmp_path):
    class ChangingNormalizer:
        def normalize(self, formula):
            assert formula == "x"
            return NormalizationResult("changed", ("custom_rule",))

    source = _write_source(
        tmp_path,
        [{"type": "text", "text": "$x$"}],
    )

    report = audit_content_list_file(
        source,
        tmp_path / "formula_audit.json",
        validator=FakeValidator(),
        normalizer=ChangingNormalizer(),
    )

    record = report.payload["formulas"][0]
    assert record["normalized_formula"] == "$changed$"
    assert record["normalization_rules"] == ["custom_rule"]
    assert record["normalization_status"] == "accepted"


def test_rejected_candidate_keeps_detail_but_has_no_replacement(
    tmp_path,
):
    source = _write_source(
        tmp_path,
        [{"type": "text", "page_idx": 3, "text": "$\\frac{m 3}{h}$"}],
    )
    items = json.loads(source.read_text(encoding="utf-8"))
    formula_id = scan_content_list(items)[0].formula_id

    report = audit_content_list_file(
        source,
        tmp_path / "formula_audit.json",
        validator=FakeValidator(
            reject_normalized_ids=(formula_id,)
        ),
    )

    record = report.payload["formulas"][0]
    assert record["normalization_status"] == "rejected"
    assert record["normalized_formula"] == (
        "$\\frac{\\mathrm{m}^{3}}{\\mathrm{h}}$"
    )
    assert record["validation_error"] == "normalized parse error"
    assert report.accepted_replacements == {}


def test_logs_repair_counts_and_before_after_detail(
    tmp_path,
    caplog,
    monkeypatch,
):
    source = _write_source(tmp_path)
    package_logger = logging.getLogger("pdf_trans")
    monkeypatch.setattr(package_logger, "handlers", [])
    monkeypatch.setattr(package_logger, "propagate", True)
    monkeypatch.setattr(package_logger, "level", logging.NOTSET)
    caplog.set_level(logging.INFO, logger="pdf_trans.formula_audit")

    audit_content_list_file(
        source,
        tmp_path / "formula_audit.json",
        validator=FakeValidator(),
    )

    text = "\n".join(caplog.messages)
    assert "总公式 8，语法失败 2，可疑公式 6" in text
    assert "公式修复统计：扫描 8，命中 1，accepted 1，rejected 0" in text
    assert "公式语法失败 page_idx：[2, 5]" in text
    assert "可疑公式 page_idx：[2, 5, 7]" in text
    assert "formula_id=" in text
    assert "page_idx=7" in text
    assert "rules=['cubic_metre_per_hour']" in text
    assert "raw=$\\frac{m 3}{h}$" in text
    assert (
        "normalized=$\\frac{\\mathrm{m}^{3}}{\\mathrm{h}}$"
        in text
    )


def test_reads_and_validates_existing_report(tmp_path):
    source = _write_source(tmp_path)
    output = tmp_path / "formula_audit.json"
    written = audit_content_list_file(
        source,
        output,
        validator=FakeValidator(),
    )

    loaded = read_formula_audit_file(output)

    assert loaded.stats == written.stats
    assert loaded.payload == written.payload


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: {**value, "schema_version": 1},
        lambda value: {
            **value,
            "summary": {
                **value["summary"],
                "total_formulas": value["summary"]["total_formulas"] + 1,
            },
        },
        lambda value: {
            **value,
            "problem_page_indices": [7, 2, 5],
        },
        lambda value: {**value, "issues": []},
        lambda value: {
            **value,
            "formulas": [
                {**value["formulas"][0], "content_hash": "invalid"},
                *value["formulas"][1:],
            ],
        },
        lambda value: {
            **value,
            "formulas": [
                *value["formulas"][:-1],
                {
                    **value["formulas"][-1],
                    "normalization_status": "accepted",
                    "validation_error": "must be null",
                },
            ],
        },
    ],
)
def test_rejects_structurally_invalid_existing_report(
    tmp_path,
    mutate,
):
    source = _write_source(tmp_path)
    output = tmp_path / "formula_audit.json"
    audit_content_list_file(
        source,
        output,
        validator=FakeValidator(),
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    output.write_text(
        json.dumps(mutate(payload)),
        encoding="utf-8",
    )

    with pytest.raises(FormulaAuditError, match="审计报告"):
        read_formula_audit_file(output)


def test_rejects_validator_result_order_mismatch(tmp_path):
    class ReversingValidator(FakeValidator):
        def validate_batch(self, formulas):
            return tuple(reversed(super().validate_batch(formulas)))

    source = _write_source(tmp_path)

    with pytest.raises(FormulaAuditError, match="顺序"):
        audit_content_list_file(
            source,
            tmp_path / "formula_audit.json",
            validator=ReversingValidator(),
        )


def test_valid_v1_report_raises_legacy_migration_signal(tmp_path):
    output = tmp_path / "formula_audit.json"
    output.write_text(
        json.dumps(_valid_empty_v1_payload()),
        encoding="utf-8",
    )

    with pytest.raises(LegacyFormulaAuditError):
        read_formula_audit_file(output)


def test_malformed_v1_report_is_not_migratable(tmp_path):
    payload = _valid_empty_v1_payload()
    payload["summary"]["total_formulas"] = 1
    output = tmp_path / "formula_audit.json"
    output.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FormulaAuditError) as error:
        read_formula_audit_file(output)

    assert not isinstance(error.value, LegacyFormulaAuditError)
