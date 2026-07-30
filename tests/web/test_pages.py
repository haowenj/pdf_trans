def test_dashboard_renders_confirmed_layout(
    web_client, repository
) -> None:
    task_id = "12345678-1234-4abc-8def-1234567890ab"
    repository.create_task(
        task_id,
        "paper.pdf",
        f"tasks/{task_id}/upload/source.pdf",
    )

    response = web_client.get("/")

    assert response.status_code == 200
    assert 'id="upload-dropzone"' in response.text
    assert 'id="task-list"' in response.text
    assert 'id="console-drawer"' in response.text
    assert 'id="console-download"' in response.text
    assert "paper.pdf" in response.text
    assert "/static/dashboard.js" in response.text
    assert "任务 UUID：12345678" in response.text
    assert f'title="{task_id}"' in response.text
    assert 'data-action="copy-id"' in response.text


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
    assert "task.id.slice(0, 8)" in script
    assert "action.dataset.action === 'copy-id'" in script
    assert "const deletableStatuses = new Set" in script
    assert "actionButton('删除', 'delete')" in script
    assert "window.confirm" in script
    assert "method: 'DELETE'" in script
    assert "button.textContent = '删除中…'" in script
    assert "if (activeTaskId === taskId)" in script
    assert "row.remove()" in script
    assert "action.dataset.action === 'delete'" in script
    assert "button.danger" in css
    assert "button:disabled" in css
    assert "const MAX_CONSOLE_LOGS = 200;" in script
    assert "/logs/recent" in script
    assert "requestAnimationFrame(flushLogs)" in script
    assert "document.createDocumentFragment()" in script
    assert "pendingLogs.splice(" in script
    assert "consoleOutput.firstChild.remove()" in script
    assert "new AbortController()" in script
    assert "historyController.abort()" in script
    assert "cancelAnimationFrame(renderFrame)" in script
    assert "consoleOutput.replaceChildren()" in script
    assert "log.id <= lastLogId" in script
    assert "/logs/download" in script


def test_dashboard_shows_delete_only_for_terminal_tasks(
    web_client,
    repository,
) -> None:
    terminal_id = "12345678-1234-4abc-8def-1234567890ab"
    queued_id = "87654321-4321-4abc-8def-1234567890ab"
    repository.create_task(
        terminal_id,
        "finished.pdf",
        f"tasks/{terminal_id}/upload/source.pdf",
    )
    repository.claim_next_task()
    repository.mark_failed(terminal_id, "failed")
    repository.create_task(
        queued_id,
        "queued.pdf",
        f"tasks/{queued_id}/upload/source.pdf",
    )

    response = web_client.get("/")

    terminal_row = response.text.split(
        f'data-task-id="{terminal_id}"',
        1,
    )[1].split("</li>", 1)[0]
    queued_row = response.text.split(
        f'data-task-id="{queued_id}"',
        1,
    )[1].split("</li>", 1)[0]
    assert 'data-action="delete"' in terminal_row
    assert 'class="danger"' in terminal_row
    assert 'data-action="delete"' not in queued_row
