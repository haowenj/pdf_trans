from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from pdf_trans.errors import FormulaAuditError
from pdf_trans.formula_audit import LegacyFormulaAuditError
from pdf_trans.web.config import WebSettings
from pdf_trans.web.repository import TaskView
from pdf_trans.web.task_runner import (
    AmbiguousCheckpoint,
    TaskRunner,
    WorkflowServices,
)


def task_view(*, attempt_count: int = 1) -> TaskView:
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    return TaskView(
        id="a",
        original_filename="paper.pdf",
        status="running",
        attempt_count=attempt_count,
        source_pdf_path="tasks/a/upload/source.pdf",
        normalized_path=None,
        markdown_path=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def settings(tmp_path) -> WebSettings:
    return WebSettings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'db'}",
        max_upload_mib=200,
        mineru_url="http://mineru:7100",
        mineru_backend="hybrid-http-client",
        mineru_server_url="http://gpustack:8000",
        host="127.0.0.1",
        port=8000,
    )


def make_checkpoint(tmp_path):
    normalized = (
        tmp_path
        / "tasks/a/attempts/1/paper/normalized_content_list.json"
    )
    normalized.parent.mkdir(parents=True)
    normalized.write_text("[]", encoding="utf-8")
    translated = normalized.with_name("translated_content_list.json")
    translated.write_text("[]", encoding="utf-8")
    return normalized


def fake_audit_report():
    return SimpleNamespace(
        accepted_replacements={},
        stats=SimpleNamespace(
            total_formulas=0,
            invalid_syntax_count=0,
            suspicious_count=0,
            scanned_formula_count=0,
            matched_formula_count=0,
            normalization_accepted_count=0,
            normalization_rejected_count=0,
        ),
        payload={"formulas": []},
        invalid_syntax_page_indices=(),
        suspicious_page_indices=(),
    )


def fake_translation_result(path):
    return SimpleNamespace(
        normalized_path=path,
        translated_path=path.with_name("translated_content_list.json"),
        stats=SimpleNamespace(success_count=3, failed_count=0),
    )


def test_runner_starts_full_workflow_when_no_checkpoint_exists(tmp_path):
    source = tmp_path / "tasks/a/upload/source.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-")
    received: dict[str, object] = {}

    def full(
        path,
        *,
        svr_url,
        mineru_backend,
        mineru_server_url,
        data_dir,
    ):
        received.update(
            path=path,
            svr_url=svr_url,
            mineru_backend=mineru_backend,
            mineru_server_url=mineru_server_url,
            data_dir=data_dir,
        )
        normalized = data_dir / "paper/normalized_content_list.json"
        markdown = data_dir / "paper/rendered.md"
        normalized.parent.mkdir(parents=True)
        normalized.write_text("[]", encoding="utf-8")
        markdown.write_text("# 译文", encoding="utf-8")
        return SimpleNamespace(
            normalized_path=normalized,
            markdown_path=markdown,
            before_count=1,
            filtered_count=0,
            candidate_count=0,
            translation_stats=SimpleNamespace(
                success_count=1, failed_count=0
            ),
        )

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(full, lambda path: None, lambda source, output: None),
    )
    result = runner.run(task_view())

    assert received["data_dir"] == tmp_path / "tasks/a/attempts/1"
    assert received["mineru_backend"] == "hybrid-http-client"
    assert received["mineru_server_url"] == "http://gpustack:8000"
    assert result.normalized_path.endswith("normalized_content_list.json")
    assert result.markdown_path.endswith("rendered.md")


def test_runner_resumes_translation_and_renders_markdown(tmp_path):
    normalized = make_checkpoint(tmp_path)
    normalized.with_name("source_content_list.json").write_text(
        "[]",
        encoding="utf-8",
    )
    translated = normalized.with_name("translated_content_list.json")
    calls: list[tuple[object, ...]] = []

    def translate(path):
        calls.append(("translate", path))
        return SimpleNamespace(
            normalized_path=path,
            translated_path=translated,
            stats=SimpleNamespace(success_count=3, failed_count=0),
        )

    def render(source, output, *, formula_audit=None):
        calls.append(("render", source, output, formula_audit))
        output.write_text("# resumed", encoding="utf-8")

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(lambda *args, **kwargs: None, translate, render),
    )
    result = runner.run(task_view(attempt_count=2))

    assert calls[0] == ("translate", normalized)
    assert calls[1][2] == normalized.with_name("rendered.md")
    assert calls[1][3] is not None
    assert result.resumed is True


def test_runner_reuses_valid_formula_audit_before_resume(tmp_path):
    normalized = make_checkpoint(tmp_path)
    audit = normalized.with_name("formula_audit.json")
    audit.write_text('{"schema_version": 2}', encoding="utf-8")
    calls = []
    report = fake_audit_report()

    def read_report(path):
        calls.append(("read_audit", path))
        return report

    def translate(path):
        calls.append(("translate", path))
        return fake_translation_result(path)

    def render(source, output, *, formula_audit=None):
        calls.append(("render", source, output, formula_audit))
        output.write_text("# resumed", encoding="utf-8")

    def fail_audit(source, output):
        raise AssertionError("existing report must be reused")

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(
            lambda *args, **kwargs: None,
            translate,
            render,
            fail_audit,
            read_report,
        ),
    )

    runner.run(task_view(attempt_count=2))

    assert calls == [
        ("read_audit", audit),
        ("translate", normalized),
        (
            "render",
            normalized.with_name("translated_content_list.json"),
            normalized.with_name("rendered.md"),
            report,
        ),
    ]


def test_runner_backfills_legacy_formula_audit_before_resume(tmp_path):
    normalized = make_checkpoint(tmp_path)
    source = normalized.with_name("source_content_list.json")
    source.write_text("[]", encoding="utf-8")
    audit = normalized.with_name("formula_audit.json")
    calls = []
    report = fake_audit_report()

    def create_audit(source_path, output_path):
        calls.append(("audit", source_path, output_path))
        output_path.write_text("{}", encoding="utf-8")
        return report

    def translate(path):
        calls.append(("translate", path))
        return fake_translation_result(path)

    def render(source_path, output_path, *, formula_audit=None):
        calls.append(
            ("render", source_path, output_path, formula_audit)
        )
        output_path.write_text("# resumed", encoding="utf-8")

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(
            lambda *args, **kwargs: None,
            translate,
            render,
            create_audit,
            lambda path: (_ for _ in ()).throw(
                AssertionError("missing report must not be read")
            ),
        ),
    )

    runner.run(task_view(attempt_count=2))

    assert calls[:2] == [
        ("audit", source, audit),
        ("translate", normalized),
    ]
    assert calls[2][0] == "render"
    assert calls[2][3] is report
    assert audit.exists()


def test_runner_rebuilds_valid_v1_formula_audit_before_resume(
    tmp_path,
):
    normalized = make_checkpoint(tmp_path)
    source = normalized.with_name("source_content_list.json")
    source.write_text("[]", encoding="utf-8")
    audit = normalized.with_name("formula_audit.json")
    audit.write_text('{"schema_version": 1}', encoding="utf-8")
    report = fake_audit_report()
    calls = []

    def read_report(path):
        calls.append(("read_v1", path))
        raise LegacyFormulaAuditError("schema v1")

    def create_audit(source_path, output_path):
        calls.append(("rebuild_v2", source_path, output_path))
        return report

    def translate(path):
        calls.append(("translate", path))
        return fake_translation_result(path)

    def render(source_path, output_path, *, formula_audit=None):
        calls.append(("render", formula_audit))
        output_path.write_text("# resumed", encoding="utf-8")

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(
            lambda *args, **kwargs: None,
            translate,
            render,
            create_audit,
            read_report,
        ),
    )

    runner.run(task_view(attempt_count=2))

    assert calls == [
        ("read_v1", audit),
        ("rebuild_v2", source, audit),
        ("translate", normalized),
        ("render", report),
    ]


def test_runner_does_not_rebuild_malformed_v2_audit(tmp_path):
    normalized = make_checkpoint(tmp_path)
    audit = normalized.with_name("formula_audit.json")
    audit.write_text('{"schema_version": 2}', encoding="utf-8")
    calls = []

    def read_report(path):
        raise FormulaAuditError("malformed v2")

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("must not continue after malformed v2")

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(
            lambda *args, **kwargs: None,
            fail,
            fail,
            fail,
            read_report,
        ),
    )

    with pytest.raises(FormulaAuditError, match="malformed v2"):
        runner.run(task_view(attempt_count=2))

    assert calls == []


@pytest.mark.parametrize("raw_count", [0, 2])
def test_runner_rejects_non_unique_raw_content_list_on_resume(
    tmp_path,
    raw_count,
):
    normalized = make_checkpoint(tmp_path)
    for index in range(raw_count):
        normalized.with_name(
            f"source_{index}_content_list.json"
        ).write_text("[]", encoding="utf-8")
    calls = []

    def translate(path):
        calls.append("translate")
        return fake_translation_result(path)

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(
            lambda *args, **kwargs: None,
            translate,
            lambda source, output: None,
            lambda source, output: fake_audit_report(),
            lambda path: fake_audit_report(),
        ),
    )

    with pytest.raises(FormulaAuditError, match=f"实际找到 {raw_count} 个"):
        runner.run(task_view(attempt_count=2))

    assert calls == []


def test_runner_rejects_multiple_normalized_checkpoints(tmp_path):
    for attempt in ("1", "2"):
        path = (
            tmp_path
            / f"tasks/a/attempts/{attempt}/paper/normalized_content_list.json"
        )
        path.parent.mkdir(parents=True)
        path.write_text("[]", encoding="utf-8")
    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(None, None, None),
    )

    with pytest.raises(AmbiguousCheckpoint):
        runner.run(task_view(attempt_count=3))
