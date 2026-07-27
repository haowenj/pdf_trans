import json
import logging
from io import BytesIO
from zipfile import ZipFile

import pytest

from pdf_trans.cleaner import ContentStats
from pdf_trans.errors import NormalizationError, WorkflowError
from pdf_trans.translation import TranslationStats
from pdf_trans.workflow import process_pdf, process_translation_file


class FakeMinerUClient:
    def __init__(self, archive_bytes: bytes) -> None:
        self.archive_bytes = archive_bytes
        self.received_path = None

    def parse_pdf(self, pdf_path):
        self.received_path = pdf_path
        return self.archive_bytes


class FakeTranslator:
    def __init__(self):
        self.received = []

    def translate(self, text):
        self.received.append(text)
        return f"译文：{text}"


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
    translator = FakeTranslator()

    result = process_pdf(
        pdf,
        data_dir=tmp_path / "data",
        client=client,
        translator=translator,
    )

    assert client.received_path == pdf.resolve()
    assert result.before_count == 3
    assert result.filtered_count == 1
    assert result.after_count == 2
    assert result.normalized_count == 2
    assert translator.received == ["正文"]
    assert result.translated_path == (
        tmp_path / "data/paper/hybrid_auto/translated_content_list.json"
    ).resolve()
    assert result.translation_stats == TranslationStats(1, 1, 0, 1, 0, 0)
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
        "译文：正文\n\n"
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
    assert json.loads(
        result.translated_path.read_text(encoding="utf-8")
    ) == [
        {
            **body,
            "translated_text": "译文：正文",
            "translation_status": "success",
        },
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
    translator = FakeTranslator()

    result = process_pdf(
        pdf,
        data_dir=tmp_path / "data",
        client=client,
        translator=translator,
    )

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
        "译文：上一页未结束 下一页继续。\n"
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
    translator = FakeTranslator()
    seen = {}

    from pdf_trans import workflow

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

    process_pdf(
        pdf,
        data_dir=tmp_path / "data",
        client=client,
        translator=translator,
    )

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
        "pdf_trans.workflow.detect_cross_page_candidates",
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
    translator = FakeTranslator()

    class ContextClient:
        def __init__(self, *, svr_url):
            received["svr_url"] = svr_url

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def parse_pdf(self, pdf_path):
            return archive_bytes

    monkeypatch.setattr("pdf_trans.workflow.MinerUClient", ContextClient)

    process_pdf(
        pdf,
        svr_url="http://mineru.internal:7200",
        data_dir=tmp_path / "data",
        translator=translator,
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


def test_process_translation_file_runs_resumeable_translation(tmp_path):
    normalized = tmp_path / "normalized_content_list.json"
    normalized.write_text(
        json.dumps([{"type": "text", "text": "正文"}, {"type": "image"}]),
        encoding="utf-8",
    )
    translator = FakeTranslator()

    result = process_translation_file(normalized, translator=translator)

    assert result.normalized_path == normalized.resolve()
    assert result.translated_path == (
        tmp_path / "translated_content_list.json"
    ).resolve()
    assert result.stats == TranslationStats(1, 1, 0, 1, 0, 0)


def test_process_translation_file_requires_exact_normalized_filename(tmp_path):
    source = tmp_path / "other.json"
    source.write_text("[]", encoding="utf-8")

    with pytest.raises(WorkflowError, match="normalized_content_list.json"):
        process_translation_file(source, translator=FakeTranslator())


def test_process_translation_file_passes_explicit_concurrency(
    tmp_path,
    monkeypatch,
):
    normalized = tmp_path / "normalized_content_list.json"
    normalized.write_text("[]", encoding="utf-8")
    received = {}

    def fake_translate(source, output, translator, **kwargs):
        received.update(kwargs)
        return TranslationStats(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr(
        "pdf_trans.workflow.translate_content_list_file",
        fake_translate,
    )

    process_translation_file(
        normalized,
        translator=FakeTranslator(),
        max_retries=2,
        concurrency=7,
    )

    assert received == {"max_retries": 2, "concurrency": 7}


def test_process_translation_file_uses_client_retry_and_concurrency(
    tmp_path,
    monkeypatch,
):
    normalized = tmp_path / "normalized_content_list.json"
    normalized.write_text("[]", encoding="utf-8")
    received = {}

    class ContextTranslator:
        max_retries = 3
        concurrency = 4

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def translate(self, text):
            return text

    monkeypatch.setattr(
        "pdf_trans.workflow.OpenAICompatibleTranslator.from_env",
        lambda: ContextTranslator(),
    )

    def fake_translate(source, output, translator, **kwargs):
        received.update(kwargs)
        return TranslationStats(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr(
        "pdf_trans.workflow.translate_content_list_file",
        fake_translate,
    )

    process_translation_file(normalized)

    assert received == {"max_retries": 3, "concurrency": 4}


def test_process_pdf_logs_stage_actions_counts_and_cross_page_merge(
    tmp_path,
    caplog,
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    client = FakeMinerUClient(
        make_result_zip(
            [
                {"type": "header", "text": "页眉"},
                {"type": "text", "text": "上一页未结束", "page_idx": 0},
                {"type": "text", "text": "下一页继续。", "page_idx": 1},
            ]
        )
    )
    caplog.set_level(logging.INFO)

    process_pdf(
        pdf,
        data_dir=tmp_path / "data",
        client=client,
        translator=FakeTranslator(),
        translation_concurrency=1,
    )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    for stage in (
        "校验 PDF",
        "调用 MinerU",
        "解压解析结果",
        "清洗数据",
        "检测跨页段落",
        "合并跨页段落",
        "翻译 text 对象",
        "渲染 Markdown",
    ):
        assert f"开始{stage}" in messages
        assert f"{stage}完成" in messages
    assert "清洗数据完成：输入 3 项，过滤 1 项，保留 2 项" in messages
    assert "被分页分裂，将合并为一个段落" in messages
    assert "耗时 " in messages
