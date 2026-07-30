from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pdf_trans.errors import FormulaAuditError


KATEX_VERSION = "0.18.1"
KATEX_CONFIG = {
    "throwOnError": False,
    "trust": False,
    "maxSize": 20,
    "maxExpand": 500,
}


@dataclass(frozen=True)
class ValidationInput:
    formula_id: str
    formula: str
    is_block: bool


@dataclass(frozen=True)
class ValidationResult:
    formula_id: str
    syntax_status: Literal["valid", "invalid_syntax"]
    validation_error: str | None


class FormulaValidator(Protocol):
    def validate_batch(
        self,
        formulas: tuple[ValidationInput, ...],
    ) -> tuple[ValidationResult, ...]:
        ...


class KaTeXFormulaValidator:
    def __init__(
        self,
        *,
        node_binary: str = "node",
        script_path: Path | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.node_binary = node_binary
        self.script_path = (
            script_path
            if script_path is not None
            else Path(__file__).with_name("katex_validator.js")
        )
        self.timeout_seconds = timeout_seconds

    def validate_batch(
        self,
        formulas: tuple[ValidationInput, ...],
    ) -> tuple[ValidationResult, ...]:
        if not formulas:
            return ()
        self._validate_inputs(formulas)
        payload = {
            "version": KATEX_VERSION,
            "config": KATEX_CONFIG,
            "formulas": [
                {
                    "formula_id": value.formula_id,
                    "formula": value.formula,
                    "is_block": value.is_block,
                }
                for value in formulas
            ],
        }
        try:
            completed = subprocess.run(
                [self.node_binary, str(self.script_path)],
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FormulaAuditError(
                f"无法运行 KaTeX 批量校验：{exc}"
            ) from exc
        if completed.returncode != 0:
            detail = self._safe_stderr(completed.stderr, formulas)
            raise FormulaAuditError(
                "KaTeX 批量校验进程失败"
                + (f"：{detail}" if detail else "")
            )
        try:
            response = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FormulaAuditError(
                "KaTeX 批量校验响应不是有效 JSON"
            ) from exc
        return self._parse_response(response, formulas)

    @staticmethod
    def _validate_inputs(
        formulas: tuple[ValidationInput, ...],
    ) -> None:
        identifiers: list[str] = []
        for value in formulas:
            if (
                not isinstance(value, ValidationInput)
                or not isinstance(value.formula_id, str)
                or not value.formula_id
                or not isinstance(value.formula, str)
                or type(value.is_block) is not bool
            ):
                raise FormulaAuditError("KaTeX 校验输入无效")
            identifiers.append(value.formula_id)
        if len(set(identifiers)) != len(identifiers):
            raise FormulaAuditError("KaTeX 校验输入存在重复 formula_id")

    @staticmethod
    def _safe_stderr(
        stderr: str,
        formulas: tuple[ValidationInput, ...],
    ) -> str:
        detail = (stderr or "").strip()
        for value in formulas:
            if value.formula:
                detail = detail.replace(value.formula, "<formula>")
        return detail[:1000]

    @staticmethod
    def _parse_response(
        response: object,
        formulas: tuple[ValidationInput, ...],
    ) -> tuple[ValidationResult, ...]:
        if not isinstance(response, dict):
            raise FormulaAuditError("KaTeX 批量校验响应必须是对象")
        if set(response) != {"version", "config", "results"}:
            raise FormulaAuditError("KaTeX 批量校验响应字段无效")
        if response["version"] != KATEX_VERSION:
            raise FormulaAuditError("KaTeX 批量校验版本不一致")
        if response["config"] != KATEX_CONFIG:
            raise FormulaAuditError("KaTeX 批量校验配置不一致")
        values = response["results"]
        if not isinstance(values, list):
            raise FormulaAuditError("KaTeX 批量校验结果必须是数组")
        if len(values) != len(formulas):
            raise FormulaAuditError("KaTeX 批量校验结果数量不一致")

        results: list[ValidationResult] = []
        for expected, value in zip(formulas, values):
            if not isinstance(value, dict) or set(value) != {
                "formula_id",
                "syntax_status",
                "validation_error",
            }:
                raise FormulaAuditError("KaTeX 批量校验结果字段无效")
            if value["formula_id"] != expected.formula_id:
                raise FormulaAuditError("KaTeX 批量校验结果顺序不一致")
            status = value["syntax_status"]
            error = value["validation_error"]
            if status == "valid":
                if error is not None:
                    raise FormulaAuditError(
                        "KaTeX 有效公式不能包含校验错误"
                    )
            elif status == "invalid_syntax":
                if not isinstance(error, str) or not error:
                    raise FormulaAuditError(
                        "KaTeX 无效公式缺少校验错误"
                    )
            else:
                raise FormulaAuditError(
                    "KaTeX 批量校验状态无效"
                )
            results.append(
                ValidationResult(
                    formula_id=expected.formula_id,
                    syntax_status=status,
                    validation_error=error,
                )
            )
        return tuple(results)
