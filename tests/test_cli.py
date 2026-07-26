from pathlib import Path

import pytest

from pdf_trans import __main__ as cli
from pdf_trans.client import DEFAULT_SVR_URL
from pdf_trans.cleaner import ContentStats
from pdf_trans.errors import WorkflowError
from pdf_trans.translation import TranslationStats
from pdf_trans.workflow import WorkflowResult


def test_main_prints_counts_and_output_path(tmp_path, monkeypatch, capsys):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    output = tmp_path / "data/paper/hybrid_auto/cleaned_content_list.json"
    normalized = (
        tmp_path / "data/paper/hybrid_auto/normalized_content_list.json"
    )
    markdown = tmp_path / "data/paper/hybrid_auto/rendered.md"
    candidates = (
        tmp_path
        / "data/paper/hybrid_auto/cross_page_candidates.json"
    )
    translated = (
        tmp_path
        / "data/paper/hybrid_auto/translated_content_list.json"
    )
    received = {}

    def fake_process(path, *, svr_url):
        received["path"] = path
        received["svr_url"] = svr_url
        return WorkflowResult(
            source_path=Path("paper_content_list.json"),
            output_path=output,
            normalized_path=normalized,
            normalized_count=6,
            markdown_path=markdown,
            candidates_path=candidates,
            candidate_count=2,
            before_count=10,
            filtered_count=3,
            after_count=7,
            content_stats=ContentStats(
                type_counts={"text": 5, "image": 2},
                text_level_count=2,
                text_level_counts={2: 1, 1: 1},
                page_idx_counts={1: 3, 0: 4},
            ),
            translated_path=translated,
            translation_stats=TranslationStats(6, 4, 2, 2, 1, 3),
        )

    monkeypatch.setattr(cli, "process_pdf", fake_process)

    exit_code = cli.main([str(pdf)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert received == {"path": pdf, "svr_url": DEFAULT_SVR_URL}
    assert captured.out == (
        "处理前数量：10\n"
        "过滤数量：3\n"
        "处理后数量：7\n"
        f"输出文件：{output}\n"
        "清洗后 type 统计：\n"
        "  image: 2\n"
        "  text: 5\n"
        "带 text_level 的 text 数量：2\n"
        "按 text_level 分组：\n"
        "  1: 1\n"
        "  2: 1\n"
        "每个 page_idx 的元素数量：\n"
        "  0: 4\n"
        "  1: 3\n"
        f"Markdown 文件：{markdown}\n"
        "跨页段落候选数量：2\n"
        f"跨页候选报告：{candidates}\n"
        "规范化后数量：6\n"
        f"规范化文件：{normalized}\n"
        "text 对象总数：6\n"
        "本次模型调用数量：4\n"
        "跳过的已成功数量：2\n"
        "翻译成功数量：2\n"
        "翻译失败数量：1\n"
        "待翻译数量：3\n"
        f"翻译文件：{translated}\n"
    )
    assert captured.err == ""


def test_main_passes_custom_svr_url(tmp_path, monkeypatch, capsys):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    received = {}

    def fake_process(path, *, svr_url):
        received["svr_url"] = svr_url
        return WorkflowResult(
            source_path=Path("paper_content_list.json"),
            output_path=Path("cleaned_content_list.json"),
            normalized_path=Path("normalized_content_list.json"),
            normalized_count=1,
            markdown_path=Path("rendered.md"),
            candidates_path=Path("cross_page_candidates.json"),
            candidate_count=0,
            before_count=1,
            filtered_count=0,
            after_count=1,
            content_stats=ContentStats(
                type_counts={},
                text_level_count=0,
                text_level_counts={},
                page_idx_counts={},
            ),
            translated_path=Path("translated_content_list.json"),
            translation_stats=TranslationStats(0, 0, 0, 0, 0, 0),
        )

    monkeypatch.setattr(cli, "process_pdf", fake_process)

    exit_code = cli.main(
        [str(pdf), "--svr-url", "http://mineru.example:7200"]
    )

    assert exit_code == 0
    assert received["svr_url"] == "http://mineru.example:7200"
    captured = capsys.readouterr()
    assert captured.out.count("  （无）") == 3
    assert "跨页段落候选数量：0" in captured.out
    assert "跨页候选报告：cross_page_candidates.json" in captured.out
    assert "规范化后数量：1" in captured.out
    assert "规范化文件：normalized_content_list.json" in captured.out
    assert "text 对象总数：0" in captured.out
    assert (
        f"翻译文件：{Path('translated_content_list.json').resolve()}"
        in captured.out
    )


def test_main_reports_expected_error(monkeypatch, capsys):
    def fail(path, *, svr_url):
        raise WorkflowError("PDF 文件不存在")

    monkeypatch.setattr(cli, "process_pdf", fail)

    exit_code = cli.main(["missing.pdf"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "错误：PDF 文件不存在" in captured.err


def test_main_translate_only_prints_translation_summary(tmp_path, monkeypatch, capsys):
    normalized = tmp_path / "normalized_content_list.json"
    received = {}

    def fake_process(path, *, translator=None, max_retries=None):
        received.update(path=path, translator=translator, max_retries=max_retries)
        return type(
            "Result",
            (),
            {
                "translated_path": tmp_path / "translated_content_list.json",
                "stats": TranslationStats(2, 3, 1, 2, 0, 0),
            },
        )()

    monkeypatch.setattr(cli, "process_translation_file", fake_process)

    exit_code = cli.main(["--translate-only", str(normalized)])

    assert exit_code == 0
    assert received == {
        "path": normalized,
        "translator": None,
        "max_retries": None,
    }
    assert capsys.readouterr().out == (
        "text 对象总数：2\n"
        "本次模型调用数量：3\n"
        "跳过的已成功数量：1\n"
        "翻译成功数量：2\n"
        "翻译失败数量：0\n"
        "待翻译数量：0\n"
        f"翻译文件：{(tmp_path / 'translated_content_list.json').resolve()}\n"
    )


def test_main_requires_exactly_one_mode():
    with pytest.raises(SystemExit):
        cli.main([])
    with pytest.raises(SystemExit):
        cli.main(["paper.pdf", "--translate-only", "normalized_content_list.json"])
