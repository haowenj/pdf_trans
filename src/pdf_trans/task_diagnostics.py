from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from pdf_trans.errors import PDFTransError
from pdf_trans.task_runs import read_task_manifest


class TaskDiagnosticError(PDFTransError):
    pass


@dataclass(frozen=True)
class DiagnosticEntry:
    display_path: str
    source_path: Path | None
    inline_bytes: bytes | None = None
    missing: bool = False

    @property
    def size(self) -> int | None:
        if self.missing:
            return None
        if self.inline_bytes is not None:
            return len(self.inline_bytes)
        if self.source_path is None:
            return None
        try:
            return self.source_path.stat().st_size
        except OSError:
            return None


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    source: str
    root: Path
    entries: tuple[DiagnosticEntry, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ZipResult:
    path: Path
    warnings: tuple[str, ...]


def _canonical_uuid(value: str) -> str:
    try:
        parsed = str(UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise TaskDiagnosticError(f"任务 UUID 格式错误：{value}") from exc
    if parsed != value:
        raise TaskDiagnosticError(
            f"任务 UUID 必须使用标准小写格式：{value}"
        )
    return parsed


def _regular_files(root: Path) -> list[DiagnosticEntry]:
    entries: list[DiagnosticEntry] = []
    for path in root.rglob("*"):
        if (
            path.is_symlink()
            or not path.is_file()
            or path.suffix.lower() == ".pdf"
        ):
            continue
        relative = path.relative_to(root).as_posix()
        entries.append(DiagnosticEntry(relative, path))
    return entries


def _load_web_details(
    database_url: str,
    task_id: str,
) -> tuple[str, bytes]:
    from pdf_trans.web.db import make_engine, make_session_factory
    from pdf_trans.web.repository import TaskRepository

    repository = TaskRepository(
        make_session_factory(make_engine(database_url))
    )
    task = repository.get_task(task_id)
    lines: list[str] = []
    cursor = 0
    while True:
        logs = repository.list_logs(task_id, after_id=cursor, limit=500)
        for log in logs:
            cursor = log.id
            lines.append(
                f"{log.created_at.isoformat()} "
                f"[{log.level}] {log.message}\n"
            )
        if len(logs) < 500:
            break
    return task.status, "".join(lines).encode("utf-8")


def collect_task_snapshot(
    task_id: str,
    *,
    cli_runs_dir: Path,
    web_data_dir: Path,
    database_url: str | None = None,
) -> TaskSnapshot:
    identifier = _canonical_uuid(task_id)
    candidates = (
        ("cli", cli_runs_dir / f"cli-{identifier}"),
        ("cli-legacy", cli_runs_dir / identifier),
        ("web", web_data_dir / "tasks" / identifier),
    )
    matches = [
        (source, root)
        for source, root in candidates
        if not root.is_symlink() and root.is_dir()
    ]
    if not matches:
        checked = "、".join(str(root) for _, root in candidates)
        raise TaskDiagnosticError(
            f"未找到任务 {identifier}；已检查：{checked}"
        )
    if len(matches) != 1:
        locations = "、".join(str(root) for _, root in matches)
        raise TaskDiagnosticError(
            f"任务定位冲突 {identifier}：{locations}"
        )

    source, root = matches[0]
    entries: list[DiagnosticEntry] = []
    warnings: list[str] = []
    if source == "cli-legacy":
        entries.extend(_regular_files(root))
        warnings.append(
            "旧格式 CLI 任务没有 task.json、task.log 和保留的 MinerU ZIP"
        )
    elif source == "cli":
        manifest = read_task_manifest(root / "task.json")
        if manifest["task_id"] != identifier:
            raise TaskDiagnosticError("任务清单 UUID 与目录不一致")
        entries.extend(_regular_files(root))
        if manifest["status"] == "running":
            warnings.append("任务仍在运行，当前内容只是变化中的快照")
        if manifest["task_type"] == "translate-only":
            allowed = {
                "normalized_content_list.json",
                "translated_content_list.json",
                "rendered.md",
            }
            seen: set[str] = set()
            references = manifest["referenced_artifacts"]
            assert isinstance(references, list)
            for raw_path in references:
                assert isinstance(raw_path, str)
                path = Path(raw_path)
                if path.name not in allowed or path.name in seen:
                    raise TaskDiagnosticError(
                        f"任务清单包含非法产物引用：{raw_path}"
                    )
                seen.add(path.name)
                available = not path.is_symlink() and path.is_file()
                entries.append(
                    DiagnosticEntry(
                        f"referenced/{path.name}",
                        source_path=path if available else None,
                        missing=not available,
                    )
                )
                if not available:
                    warnings.append(f"引用文件缺失：{path}")
    else:
        attempts = root / "attempts"
        if not attempts.is_symlink() and attempts.is_dir():
            entries.extend(
                DiagnosticEntry(
                    f"attempts/{entry.display_path}",
                    entry.source_path,
                    entry.inline_bytes,
                    entry.missing,
                )
                for entry in _regular_files(attempts)
            )
        task_log = root / "task.log"
        has_file_log = not task_log.is_symlink() and task_log.is_file()
        if has_file_log:
            entries.append(DiagnosticEntry("task.log", task_log))
        if database_url is not None:
            try:
                status, log_bytes = _load_web_details(
                    database_url,
                    identifier,
                )
                if status == "running":
                    warnings.append(
                        "任务仍在运行，当前内容只是变化中的快照"
                    )
                if not has_file_log and log_bytes:
                    entries.append(
                        DiagnosticEntry(
                            "task.log",
                            source_path=None,
                            inline_bytes=log_bytes,
                        )
                    )
            except Exception as exc:
                warnings.append(f"无法读取 Web 数据库日志：{exc}")
        elif not has_file_log:
            warnings.append("未配置 Web 数据库，无法导出旧任务日志")

    entries.sort(key=lambda entry: entry.display_path)
    return TaskSnapshot(
        identifier,
        source,
        root.resolve(),
        tuple(entries),
        tuple(warnings),
    )


def format_task_snapshot(snapshot: TaskSnapshot) -> str:
    lines = [
        f"任务来源：{snapshot.source}",
        f"任务 UUID：{snapshot.task_id}",
        f"任务目录：{snapshot.root.resolve()}",
        "诊断文件：",
    ]
    for entry in sorted(
        snapshot.entries,
        key=lambda item: item.display_path,
    ):
        size = entry.size
        if entry.missing or size is None:
            lines.append(f"  [missing] {entry.display_path}")
        else:
            lines.append(f"  {size} B  {entry.display_path}")
    for warning in snapshot.warnings:
        lines.append(f"警告：{warning}")
    return "\n".join(lines) + "\n"


def create_diagnostic_zip(
    snapshot: TaskSnapshot,
    output_dir: Path,
) -> ZipResult:
    from zipfile import ZIP_DEFLATED, ZipFile

    target = output_dir / f"pdf-trans-{snapshot.task_id}.zip"
    prefix = PurePosixPath(f"pdf-trans-{snapshot.task_id}")
    warnings: list[str] = []
    try:
        with target.open("xb") as raw:
            with ZipFile(raw, "w", compression=ZIP_DEFLATED) as archive:
                for entry in snapshot.entries:
                    if entry.missing:
                        continue
                    member = prefix / PurePosixPath(entry.display_path)
                    if member.is_absolute() or ".." in member.parts:
                        raise TaskDiagnosticError(
                            f"ZIP 条目路径越界：{entry.display_path}"
                        )
                    if entry.inline_bytes is not None:
                        archive.writestr(
                            member.as_posix(),
                            entry.inline_bytes,
                        )
                    elif (
                        entry.source_path is None
                        or entry.source_path.is_symlink()
                        or not entry.source_path.is_file()
                    ):
                        warnings.append(
                            f"打包时文件已缺失：{entry.display_path}"
                        )
                    else:
                        try:
                            archive.write(
                                entry.source_path,
                                arcname=member.as_posix(),
                            )
                        except FileNotFoundError:
                            warnings.append(
                                f"打包时文件已缺失：{entry.display_path}"
                            )
    except FileExistsError as exc:
        raise TaskDiagnosticError(f"诊断 ZIP 已存在：{target}") from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return ZipResult(target.resolve(), tuple(warnings))
