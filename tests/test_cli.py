from pathlib import Path

from mineru_cleaner import __main__ as cli
from mineru_cleaner.client import DEFAULT_SVR_URL
from mineru_cleaner.cleaner import ContentStats
from mineru_cleaner.errors import WorkflowError
from mineru_cleaner.workflow import WorkflowResult


def test_main_prints_counts_and_output_path(tmp_path, monkeypatch, capsys):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    output = tmp_path / "data/paper/hybrid_auto/cleaned_content_list.json"
    markdown = tmp_path / "data/paper/hybrid_auto/rendered.md"
    received = {}

    def fake_process(path, *, svr_url):
        received["path"] = path
        received["svr_url"] = svr_url
        return WorkflowResult(
            source_path=Path("paper_content_list.json"),
            output_path=output,
            markdown_path=markdown,
            before_count=10,
            filtered_count=3,
            after_count=7,
            content_stats=ContentStats(
                type_counts={"text": 5, "image": 2},
                text_level_count=2,
                text_level_counts={2: 1, 1: 1},
                page_idx_counts={1: 3, 0: 4},
            ),
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
            markdown_path=Path("rendered.md"),
            before_count=1,
            filtered_count=0,
            after_count=1,
            content_stats=ContentStats(
                type_counts={},
                text_level_count=0,
                text_level_counts={},
                page_idx_counts={},
            ),
        )

    monkeypatch.setattr(cli, "process_pdf", fake_process)

    exit_code = cli.main(
        [str(pdf), "--svr-url", "http://mineru.example:7200"]
    )

    assert exit_code == 0
    assert received["svr_url"] == "http://mineru.example:7200"
    assert capsys.readouterr().out.count("  （无）") == 3


def test_main_reports_expected_error(monkeypatch, capsys):
    def fail(path, *, svr_url):
        raise WorkflowError("PDF 文件不存在")

    monkeypatch.setattr(cli, "process_pdf", fail)

    exit_code = cli.main(["missing.pdf"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "错误：PDF 文件不存在" in captured.err
