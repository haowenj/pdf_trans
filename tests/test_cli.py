from pathlib import Path

from mineru_cleaner import __main__ as cli
from mineru_cleaner.client import DEFAULT_SVR_URL
from mineru_cleaner.errors import WorkflowError
from mineru_cleaner.workflow import WorkflowResult


def test_main_prints_counts_and_output_path(tmp_path, monkeypatch, capsys):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    output = tmp_path / "data/paper/hybrid_auto/cleaned_content_list.json"
    received = {}

    def fake_process(path, *, svr_url):
        received["path"] = path
        received["svr_url"] = svr_url
        return WorkflowResult(
            source_path=Path("paper_content_list.json"),
            output_path=output,
            before_count=10,
            filtered_count=3,
            after_count=7,
        )

    monkeypatch.setattr(cli, "process_pdf", fake_process)

    exit_code = cli.main([str(pdf)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert received == {"path": pdf, "svr_url": DEFAULT_SVR_URL}
    assert "处理前数量：10" in captured.out
    assert "过滤数量：3" in captured.out
    assert "处理后数量：7" in captured.out
    assert f"输出文件：{output}" in captured.out
    assert captured.err == ""


def test_main_passes_custom_svr_url(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    received = {}

    def fake_process(path, *, svr_url):
        received["svr_url"] = svr_url
        return WorkflowResult(
            source_path=Path("paper_content_list.json"),
            output_path=Path("cleaned_content_list.json"),
            before_count=1,
            filtered_count=0,
            after_count=1,
        )

    monkeypatch.setattr(cli, "process_pdf", fake_process)

    exit_code = cli.main(
        [str(pdf), "--svr-url", "http://mineru.example:7200"]
    )

    assert exit_code == 0
    assert received["svr_url"] == "http://mineru.example:7200"


def test_main_reports_expected_error(monkeypatch, capsys):
    def fail(path, *, svr_url):
        raise WorkflowError("PDF 文件不存在")

    monkeypatch.setattr(cli, "process_pdf", fail)

    exit_code = cli.main(["missing.pdf"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "错误：PDF 文件不存在" in captured.err
