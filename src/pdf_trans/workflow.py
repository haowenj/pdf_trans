from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pdf_trans.archive import extract_zip, find_content_list
from pdf_trans.cleaner import (
    ContentStats,
    clean_content_list_file_with_items,
)
from pdf_trans.client import (
    DEFAULT_MINERU_BACKEND,
    DEFAULT_SVR_URL,
    MinerUClient,
)
from pdf_trans.cross_page import (
    detect_cross_page_candidates,
    write_cross_page_candidates_file,
)
from pdf_trans.errors import WorkflowError
from pdf_trans.logging_utils import logged_stage
from pdf_trans.normalizer import (
    normalize_cross_page_items,
    write_normalized_content_list_file,
)
from pdf_trans.renderer import render_content_list_file
from pdf_trans.translation import (
    TextTranslator,
    TranslationStats,
    translate_content_list_file,
)
from pdf_trans.translation_client import (
    DEFAULT_TRANSLATION_CONCURRENCY,
    DEFAULT_TRANSLATION_MAX_RETRIES,
    OpenAICompatibleTranslator,
)

LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


class PDFParser(Protocol):
    def parse_pdf(self, pdf_path: Path) -> bytes:
        ...


@dataclass(frozen=True)
class WorkflowResult:
    source_path: Path
    output_path: Path
    normalized_path: Path
    normalized_count: int
    markdown_path: Path
    candidates_path: Path
    candidate_count: int
    before_count: int
    filtered_count: int
    after_count: int
    content_stats: ContentStats
    translated_path: Path
    translation_stats: TranslationStats


@dataclass(frozen=True)
class TranslationFileResult:
    normalized_path: Path
    translated_path: Path
    stats: TranslationStats


def validate_pdf_path(pdf_path: Path) -> Path:
    resolved = pdf_path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise WorkflowError(f"PDF 文件不存在或不是普通文件：{pdf_path}")
    if resolved.suffix.lower() != ".pdf":
        raise WorkflowError(f"输入文件必须是 PDF：{pdf_path}")
    return resolved


def validate_normalized_path(normalized_path: Path) -> Path:
    resolved = normalized_path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise WorkflowError(
            f"规范化内容文件不存在或不是普通文件：{normalized_path}"
        )
    if resolved.name != "normalized_content_list.json":
        raise WorkflowError(
            "翻译输入文件必须命名为 normalized_content_list.json"
        )
    return resolved


def _run_translation(
    normalized_path: Path,
    *,
    translator: TextTranslator | None,
    max_retries: int | None,
    concurrency: int | None,
) -> TranslationFileResult:
    translated_path = normalized_path.parent / "translated_content_list.json"
    if translator is None:
        with OpenAICompatibleTranslator.from_env() as translation_client:
            retries = (
                translation_client.max_retries
                if max_retries is None
                else max_retries
            )
            workers = (
                translation_client.concurrency
                if concurrency is None
                else concurrency
            )
            stats = translate_content_list_file(
                normalized_path,
                translated_path,
                translation_client,
                max_retries=retries,
                concurrency=workers,
            )
    else:
        stats = translate_content_list_file(
            normalized_path,
            translated_path,
            translator,
            max_retries=(
                DEFAULT_TRANSLATION_MAX_RETRIES
                if max_retries is None
                else max_retries
            ),
            concurrency=(
                DEFAULT_TRANSLATION_CONCURRENCY
                if concurrency is None
                else concurrency
            ),
        )
    return TranslationFileResult(
        normalized_path=normalized_path.resolve(),
        translated_path=translated_path.resolve(),
        stats=stats,
    )


def process_translation_file(
    normalized_path: Path,
    *,
    translator: TextTranslator | None = None,
    max_retries: int | None = None,
    concurrency: int | None = None,
) -> TranslationFileResult:
    with logged_stage(
        LOGGER,
        "仅翻译工作流",
        f"从 {normalized_path} 校验断点并翻译全部 text",
    ) as workflow_stage:
        with logged_stage(
            LOGGER,
            "校验规范化文件",
            "确认文件存在且名称为 normalized_content_list.json",
        ) as validation_stage:
            resolved = validate_normalized_path(normalized_path)
            validation_stage.set_result(f"输入文件 {resolved}")
        with logged_stage(
            LOGGER,
            "翻译 text 对象",
            "按配置的线程数逐段调用 OpenAI 兼容接口",
        ) as translation_stage:
            result = _run_translation(
                resolved,
                translator=translator,
                max_retries=max_retries,
                concurrency=concurrency,
            )
            translation_stage.set_result(
                f"success {result.stats.success_count} 段，"
                f"failed {result.stats.failed_count} 段"
            )
        workflow_stage.set_result(f"输出翻译文件 {result.translated_path}")
        return result


def _text_section_numbers(items: list[object]) -> dict[int, int]:
    result: dict[int, int] = {}
    section_number = 0
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("type") == "text":
            section_number += 1
            result[index] = section_number
    return result


def _log_cross_page_candidates(
    items: list[object],
    candidates: list[dict[str, object]],
) -> None:
    section_numbers = _text_section_numbers(items)
    for candidate in candidates:
        previous_index = candidate["previous_index"]
        next_index = candidate["next_index"]
        LOGGER.warning(
            "检测到第 %d 段（数组第 %d 项）和第 %d 段（数组第 %d 项）"
            "被分页分裂，将合并为一个段落",
            section_numbers[previous_index],
            previous_index + 1,
            section_numbers[next_index],
            next_index + 1,
        )


def _write_bytes_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("xb") as handle:
            handle.write(value)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _process_pdf_stages(
    pdf_path: Path,
    *,
    svr_url: str,
    mineru_backend: str,
    mineru_server_url: str | None,
    data_dir: Path | None,
    client: PDFParser | None,
    translator: TextTranslator | None,
    translation_max_retries: int | None,
    translation_concurrency: int | None,
) -> WorkflowResult:
    with logged_stage(
        LOGGER,
        "校验 PDF",
        "确认输入存在并且扩展名为 .pdf",
    ) as stage:
        resolved_pdf = validate_pdf_path(pdf_path)
        stage.set_result(f"输入文件 {resolved_pdf}")

    output_root = (data_dir or DEFAULT_DATA_DIR).resolve()
    with logged_stage(
        LOGGER,
        "调用 MinerU",
        "上传 PDF、轮询解析状态并下载 ZIP",
    ) as stage:
        if client is None:
            with MinerUClient(
                svr_url=svr_url,
                backend=mineru_backend,
                server_url=mineru_server_url,
            ) as mineru_client:
                archive_bytes = mineru_client.parse_pdf(resolved_pdf)
        else:
            archive_bytes = client.parse_pdf(resolved_pdf)
        stage.set_result(f"收到 {len(archive_bytes)} 字节 ZIP 数据")

    archive_path = output_root / "mineru_result.zip"
    _write_bytes_atomic(archive_path, archive_bytes)

    with logged_stage(
        LOGGER,
        "解压解析结果",
        f"解压 ZIP 到 {output_root} 并定位 content list",
    ) as stage:
        extracted_paths = extract_zip(
            archive_bytes,
            output_root,
            reserved_paths=(archive_path.name,),
        )
        source_path = find_content_list(extracted_paths)
        stage.set_result(
            f"解压 {len(extracted_paths)} 个文件，content list 为 {source_path}"
        )

    output_path = source_path.parent / "cleaned_content_list.json"
    with logged_stage(
        LOGGER,
        "清洗数据",
        "移除 header、footer、page_number 和空 text",
    ) as stage:
        cleaned_items, stats = clean_content_list_file_with_items(
            source_path,
            output_path,
        )
        stage.set_result(
            f"输入 {stats.before_count} 项，过滤 {stats.filtered_count} 项，"
            f"保留 {stats.after_count} 项"
        )

    candidates_path = output_path.parent / "cross_page_candidates.json"
    with logged_stage(
        LOGGER,
        "检测跨页段落",
        "检查连续页面上的相邻 text 并写出候选报告",
    ) as stage:
        candidates = detect_cross_page_candidates(cleaned_items)
        _log_cross_page_candidates(cleaned_items, candidates)
        write_cross_page_candidates_file(candidates, candidates_path)
        stage.set_result(f"发现 {len(candidates)} 个候选")

    normalized_path = output_path.parent / "normalized_content_list.json"
    with logged_stage(
        LOGGER,
        "合并跨页段落",
        "按候选链合并 text 并写出规范化内容",
    ) as stage:
        normalized_items = normalize_cross_page_items(
            cleaned_items,
            candidates,
        )
        write_normalized_content_list_file(
            normalized_items,
            normalized_path,
        )
        stage.set_result(f"写出 {len(normalized_items)} 个对象")

    with logged_stage(
        LOGGER,
        "翻译 text 对象",
        "按配置的线程数逐段调用 OpenAI 兼容接口",
    ) as stage:
        translation_result = _run_translation(
            normalized_path,
            translator=translator,
            max_retries=translation_max_retries,
            concurrency=translation_concurrency,
        )
        stage.set_result(
            f"success {translation_result.stats.success_count} 段，"
            f"failed {translation_result.stats.failed_count} 段"
        )

    markdown_path = output_path.parent / "rendered.md"
    with logged_stage(
        LOGGER,
        "渲染 Markdown",
        "按翻译结果对象顺序生成 rendered.md",
    ) as stage:
        render_content_list_file(
            translation_result.translated_path,
            markdown_path,
        )
        stage.set_result(f"输出文件 {markdown_path.resolve()}")

    return WorkflowResult(
        source_path=source_path,
        output_path=output_path.resolve(),
        normalized_path=normalized_path.resolve(),
        normalized_count=len(normalized_items),
        markdown_path=markdown_path.resolve(),
        candidates_path=candidates_path.resolve(),
        candidate_count=len(candidates),
        before_count=stats.before_count,
        filtered_count=stats.filtered_count,
        after_count=stats.after_count,
        content_stats=stats.content_stats,
        translated_path=translation_result.translated_path,
        translation_stats=translation_result.stats,
    )


def process_pdf(
    pdf_path: Path,
    *,
    svr_url: str = DEFAULT_SVR_URL,
    mineru_backend: str = DEFAULT_MINERU_BACKEND,
    mineru_server_url: str | None = None,
    data_dir: Path | None = None,
    client: PDFParser | None = None,
    translator: TextTranslator | None = None,
    translation_max_retries: int | None = None,
    translation_concurrency: int | None = None,
) -> WorkflowResult:
    with logged_stage(
        LOGGER,
        "完整 PDF 工作流",
        f"解析、清洗、规范化并翻译 {pdf_path}",
    ) as workflow_stage:
        result = _process_pdf_stages(
            pdf_path,
            svr_url=svr_url,
            mineru_backend=mineru_backend,
            mineru_server_url=mineru_server_url,
            data_dir=data_dir,
            client=client,
            translator=translator,
            translation_max_retries=translation_max_retries,
            translation_concurrency=translation_concurrency,
        )
        workflow_stage.set_result(f"输出翻译文件 {result.translated_path}")
        return result
