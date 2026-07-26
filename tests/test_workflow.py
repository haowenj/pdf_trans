import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from mineru_cleaner.cleaner import ContentStats
from mineru_cleaner.errors import NormalizationError, WorkflowError
from mineru_cleaner.workflow import process_pdf


class FakeMinerUClient:
    def __init__(self, archive_bytes: bytes) -> None:
        self.archive_bytes = archive_bytes
        self.received_path = None

    def parse_pdf(self, pdf_path):
        self.received_path = pdf_path
        return self.archive_bytes


def make_result_zip(items) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "paper/hybrid_auto/paper_content_list.json",
            json.dumps(items, ensure_ascii=False),
        )
        archive.writestr("paper/hybrid_auto/images/a.jpg", b"image")
    return buffer.getvalue()


def test_process_pdf_runs_complete_workflow(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    body = {
        "type": "text",
        "text": "正文",
        "page_idx": 0,
        "bbox": [1, 2, 3, 4],
    }
    client = FakeMinerUClient(
        make_result_zip(
            [
                {"type": "header", "text": "页眉"},
                body,
                {"type": "chart", "img_path": "images/a.jpg"},
            ]
        )
    )

    result = process_pdf(pdf, data_dir=tmp_path / "data", client=client)

    assert client.received_path == pdf.resolve()
    assert result.before_count == 3
    assert result.filtered_count == 1
    assert result.after_count == 2
    assert result.normalized_count == 2
    assert result.content_stats == ContentStats(
        type_counts={"chart": 1, "text": 1},
        text_level_count=0,
        text_level_counts={},
        page_idx_counts={0: 1},
    )
    assert result.markdown_path == (
        tmp_path / "data/paper/hybrid_auto/rendered.md"
    ).resolve()
    assert result.markdown_path.read_text(encoding="utf-8") == (
        "正文\n\n"
        "![](images/a.jpg)\n"
    )
    assert result.output_path == (
        tmp_path / "data/paper/hybrid_auto/cleaned_content_list.json"
    ).resolve()
    assert result.normalized_path == (
        tmp_path / "data/paper/hybrid_auto/normalized_content_list.json"
    ).resolve()
    assert json.loads(result.output_path.read_text(encoding="utf-8")) == [
        body,
        {"type": "chart", "img_path": "images/a.jpg"},
    ]
    assert json.loads(
        result.normalized_path.read_text(encoding="utf-8")
    ) == [
        body,
        {"type": "chart", "img_path": "images/a.jpg"},
    ]
    assert (
        tmp_path / "data/paper/hybrid_auto/images/a.jpg"
    ).read_bytes() == b"image"


def test_process_pdf_writes_cross_page_report_without_changing_other_outputs(
    tmp_path,
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    previous = {"type": "text", "text": "上一页未结束", "page_idx": 0}
    next_item = {"type": "text", "text": "下一页继续。", "page_idx": 1}
    client = FakeMinerUClient(make_result_zip([previous, next_item]))

    result = process_pdf(pdf, data_dir=tmp_path / "data", client=client)

    assert result.candidate_count == 1
    assert result.candidates_path == (
        tmp_path / "data/paper/hybrid_auto/cross_page_candidates.json"
    ).resolve()
    assert json.loads(result.candidates_path.read_text(encoding="utf-8")) == [
        {
            "previous_index": 0,
            "next_index": 1,
            "previous_page_idx": 0,
            "next_page_idx": 1,
            "previous_text": "上一页未结束",
            "next_text": "下一页继续。",
            "reason": (
                "相邻 text 位于连续页面，且前一个 text "
                "未以完整句结束符 . ! ? : ; 结尾"
            ),
        }
    ]
    assert json.loads(result.output_path.read_text(encoding="utf-8")) == [
        previous,
        next_item,
    ]
    assert result.normalized_count == 1
    assert result.normalized_path == (
        tmp_path / "data/paper/hybrid_auto/normalized_content_list.json"
    ).resolve()
    assert json.loads(
        result.normalized_path.read_text(encoding="utf-8")
    ) == [
        {
            "type": "text",
            "text": "上一页未结束 下一页继续。",
            "page_idx": 0,
            "source_page_indices": [0, 1],
            "source_bboxes": [None, None],
            "merged_cross_page": True,
        }
    ]
    assert result.markdown_path.read_text(encoding="utf-8") == (
        "上一页未结束 下一页继续。\n"
    )


def test_process_pdf_passes_same_in_memory_items_and_candidates_to_normalizer(
    tmp_path,
    monkeypatch,
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    client = FakeMinerUClient(
        make_result_zip(
            [
                {"type": "text", "text": "前", "page_idx": 0},
                {"type": "text", "text": "后", "page_idx": 1},
            ]
        )
    )
    seen = {}

    from mineru_cleaner import workflow

    real_detect = workflow.detect_cross_page_candidates
    real_normalize = workflow.normalize_cross_page_items

    def tracking_detect(items):
        candidates = real_detect(items)
        seen["detected_items"] = items
        seen["detected_candidates"] = candidates
        return candidates

    def tracking_normalize(items, candidates):
        seen["normalized_items"] = items
        seen["normalized_candidates"] = candidates
        return real_normalize(items, candidates)

    monkeypatch.setattr(
        workflow,
        "detect_cross_page_candidates",
        tracking_detect,
    )
    monkeypatch.setattr(
        workflow,
        "normalize_cross_page_items",
        tracking_normalize,
    )

    process_pdf(pdf, data_dir=tmp_path / "data", client=client)

    assert seen["normalized_items"] is seen["detected_items"]
    assert seen["normalized_candidates"] is seen["detected_candidates"]


def test_process_pdf_does_not_write_normalized_when_validation_fails(
    tmp_path,
    monkeypatch,
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    client = FakeMinerUClient(
        make_result_zip(
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
                {"type": "text", "text": "C", "page_idx": 2},
            ]
        )
    )
    invalid_candidates = [{"previous_index": 0, "next_index": 2}]
    monkeypatch.setattr(
        "mineru_cleaner.workflow.detect_cross_page_candidates",
        lambda items: invalid_candidates,
    )
    output_dir = tmp_path / "data/paper/hybrid_auto"

    with pytest.raises(NormalizationError, match="索引必须相邻"):
        process_pdf(pdf, data_dir=tmp_path / "data", client=client)

    assert not (output_dir / "normalized_content_list.json").exists()
    assert not (output_dir / "rendered.md").exists()
    assert json.loads(
        (output_dir / "cross_page_candidates.json").read_text(
            encoding="utf-8"
        )
    ) == invalid_candidates


def test_process_pdf_passes_svr_url_to_created_client(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    archive_bytes = make_result_zip([{"type": "text", "text": "正文"}])
    received = {}

    class ContextClient:
        def __init__(self, *, svr_url):
            received["svr_url"] = svr_url

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def parse_pdf(self, pdf_path):
            return archive_bytes

    monkeypatch.setattr("mineru_cleaner.workflow.MinerUClient", ContextClient)

    process_pdf(
        pdf,
        svr_url="http://mineru.internal:7200",
        data_dir=tmp_path / "data",
    )

    assert received["svr_url"] == "http://mineru.internal:7200"


@pytest.mark.parametrize(
    "filename, create_file",
    [
        ("missing.pdf", False),
        ("document.txt", True),
    ],
)
def test_process_pdf_rejects_invalid_input(tmp_path, filename, create_file):
    source = tmp_path / filename
    if create_file:
        source.write_text("not pdf", encoding="utf-8")

    with pytest.raises(WorkflowError, match="PDF"):
        process_pdf(source, data_dir=tmp_path / "data")
