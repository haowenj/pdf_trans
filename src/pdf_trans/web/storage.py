from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePath

from starlette.datastructures import UploadFile

CHUNK_SIZE = 1024 * 1024
PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}


class StorageError(RuntimeError):
    pass


class UploadValidationError(StorageError):
    pass


@dataclass(frozen=True)
class StoredUpload:
    original_filename: str
    relative_path: str


async def save_pdf_upload(
    upload: UploadFile,
    *,
    task_id: str,
    data_dir: Path,
    max_bytes: int,
) -> StoredUpload:
    original = PurePath(upload.filename or "").name[:255]
    if not original or Path(original).suffix.lower() != ".pdf":
        raise UploadValidationError("只能上传扩展名为 .pdf 的文件")
    if upload.content_type not in PDF_CONTENT_TYPES:
        raise UploadValidationError("上传文件的 Content-Type 必须是 PDF")

    relative = Path("tasks") / task_id / "upload/source.pdf"
    target = data_dir / relative
    temporary = target.with_suffix(".pdf.part")
    target.parent.mkdir(parents=True, exist_ok=False)
    size = 0
    prefix = b""

    try:
        with temporary.open("xb") as handle:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadValidationError("PDF 超过上传大小限制")
                if len(prefix) < 5:
                    prefix = (prefix + chunk)[:5]
                handle.write(chunk)
        if prefix != b"%PDF-":
            raise UploadValidationError("文件头不是有效 PDF")
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        shutil.rmtree(target.parents[1], ignore_errors=True)
        raise
    finally:
        await upload.close()

    return StoredUpload(original, relative.as_posix())


def resolve_stored_path(data_dir: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise StorageError("任务文件路径越界")
    root = data_dir.resolve()
    candidate = (root / relative).resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise StorageError("任务文件路径越界")
    return candidate


def resolve_task_asset(markdown_path: Path, asset_path: str) -> Path:
    relative = Path(asset_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise StorageError("任务资源路径越界")
    root = markdown_path.resolve(strict=True).parent
    candidate = (root / relative).resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise StorageError("任务资源路径越界")
    return candidate
