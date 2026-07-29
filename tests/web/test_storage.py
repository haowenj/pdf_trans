from io import BytesIO

import pytest
from starlette.datastructures import Headers, UploadFile

import pdf_trans.web.storage as storage
from pdf_trans.web.storage import (
    StorageError,
    UploadValidationError,
    resolve_task_asset,
    save_pdf_upload,
)


@pytest.mark.anyio
async def test_save_pdf_uses_uuid_path_and_returns_relative_storage(
    tmp_path,
) -> None:
    upload = UploadFile(
        filename="../../paper.pdf",
        file=BytesIO(b"%PDF-1.7\nbody"),
        headers=Headers({"content-type": "application/pdf"}),
    )

    stored = await save_pdf_upload(
        upload,
        task_id="task-id",
        data_dir=tmp_path,
        max_bytes=100,
    )

    assert stored.original_filename == "paper.pdf"
    assert stored.relative_path == "tasks/task-id/upload/source.pdf"
    assert (tmp_path / stored.relative_path).read_bytes() == b"%PDF-1.7\nbody"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "content_type", "body", "max_bytes"),
    [
        ("paper.txt", "application/pdf", b"%PDF-x", 100),
        ("paper.pdf", "text/plain", b"%PDF-x", 100),
        ("paper.pdf", "application/pdf", b"not-pdf", 100),
        ("paper.pdf", "application/pdf", b"%PDF-too-large", 5),
    ],
)
async def test_save_pdf_rejects_invalid_uploads(
    tmp_path, filename, content_type, body, max_bytes
) -> None:
    upload = UploadFile(
        filename=filename,
        file=BytesIO(body),
        headers=Headers({"content-type": content_type}),
    )

    with pytest.raises(UploadValidationError):
        await save_pdf_upload(
            upload,
            task_id="task-id",
            data_dir=tmp_path,
            max_bytes=max_bytes,
        )

    assert not (tmp_path / "tasks/task-id/upload/source.pdf").exists()


def test_asset_resolution_stays_below_markdown_directory(tmp_path) -> None:
    paper = tmp_path / "tasks/a/attempts/1/paper"
    image = paper / "images/a.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    markdown = paper / "rendered.md"
    markdown.write_text("x", encoding="utf-8")

    assert resolve_task_asset(markdown, "images/a.png") == image.resolve()
    with pytest.raises(StorageError):
        resolve_task_asset(markdown, "../../upload/source.pdf")


def test_delete_task_directory_removes_only_requested_web_task(
    tmp_path,
) -> None:
    task_root = tmp_path / "tasks/task-id"
    artifact = task_root / "attempts/1/paper/rendered.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("translated", encoding="utf-8")
    cli_root = tmp_path / "runs/cli-keep"
    cli_root.mkdir(parents=True)
    cli_manifest = cli_root / "task.json"
    cli_manifest.write_text("{}", encoding="utf-8")

    storage.delete_task_directory(tmp_path, "task-id")

    assert not task_root.exists()
    assert cli_manifest.read_text(encoding="utf-8") == "{}"


def test_delete_task_directory_accepts_missing_task_directory(tmp_path) -> None:
    storage.delete_task_directory(tmp_path, "missing")

    assert not (tmp_path / "tasks/missing").exists()


@pytest.mark.parametrize(
    "task_id",
    ["", ".", "..", "../escape", "nested/task", "/absolute"],
)
def test_delete_task_directory_rejects_unsafe_task_ids(
    tmp_path,
    task_id,
) -> None:
    with pytest.raises(StorageError, match="任务目录路径越界"):
        storage.delete_task_directory(tmp_path, task_id)


def test_delete_task_directory_maps_recursive_delete_errors(
    tmp_path,
    monkeypatch,
) -> None:
    task_root = tmp_path / "tasks/task-id"
    task_root.mkdir(parents=True)

    def fail_delete(path):
        raise OSError("locked")

    monkeypatch.setattr(storage.shutil, "rmtree", fail_delete)

    with pytest.raises(StorageError, match="无法删除任务文件"):
        storage.delete_task_directory(tmp_path, "task-id")

    assert task_root.exists()
