from pathlib import Path
from zipfile import ZipFile

import pytest

from pdf_trans.task_diagnostics import (
    DiagnosticEntry,
    TaskDiagnosticError,
    TaskSnapshot,
    collect_task_snapshot,
    create_diagnostic_zip,
    format_task_snapshot,
)
from pdf_trans.task_runs import finish_cli_task, start_cli_task


TASK_ID = "12345678-1234-4abc-8def-1234567890ab"


def test_collects_prefixed_cli_files_sorted_without_symlinks(tmp_path):
    task = start_cli_task(
        tmp_path / "runs",
        "full",
        tmp_path / "paper.pdf",
        task_id=TASK_ID,
    )
    (task.root / "z.json").write_text("{}", encoding="utf-8")
    (task.root / "a.md").write_text("x", encoding="utf-8")
    (task.root / "source.pdf").write_bytes(b"%PDF")
    (task.root / "outside-link").symlink_to(tmp_path / "outside")
    finish_cli_task(task, "succeeded")

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
    )

    assert snapshot.source == "cli"
    assert [entry.display_path for entry in snapshot.entries] == sorted(
        entry.display_path for entry in snapshot.entries
    )
    assert "outside-link" not in {
        entry.display_path for entry in snapshot.entries
    }
    assert "source.pdf" not in {
        entry.display_path for entry in snapshot.entries
    }


def test_collects_legacy_cli_directory_with_warning(tmp_path):
    legacy = tmp_path / "runs" / TASK_ID
    legacy.mkdir(parents=True)
    (legacy / "old.json").write_text("{}", encoding="utf-8")

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
    )

    assert snapshot.source == "cli-legacy"
    assert any("旧格式" in warning for warning in snapshot.warnings)


def test_running_cli_task_reports_snapshot_warning(tmp_path):
    start_cli_task(
        tmp_path / "runs",
        "full",
        tmp_path / "paper.pdf",
        task_id=TASK_ID,
    )

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
    )

    assert any("仍在运行" in warning for warning in snapshot.warnings)


def test_translate_only_collects_exact_references_and_marks_missing(tmp_path):
    normalized = tmp_path / "normalized_content_list.json"
    normalized.write_text("[]", encoding="utf-8")
    translated = tmp_path / "translated_content_list.json"
    translated.write_text("[]", encoding="utf-8")
    rendered = tmp_path / "rendered.md"
    task = start_cli_task(
        tmp_path / "runs",
        "translate-only",
        normalized,
        task_id=TASK_ID,
    )
    finish_cli_task(
        task,
        "failed",
        referenced_artifacts=(normalized, translated, rendered),
        error="render failed",
    )

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
    )

    referenced = {
        entry.display_path: entry
        for entry in snapshot.entries
        if entry.display_path.startswith("referenced/")
    }
    assert set(referenced) == {
        "referenced/normalized_content_list.json",
        "referenced/translated_content_list.json",
        "referenced/rendered.md",
    }
    assert referenced["referenced/rendered.md"].missing is True


def test_collects_all_web_attempts_and_excludes_upload(tmp_path):
    web_root = tmp_path / "web/tasks" / TASK_ID
    (web_root / "upload").mkdir(parents=True)
    (web_root / "upload/source.pdf").write_bytes(b"%PDF")
    for attempt in ("1", "2"):
        path = web_root / f"attempts/{attempt}/result.json"
        path.parent.mkdir(parents=True)
        path.write_text(attempt, encoding="utf-8")
    (web_root / "task.log").write_text("log", encoding="utf-8")

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
    )

    paths = {entry.display_path for entry in snapshot.entries}
    assert paths == {
        "attempts/1/result.json",
        "attempts/2/result.json",
        "task.log",
    }


@pytest.mark.parametrize(
    "task_id",
    ["not-a-uuid", "12345678-1234-4ABC-8def-1234567890ab"],
)
def test_rejects_noncanonical_uuid_without_scanning(tmp_path, task_id):
    with pytest.raises(TaskDiagnosticError, match="UUID"):
        collect_task_snapshot(
            task_id,
            cli_runs_dir=tmp_path / "runs",
            web_data_dir=tmp_path / "web",
        )


def test_reports_checked_paths_when_task_is_missing(tmp_path):
    with pytest.raises(TaskDiagnosticError, match="未找到任务") as caught:
        collect_task_snapshot(
            TASK_ID,
            cli_runs_dir=tmp_path / "runs",
            web_data_dir=tmp_path / "web",
        )
    assert f"cli-{TASK_ID}" in str(caught.value)
    assert f"tasks/{TASK_ID}" in str(caught.value)


@pytest.mark.parametrize(
    ("first", "second"),
    [("new", "legacy"), ("new", "web"), ("legacy", "web")],
)
def test_rejects_multiple_matching_task_roots(tmp_path, first, second):
    roots = {
        "new": tmp_path / "runs" / f"cli-{TASK_ID}",
        "legacy": tmp_path / "runs" / TASK_ID,
        "web": tmp_path / "web/tasks" / TASK_ID,
    }
    roots[first].mkdir(parents=True)
    roots[second].mkdir(parents=True)

    with pytest.raises(TaskDiagnosticError, match="定位冲突"):
        collect_task_snapshot(
            TASK_ID,
            cli_runs_dir=tmp_path / "runs",
            web_data_dir=tmp_path / "web",
        )


def test_legacy_web_logs_become_virtual_task_log(tmp_path, monkeypatch):
    root = tmp_path / "web/tasks" / TASK_ID
    (root / "attempts/1").mkdir(parents=True)
    monkeypatch.setattr(
        "pdf_trans.task_diagnostics._load_web_details",
        lambda database_url, task_id: ("running", b"[INFO] old log\n"),
        raising=False,
    )

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
        database_url="sqlite:///unused.db",
    )

    task_log = next(
        entry for entry in snapshot.entries if entry.display_path == "task.log"
    )
    assert task_log.inline_bytes == b"[INFO] old log\n"
    assert any("仍在运行" in warning for warning in snapshot.warnings)


def test_web_database_failure_keeps_filesystem_artifacts(
    tmp_path, monkeypatch
):
    root = tmp_path / "web/tasks" / TASK_ID
    result = root / "attempts/1/result.json"
    result.parent.mkdir(parents=True)
    result.write_text("{}", encoding="utf-8")

    def fail(database_url, task_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        "pdf_trans.task_diagnostics._load_web_details",
        fail,
        raising=False,
    )
    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
        database_url="sqlite:///missing.db",
    )

    assert [entry.display_path for entry in snapshot.entries] == [
        "attempts/1/result.json"
    ]
    assert any(
        "database unavailable" in warning
        for warning in snapshot.warnings
    )


def test_format_includes_source_root_sizes_missing_and_warnings(tmp_path):
    present = tmp_path / "task.log"
    present.write_bytes(b"x")
    snapshot = TaskSnapshot(
        task_id=TASK_ID,
        source="cli",
        root=tmp_path,
        entries=(
            DiagnosticEntry("task.log", source_path=present),
            DiagnosticEntry(
                "referenced/rendered.md",
                source_path=None,
                missing=True,
            ),
        ),
        warnings=("引用文件缺失",),
    )

    text = format_task_snapshot(snapshot)
    assert f"任务 UUID：{TASK_ID}" in text
    assert "任务来源：cli" in text
    assert "0.00 KB  task.log" in text
    assert "[missing] referenced/rendered.md" in text
    assert "警告：" in text


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (500, "0.50 KB"),
        (1_000_000, "1000.00 KB"),
        (1_000_001, "1.00 MB"),
        (1_250_000, "1.25 MB"),
    ],
)
def test_format_uses_decimal_kb_and_switches_to_mb_above_threshold(
    tmp_path,
    size,
    expected,
):
    snapshot = TaskSnapshot(
        task_id=TASK_ID,
        source="cli",
        root=tmp_path,
        entries=(
            DiagnosticEntry(
                "result.bin",
                source_path=None,
                inline_bytes=b"x" * size,
            ),
        ),
        warnings=(),
    )

    assert f"{expected}  result.bin" in format_task_snapshot(snapshot)


def test_zip_preserves_layout_virtual_log_and_never_overwrites(tmp_path):
    result_file = tmp_path / "result.json"
    result_file.write_text("{}", encoding="utf-8")
    snapshot = TaskSnapshot(
        task_id=TASK_ID,
        source="web",
        root=tmp_path,
        entries=(
            DiagnosticEntry("attempts/1/result.json", result_file),
            DiagnosticEntry(
                "task.log",
                source_path=None,
                inline_bytes=b"[INFO] old log\n",
            ),
        ),
        warnings=(),
    )
    result = create_diagnostic_zip(snapshot, tmp_path)

    assert result.path == tmp_path / f"pdf-trans-{TASK_ID}.zip"
    with ZipFile(result.path) as archive:
        names = set(archive.namelist())
        prefix = f"pdf-trans-{TASK_ID}/"
        assert prefix + "attempts/1/result.json" in names
        assert archive.read(prefix + "task.log") == b"[INFO] old log\n"

    with pytest.raises(TaskDiagnosticError, match="已存在"):
        create_diagnostic_zip(snapshot, tmp_path)


def test_zip_rejects_parent_relative_member_and_removes_partial_file(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    snapshot = TaskSnapshot(
        task_id=TASK_ID,
        source="cli",
        root=tmp_path,
        entries=(DiagnosticEntry("../secret.txt", secret),),
        warnings=(),
    )

    with pytest.raises(TaskDiagnosticError, match="路径越界"):
        create_diagnostic_zip(snapshot, tmp_path)

    assert not (tmp_path / f"pdf-trans-{TASK_ID}.zip").exists()
