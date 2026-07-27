from pdf_trans.web.markdown import render_safe_markdown


def test_markdown_keeps_tables_spans_and_rewrites_relative_images() -> None:
    source = """
# 标题

<table><tr><td rowspan="2" colspan="3">A $x$</td></tr></table>

![](images/a.png)
"""
    html = render_safe_markdown(
        source, asset_base_url="/tasks/a/assets"
    )

    assert "<h1>标题</h1>" in html
    assert 'rowspan="2"' in html
    assert 'colspan="3"' in html
    assert "A $x$" in html
    assert 'src="/tasks/a/assets/images/a.png"' in html


def test_markdown_removes_dangerous_html_attributes_and_urls() -> None:
    html = render_safe_markdown(
        """
<script>alert(1)</script>
<img src="javascript:alert(1)" onerror="alert(2)" style="position:fixed">
<a href="javascript:alert(3)">bad</a>
<iframe src="https://evil.example"></iframe>
""",
        asset_base_url="/tasks/a/assets",
    )

    assert "script" not in html
    assert "alert" not in html
    assert "onerror" not in html
    assert "style=" not in html
    assert "iframe" not in html


def test_markdown_rejects_parent_relative_asset_urls() -> None:
    html = render_safe_markdown(
        "![](../../source.pdf)",
        asset_base_url="/tasks/a/assets",
    )
    assert "source.pdf" not in html
