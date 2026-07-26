from __future__ import annotations

from io import BytesIO
from pathlib import Path, PurePosixPath
from shutil import copyfileobj
from typing import Iterable
from zipfile import BadZipFile, ZipFile

from pdf_trans.errors import ArchiveError


def extract_zip(archive_bytes: bytes, output_dir: Path) -> tuple[Path, ...]:
    output_root = output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            for member in archive.infolist():
                normalized_name = member.filename.replace("\\", "/")
                member_path = PurePosixPath(normalized_name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ArchiveError(f"ZIP 包含不安全路径：{member.filename}")

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
