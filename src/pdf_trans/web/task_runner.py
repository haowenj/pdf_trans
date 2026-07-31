from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pdf_trans.errors import FormulaAuditError
from pdf_trans.formula_audit import (
    FormulaAuditReport,
    LegacyFormulaAuditError,
    audit_content_list_file,
    log_formula_audit_summary,
    read_formula_audit_file,
)
from pdf_trans.renderer import render_content_list_file
from pdf_trans.workflow import (
    TranslationFileResult,
    WorkflowResult,
    process_pdf,
    process_translation_file,
)
from pdf_trans.web.config import WebSettings
from pdf_trans.web.repository import TaskView
from pdf_trans.web.storage import resolve_stored_path


LOGGER = logging.getLogger(__name__)
_GENERATED_CONTENT_LIST_NAMES = {
    "cleaned_content_list.json",
    "normalized_content_list.json",
    "translated_content_list.json",
}


@dataclass(frozen=True)
class WorkflowServices:
    process_pdf: Callable[..., WorkflowResult]
    process_translation_file: Callable[..., TranslationFileResult]
    render_content_list_file: Callable[..., None]
    audit_content_list_file: Callable[
        [Path, Path],
        FormulaAuditReport,
    ] = audit_content_list_file
    read_formula_audit_file: Callable[
        [Path],
        FormulaAuditReport,
    ] = read_formula_audit_file


@dataclass(frozen=True)
class TaskArtifacts:
    normalized_path: str
    markdown_path: str
    resumed: bool
    summary: str


class AmbiguousCheckpoint(RuntimeError):
    pass


def _original_content_list(normalized: Path) -> Path:
    candidates = sorted(
        path
        for path in normalized.parent.glob("*_content_list.json")
        if path.name not in _GENERATED_CONTENT_LIST_NAMES
    )
    if len(candidates) != 1:
        raise FormulaAuditError(
            "断点续传前需要唯一的 MinerU 原始 content list，"
            f"实际找到 {len(candidates)} 个"
        )
    return candidates[0]


class TaskRunner:
    def __init__(
        self,
        settings: WebSettings,
        services: WorkflowServices | None = None,
    ) -> None:
        self.settings = settings
        self.services = services or WorkflowServices(
            process_pdf,
            process_translation_file,
            render_content_list_file,
        )

    def run(self, task: TaskView) -> TaskArtifacts:
        task_root = self.settings.data_dir / "tasks" / task.id
        checkpoints = sorted(
            task_root.glob("attempts/*/**/normalized_content_list.json")
        )
        if len(checkpoints) > 1:
            raise AmbiguousCheckpoint(
                "发现多个 normalized_content_list.json，无法选择断点"
            )

        if checkpoints:
            normalized = checkpoints[0]
            audit_report = self._ensure_formula_audit(normalized)
            result = self.services.process_translation_file(normalized)
            markdown = normalized.with_name("rendered.md")
            self.services.render_content_list_file(
                result.translated_path,
                markdown,
                formula_audit=audit_report,
            )
            summary = (
                f"断点续传完成：成功 {result.stats.success_count} 段，"
                f"失败 {result.stats.failed_count} 段"
            )
            return self._artifacts(normalized, markdown, True, summary)

        source = resolve_stored_path(
            self.settings.data_dir, task.source_pdf_path
        )
        attempt_dir = task_root / "attempts" / str(task.attempt_count)
        result = self.services.process_pdf(
            source,
            svr_url=self.settings.mineru_url,
            mineru_backend=self.settings.mineru_backend,
            mineru_server_url=self.settings.mineru_server_url,
            data_dir=attempt_dir,
        )
        summary = (
            f"完整工作流完成：输入 {result.before_count} 项，"
            f"过滤 {result.filtered_count} 项，"
            f"跨页候选 {result.candidate_count} 个，"
            f"翻译成功 {result.translation_stats.success_count} 段，"
            f"失败 {result.translation_stats.failed_count} 段"
        )
        return self._artifacts(
            result.normalized_path,
            result.markdown_path,
            False,
            summary,
        )

    def _ensure_formula_audit(
        self,
        normalized: Path,
    ) -> FormulaAuditReport:
        audit_path = normalized.with_name("formula_audit.json")
        if audit_path.is_file():
            try:
                report = self.services.read_formula_audit_file(
                    audit_path
                )
            except LegacyFormulaAuditError:
                source = _original_content_list(normalized)
                return self.services.audit_content_list_file(
                    source,
                    audit_path,
                )
            log_formula_audit_summary(report, LOGGER)
            return report
        source = _original_content_list(normalized)
        return self.services.audit_content_list_file(
            source,
            audit_path,
        )

    def _artifacts(
        self,
        normalized: Path,
        markdown: Path,
        resumed: bool,
        summary: str,
    ) -> TaskArtifacts:
        root = self.settings.data_dir.resolve()
        return TaskArtifacts(
            normalized.resolve().relative_to(root).as_posix(),
            markdown.resolve().relative_to(root).as_posix(),
            resumed,
            summary,
        )
