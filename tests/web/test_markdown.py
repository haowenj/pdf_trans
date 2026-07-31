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


def test_markdown_does_not_parse_emphasis_across_inline_formulas() -> None:
    source = (
        r"The catalyst converts ${ \mathrm{C}_{4} } ^ { = }$ to "
        r"$\scriptstyle \mathrm{C}_{7}^{=} / \mathrm{C}_{8}^{=}$ "
        "olefins."
    )

    rendered = render_safe_markdown(
        source,
        asset_base_url="/tasks/a/assets",
    )

    assert "<em>" not in rendered
    assert (
        r"${ \mathrm{C}_{4} } ^ { = }$ to "
        r"$\scriptstyle \mathrm{C}_{7}^{=} / \mathrm{C}_{8}^{=}$"
        in rendered
    )


def test_markdown_preserves_all_supported_math_delimiters() -> None:
    source = r"$a_b$ and $$c_d$$ and \(e_f\) and \[g_h\]"

    rendered = render_safe_markdown(
        source,
        asset_base_url="/tasks/a/assets",
    )

    assert source in rendered
    assert "<em>" not in rendered


def test_markdown_escapes_html_inside_protected_formula() -> None:
    rendered = render_safe_markdown(
        r"$x <img src=x onerror=alert(1)> y$",
        asset_base_url="/tasks/a/assets",
    )

    assert "<img" not in rendered
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered


def test_markdown_keeps_regular_emphasis_outside_formulas() -> None:
    rendered = render_safe_markdown(
        "before _important_ after",
        asset_base_url="/tasks/a/assets",
    )

    assert "<em>important</em>" in rendered


def test_markdown_leaves_escaped_and_unclosed_dollars_as_text() -> None:
    rendered = render_safe_markdown(
        r"cost \$5 and unfinished $x_y",
        asset_base_url="/tasks/a/assets",
    )

    assert "cost $5 and unfinished $x_y" in rendered
