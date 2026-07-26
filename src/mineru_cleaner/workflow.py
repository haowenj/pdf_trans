from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mineru_cleaner.archive import extract_zip, find_content_list
from mineru_cleaner.cleaner import ContentStats, clean_content_list_file
from mineru_cleaner.client import DEFAULT_SVR_URL, MinerUClient
from mineru_cleaner.cross_page import detect_cross_page_candidates_file
from mineru_cleaner.errors import WorkflowError
from mineru_cleaner.renderer import render_content_list_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


class PDFParser(Protocol):
    def parse_pdf(self, pdf_path: Path) -> bytes:
        ...


@dataclass(frozen=True)
class WorkflowResult:
    source_path: Path
    output_path: Path
    markdown_path: Path
    candidates_path: Path
    candidate_count: int
    before_count: int
    filtered_count: int
    after_count: int
    content_stats: ContentStats


def validate_pdf_path(pdf_path: Path) -> Path:
    resolved = pdf_path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise WorkflowError(f"PDF 文件不存在或不是普通文件：{pdf_path}")
    if resolved.suffix.lower() != ".pdf":
        raise WorkflowError(f"输入文件必须是 PDF：{pdf_path}")
    return resolved


def process_pdf(
    pdf_path: Path,
    *,
    svr_url: str = DEFAULT_SVR_URL,
    data_dir: Path | None = None,
    client: PDFParser | None = None,
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
    stats = clean_content_list_file(source_path, output_path)
    markdown_path = output_path.parent / "rendered.md"
    render_content_list_file(output_path, markdown_path)
    candidates_path = output_path.parent / "cross_page_candidates.json"
    candidate_count = detect_cross_page_candidates_file(
        output_path,
        candidates_path,
    )
    return WorkflowResult(
        source_path=source_path,
        output_path=output_path.resolve(),
        markdown_path=markdown_path.resolve(),
        candidates_path=candidates_path.resolve(),
        candidate_count=candidate_count,
        before_count=stats.before_count,
        filtered_count=stats.filtered_count,
        after_count=stats.after_count,
        content_stats=stats.content_stats,
    )
