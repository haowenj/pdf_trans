def test_dashboard_renders_confirmed_layout(
    web_client, repository
) -> None:
    repository.create_task("a", "paper.pdf", "tasks/a/upload/source.pdf")

    response = web_client.get("/")

    assert response.status_code == 200
    assert 'id="upload-dropzone"' in response.text
    assert 'id="task-list"' in response.text
    assert 'id="console-drawer"' in response.text
    assert "paper.pdf" in response.text
    assert "/static/dashboard.js" in response.text


def test_dashboard_assets_define_responsive_drawer_and_status_styles(
    web_client,
) -> None:
    css = web_client.get("/static/app.css").text
    script = web_client.get("/static/dashboard.js").text

    assert "@media (max-width: 720px)" in css
    assert ".console-drawer" in css
    assert "new EventSource('/tasks/events')" in script
    assert "navigator.clipboard.writeText" in script
    assert "scrollHeight" in script
