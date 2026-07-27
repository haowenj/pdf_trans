def complete_task(repository, data_dir) -> None:
    repository.create_task("a", "paper.pdf", "tasks/a/upload/source.pdf")
    repository.claim_next_task()
    paper = data_dir / "tasks/a/attempts/1/paper"
    images = paper / "images"
    images.mkdir(parents=True)
    (images / "a.png").write_bytes(b"png")
    (paper / "normalized_content_list.json").write_text("[]")
    (paper / "rendered.md").write_text(
        "# 标题\n\n<table><tr><td>$x$</td></tr></table>\n\n"
        "![](images/a.png)\n\n<script>alert(1)</script>",
        encoding="utf-8",
    )
    repository.mark_succeeded(
        "a",
        normalized_path=(
            "tasks/a/attempts/1/paper/normalized_content_list.json"
        ),
        markdown_path="tasks/a/attempts/1/paper/rendered.md",
    )


def test_reader_is_sanitized_and_uses_local_katex(
    web_client, repository, web_settings
) -> None:
    complete_task(repository, web_settings.data_dir)
    response = web_client.get("/tasks/a/view")

    assert response.status_code == 200
    assert "<h1>标题</h1>" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "/static/vendor/katex/katex.min.js" in response.text
    assert "/static/vendor/katex/auto-render.min.js" in response.text
    assert 'id="print-button"' in response.text


def test_markdown_download_and_asset_route(
    web_client, repository, web_settings
) -> None:
    complete_task(repository, web_settings.data_dir)

    markdown = web_client.get("/tasks/a/markdown")
    image = web_client.get("/tasks/a/assets/images/a.png")

    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "attachment;" in markdown.headers["content-disposition"]
    assert image.content == b"png"
    assert (
        web_client.get("/tasks/a/assets/../../upload/source.pdf").status_code
        in {404, 400}
    )


def test_unfinished_task_cannot_open_reader(
    web_client, repository
) -> None:
    repository.create_task("a", "paper.pdf", "tasks/a/upload/source.pdf")
    assert web_client.get("/tasks/a/view").status_code == 409
    assert web_client.get("/tasks/a/markdown").status_code == 409
