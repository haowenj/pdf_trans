from io import BytesIO

import pytest
from starlette.datastructures import Headers, UploadFile

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
