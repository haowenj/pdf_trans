from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path, PurePosixPath
from shutil import copyfileobj
from typing import Iterable
from zipfile import BadZipFile, ZipFile

from pdf_trans.errors import ArchiveError


def extract_zip(
    archive_bytes: bytes,
    output_dir: Path,
    *,
    reserved_paths: Iterable[str] = (),
) -> tuple[Path, ...]:
    output_root = output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    reserved = {PurePosixPath(path) for path in reserved_paths}
    extracted: list[Path] = []

    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            for member in archive.infolist():
                normalized_name = member.filename.replace("\\", "/")
                member_path = PurePosixPath(normalized_name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ArchiveError(f"ZIP 包含不安全路径：{member.filename}")
                if member_path in reserved:
                    raise ArchiveError(
                        f"ZIP 包含保留路径：{member.filename}"
                    )

                target = (output_root / Path(*member_path.parts)).resolve()
                if target != output_root and output_root not in target.parents:
                    raise ArchiveError(f"ZIP 包含不安全路径：{member.filename}")

                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as destination:
                    copyfileobj(source, destination)
                extracted.append(target)
    except ArchiveError:
        raise
    except (BadZipFile, OSError) as exc:
        raise ArchiveError(f"无法解压 MinerU 结果：{exc}") from exc

    return tuple(extracted)


def find_content_list(extracted_paths: Iterable[Path]) -> Path:
    matches = sorted(
        path.resolve()
        for path in extracted_paths
        if path.is_file() and path.name.endswith("_content_list.json")
    )
    if len(matches) != 1:
        raise ArchiveError(
            f"本次 MinerU 结果中应有 1 个 content list，实际找到 {len(matches)} 个"
        )
    return matches[0]


def _annotation_texts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        text.strip()
        for annotation in value
        if isinstance(annotation, dict)
        and isinstance((text := annotation.get("content")), str)
        and text.strip()
    ]


def _structured_items(source: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"无法读取 MinerU structured content：{exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("pages"), list):
        raise ArchiveError("MinerU structured content 缺少 pages")

    items: list[dict[str, object]] = []
    for fallback_page_idx, page in enumerate(payload["pages"]):
        if not isinstance(page, dict):
            continue
        page_idx = page.get("page_idx")
        if type(page_idx) is not int:
            page_idx = fallback_page_idx
        blocks = page.get("blocks")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            content = block.get("content")
            item: dict[str, object] = {"page_idx": page_idx}
            bbox = block.get("bbox")
            if isinstance(bbox, list):
                item["bbox"] = bbox

            if block_type == "table":
                item["type"] = "table"
                if isinstance(content, str) and content.strip():
                    item["table_body"] = content.strip()
                item["table_caption"] = _annotation_texts(block.get("captions"))
                item["table_footnote"] = _annotation_texts(block.get("footnotes"))
            elif block_type in {"image", "chart"}:
                item["type"] = block_type
                item[f"{block_type}_caption"] = _annotation_texts(
                    block.get("captions")
                )
                item[f"{block_type}_footnote"] = _annotation_texts(
                    block.get("footnotes")
                )
            else:
                item["type"] = (
                    block_type
                    if block_type in {"header", "footer", "page_number", "equation"}
                    else "text"
                )
                if isinstance(content, str) and content.strip():
                    item["text"] = content.strip()
                level = block.get("level")
                if block_type == "paragraph_title" and type(level) is int:
                    item["text_level"] = level

            image_source = block.get("image_source")
            if isinstance(image_source, str) and image_source.strip():
                item["img_path"] = image_source.strip()
            items.append(item)
    return items


def resolve_content_list(extracted_paths: Iterable[Path]) -> Path:
    """Use an original content list or materialize one from MinerU v1 output."""
    paths = tuple(extracted_paths)
    matches = sorted(
        path.resolve()
        for path in paths
        if path.is_file() and path.name.endswith("_content_list.json")
    )
    if matches:
        return find_content_list(paths)
    structured = [
        path.resolve()
        for path in paths
        if path.is_file() and path.name == "structured_content.json"
    ]
    if len(structured) != 1:
        raise ArchiveError(
            "本次 MinerU 结果中既无 content list，也无唯一的 "
            f"structured_content.json（找到 {len(structured)} 个）"
        )
    target = structured[0].with_name("structured_content_list.json")
    if target.exists():
        raise ArchiveError(f"MinerU content list 目标文件已存在：{target}")
    items = _structured_items(structured[0])
    try:
        target.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    except OSError as exc:
        raise ArchiveError(f"无法写入 MinerU content list：{exc}") from exc
    return target
