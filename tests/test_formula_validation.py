import json
import subprocess
from pathlib import Path

import pytest

from pdf_trans import formula_validation
from pdf_trans.errors import FormulaAuditError
from pdf_trans.formula_validation import (
    KATEX_CONFIG,
    KATEX_VERSION,
    KaTeXFormulaValidator,
    ValidationInput,
)


def test_validates_complete_batch_with_vendored_katex():
    results = KaTeXFormulaValidator().validate_batch(
        (
            ValidationInput(
                "temperature",
                "{15}^{\\circ}\\mathrm{C}",
                False,
            ),
            ValidationInput("c7", "C_7 \\neqq C_6", True),
            ValidationInput("neq", "C_7 \\neq C_6", False),
        )
    )

    assert [value.formula_id for value in results] == [
        "temperature",
        "c7",
        "neq",
    ]
    assert [value.syntax_status for value in results] == [
        "valid",
        "invalid_syntax",
        "valid",
    ]
    assert results[0].validation_error is None
    assert results[1].validation_error.startswith("KaTeX parse error:")
    assert results[2].validation_error is None


def test_production_katex_accepts_normalized_whitelist_examples():
    results = KaTeXFormulaValidator().validate_batch(
        (
            ValidationInput(
                "complement",
                r"\mathrm{C}_{4}^{=}",
                False,
            ),
            ValidationInput(
                "alkene",
                r"\mathrm{C}_{7}^{=} / \mathrm{C}_{8}^{=}",
                False,
            ),
            ValidationInput(
                "control",
                r"\mathrm{FIC0012}_{\mathrm{SetPoint}}"
                r"\frac{\mathrm{m}^{3}}{\mathrm{h}}",
                True,
            ),
        )
    )

    assert [value.syntax_status for value in results] == [
        "valid",
        "valid",
        "valid",
    ]
    assert all(value.validation_error is None for value in results)


def test_configuration_matches_frontend_source():
    root = Path(__file__).resolve().parents[1]
    reader = (
        root / "src/pdf_trans/web/static/reader.js"
    ).read_text(encoding="utf-8")
    source = (
        root / "src/pdf_trans/web/static/vendor/katex/SOURCE.txt"
    ).read_text(encoding="utf-8")

    assert KATEX_VERSION == "0.18.1"
    assert "KaTeX 0.18.1" in source
    assert KATEX_CONFIG == {
        "throwOnError": False,
        "trust": False,
        "maxSize": 20,
        "maxExpand": 500,
    }
    for fragment in (
        "throwOnError: false",
        "trust: false",
        "maxSize: 20",
        "maxExpand: 500",
        "{ left: '$$', right: '$$', display: true }",
        "{ left: '$', right: '$', display: false }",
        "{ left: '\\\\(', right: '\\\\)', display: false }",
        "{ left: '\\\\[', right: '\\\\]', display: true }",
    ):
        assert fragment in reader


def _successful_response(payload):
    return {
        "version": KATEX_VERSION,
        "config": KATEX_CONFIG,
        "results": [
            {
                "formula_id": value["formula_id"],
                "syntax_status": "valid",
                "validation_error": None,
            }
            for value in payload["formulas"]
        ],
    }


def test_non_empty_batch_starts_exactly_one_node_process(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        payload = json.loads(kwargs["input"])
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_successful_response(payload)),
            stderr="",
        )

    monkeypatch.setattr(formula_validation.subprocess, "run", fake_run)

    results = KaTeXFormulaValidator().validate_batch(
        (
            ValidationInput("a", "x", False),
            ValidationInput("b", "y", True),
            ValidationInput("c", "z", False),
        )
    )

    assert len(calls) == 1
    assert calls[0][0][0] == "node"
    payload = json.loads(calls[0][1]["input"])
    assert payload["formulas"] == [
        {"formula_id": "a", "formula": "x", "is_block": False},
        {"formula_id": "b", "formula": "y", "is_block": True},
        {"formula_id": "c", "formula": "z", "is_block": False},
    ]
    assert [value.formula_id for value in results] == ["a", "b", "c"]


def test_empty_batch_does_not_start_node(monkeypatch):
    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess should not start")

    monkeypatch.setattr(formula_validation.subprocess, "run", fail_run)

    assert KaTeXFormulaValidator().validate_batch(()) == ()


@pytest.mark.parametrize(
    "values",
    [
        (ValidationInput("", "x", False),),
        (ValidationInput("same", "x", False), ValidationInput("same", "y", True)),
        (ValidationInput("x", 123, False),),
        (ValidationInput("x", "x", "false"),),
    ],
)
def test_rejects_invalid_input_before_starting_node(monkeypatch, values):
    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess should not start")

    monkeypatch.setattr(formula_validation.subprocess, "run", fail_run)

    with pytest.raises(FormulaAuditError, match="校验输入"):
        KaTeXFormulaValidator().validate_batch(values)


def _run_with_response(monkeypatch, response, *, returncode=0, stderr=""):
    def fake_run(command, **kwargs):
        payload = json.loads(kwargs["input"])
        value = response(payload) if callable(response) else response
        stdout = value if isinstance(value, str) else json.dumps(value)
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=stdout,
            stderr=stderr,
        )

    monkeypatch.setattr(formula_validation.subprocess, "run", fake_run)
    return KaTeXFormulaValidator()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda response: {**response, "version": "0.17.0"},
            "版本",
        ),
        (
            lambda response: {
                **response,
                "config": {**KATEX_CONFIG, "maxSize": 99},
            },
            "配置",
        ),
        (
            lambda response: {
                **response,
                "results": list(reversed(response["results"])),
            },
            "顺序",
        ),
        (
            lambda response: {
                **response,
                "results": response["results"][:-1],
            },
            "数量",
        ),
        (
            lambda response: {
                **response,
                "results": [
                    response["results"][0],
                    response["results"][0],
                ],
            },
            "顺序",
        ),
    ],
)
def test_rejects_invalid_node_protocol(monkeypatch, mutate, message):
    def response(payload):
        return mutate(_successful_response(payload))

    validator = _run_with_response(monkeypatch, response)

    with pytest.raises(FormulaAuditError, match=message):
        validator.validate_batch(
            (
                ValidationInput("a", "x", False),
                ValidationInput("b", "y", True),
            )
        )


def test_rejects_non_json_node_output(monkeypatch):
    validator = _run_with_response(monkeypatch, "not json")

    with pytest.raises(FormulaAuditError, match="JSON"):
        validator.validate_batch((ValidationInput("a", "x", False),))


def test_rejects_non_zero_node_exit_without_formula_source(monkeypatch):
    validator = _run_with_response(
        monkeypatch,
        "",
        returncode=1,
        stderr="internal failure",
    )

    with pytest.raises(FormulaAuditError, match="internal failure") as error:
        validator.validate_batch(
            (ValidationInput("a", "SECRET_FORMULA", False),)
        )

    assert "SECRET_FORMULA" not in str(error.value)


def test_wraps_node_startup_and_timeout_errors(monkeypatch):
    for exception in (
        OSError("node missing"),
        subprocess.TimeoutExpired(["node"], 3),
    ):
        monkeypatch.setattr(
            formula_validation.subprocess,
            "run",
            lambda *args, exception=exception, **kwargs: (_ for _ in ()).throw(
                exception
            ),
        )
        with pytest.raises(FormulaAuditError):
            KaTeXFormulaValidator().validate_batch(
                (ValidationInput("a", "x", False),)
            )
