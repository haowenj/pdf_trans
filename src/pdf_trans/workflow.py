from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pdf_trans.archive import extract_zip, find_content_list
from pdf_trans.cleaner import (
    ContentStats,
    clean_content_list_file_with_items,
)
from pdf_trans.client import DEFAULT_SVR_URL, MinerUClient
from pdf_trans.cross_page import (
    detect_cross_page_candidates,
    write_cross_page_candidates_file,
)
from pdf_trans.errors import WorkflowError
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
from pdf_trans.translation_client import OpenAICompatibleTranslator

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
) -> TranslationFileResult:
    translated_path = normalized_path.parent / "translated_content_list.json"
    if translator is None:
        with OpenAICompatibleTranslator.from_env() as translation_client:
            retries = (
                translation_client.max_retries
                if max_retries is None
                else max_retries
            )
            stats = translate_content_list_file(
                normalized_path,
                translated_path,
                translation_client,
                max_retries=retries,
            )
    else:
        stats = translate_content_list_file(
            normalized_path,
            translated_path,
            translator,
            max_retries=(1 if max_retries is None else max_retries),
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
) -> TranslationFileResult:
    resolved = validate_normalized_path(normalized_path)
    return _run_translation(
        resolved,
        translator=translator,
        max_retries=max_retries,
    )


def process_pdf(
    pdf_path: Path,
    *,
    svr_url: str = DEFAULT_SVR_URL,
    data_dir: Path | None = None,
    client: PDFParser | None = None,
    translator: TextTranslator | None = None,
    translation_max_retries: int | None = None,
) -> WorkflowResult:
    resolved_pdf = validate_pdf_path(pdf_path)
    output_root = (data_dir or DEFAULT_DATA_DIR).resolve()

    if client is None:
        with MinerUClient(svr_url=svr_url) as mineru_client:
            archive_bytes = mineru_client.parse_pdf(resolved_pdf)
    else:
        archive_bytes = client.parse_pdf(resolved_pdf)

    extracted_paths = extract_zip(archive_bytes, output_root)
    source_path = find_content_list(extracted_paths)
    output_path = source_path.parent / "cleaned_content_list.json"
    cleaned_items, stats = clean_content_list_file_with_items(
        source_path,
        output_path,
    )

    candidates = detect_cross_page_candidates(cleaned_items)
    candidates_path = output_path.parent / "cross_page_candidates.json"
    write_cross_page_candidates_file(
        candidates,
        candidates_path,
    )

    normalized_items = normalize_cross_page_items(
        cleaned_items,
        candidates,
    )
    normalized_path = output_path.parent / "normalized_content_list.json"
    write_normalized_content_list_file(
        normalized_items,
        normalized_path,
    )

    translation_result = _run_translation(
        normalized_path,
        translator=translator,
        max_retries=translation_max_retries,
    )

    markdown_path = output_path.parent / "rendered.md"
    render_content_list_file(normalized_path, markdown_path)
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
