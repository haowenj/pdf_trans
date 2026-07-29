# Web Task Hard Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a confirmed hard-delete action for completed Web tasks that removes the database task, its logs, the original PDF, and every generated artifact.

**Architecture:** A storage helper deletes one validated `data/tasks/<task_id>` directory. `TaskRepository.delete_task()` locks and validates the task, explicitly deletes logs and the task inside one transaction, and runs storage cleanup before commit so a cleanup failure rolls the database work back. A `DELETE /tasks/{task_id}` route exposes the operation, while the dashboard renders and handles the destructive action for terminal tasks only.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2, SQLite/MySQL-compatible SQL, Jinja2, browser JavaScript, CSS, pytest 8.

## Global Constraints

- Only Web tasks stored in the database and under `data/tasks/<task_id>` are in scope.
- Never enumerate, modify, or delete CLI runs under `data/runs/cli-*`.
- Only `succeeded`, `failed`, and `interrupted` tasks are deletable.
- `queued` and `running` tasks must remain undeletable even if a client calls the API directly.
- Return `204 No Content` for success, `404 Not Found` for a missing task, and `409 Conflict` for a non-terminal task.
- A storage cleanup failure must preserve the task row and all task logs.
- Missing Web task directories are treated as already cleaned.
- Do not add database columns, migrations, soft deletion, recovery, bulk deletion, or date-based CLI cleanup.
- The browser must show the filename in a destructive confirmation before sending the request.
- Use `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pytest` for tests in the current workspace.

---

## File Map

- `src/pdf_trans/web/storage.py`: validate and recursively remove exactly one Web task directory.
- `src/pdf_trans/web/repository.py`: enforce terminal-state deletion and coordinate database rollback with filesystem cleanup.
- `src/pdf_trans/web/routes/tasks.py`: expose `DELETE /tasks/{task_id}` and map domain/storage errors to HTTP responses.
- `src/pdf_trans/web/templates/dashboard.html`: render the delete action on terminal tasks during the initial page response.
- `src/pdf_trans/web/static/dashboard.js`: render the action after SSE updates and implement confirmation, request state, success cleanup, and error recovery.
- `src/pdf_trans/web/static/app.css`: add a destructive button treatment and disabled state.
- `tests/web/test_storage.py`: prove path containment, exact cleanup, missing-directory behavior, CLI isolation, and I/O error mapping.
- `tests/web/test_repository.py`: prove state enforcement, explicit log deletion, and transaction rollback.
- `tests/web/test_task_routes.py`: prove API status codes and end-to-end hard deletion.
- `tests/web/test_pages.py`: prove server-rendered and dynamic UI rules.

---

### Task 1: Safe Web Task Directory Cleanup

**Files:**
- Modify: `src/pdf_trans/web/storage.py:70-89`
- Test: `tests/web/test_storage.py`

**Interfaces:**
- Consumes: `data_dir: pathlib.Path` and `task_id: str`.
- Produces: `delete_task_directory(data_dir: Path, task_id: str) -> None`.
- Raises: `StorageError("任务目录路径越界")` for an unsafe/non-directory target and `StorageError("无法删除任务文件")` for recursive deletion failures.

- [ ] **Step 1: Write failing storage tests**

Append these tests to `tests/web/test_storage.py`. Add `import pdf_trans.web.storage as storage` beside the existing imports so the missing API fails at call time rather than during test collection.

```python
import pdf_trans.web.storage as storage


def test_delete_task_directory_removes_only_requested_web_task(
    tmp_path,
) -> None:
    task_root = tmp_path / "tasks/task-id"
    artifact = task_root / "attempts/1/paper/rendered.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("translated", encoding="utf-8")
    cli_root = tmp_path / "runs/cli-keep"
    cli_root.mkdir(parents=True)
    cli_manifest = cli_root / "task.json"
    cli_manifest.write_text("{}", encoding="utf-8")

    storage.delete_task_directory(tmp_path, "task-id")

    assert not task_root.exists()
    assert cli_manifest.read_text(encoding="utf-8") == "{}"


def test_delete_task_directory_accepts_missing_task_directory(tmp_path) -> None:
    storage.delete_task_directory(tmp_path, "missing")

    assert not (tmp_path / "tasks/missing").exists()


@pytest.mark.parametrize(
    "task_id",
    ["", ".", "..", "../escape", "nested/task", "/absolute"],
)
def test_delete_task_directory_rejects_unsafe_task_ids(
    tmp_path,
    task_id,
) -> None:
    with pytest.raises(StorageError, match="任务目录路径越界"):
        storage.delete_task_directory(tmp_path, task_id)


def test_delete_task_directory_maps_recursive_delete_errors(
    tmp_path,
    monkeypatch,
) -> None:
    task_root = tmp_path / "tasks/task-id"
    task_root.mkdir(parents=True)

    def fail_delete(path):
        raise OSError("locked")

    monkeypatch.setattr(storage.shutil, "rmtree", fail_delete)

    with pytest.raises(StorageError, match="无法删除任务文件"):
        storage.delete_task_directory(tmp_path, "task-id")

    assert task_root.exists()
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pytest \
  tests/web/test_storage.py -k delete_task_directory -v
```

Expected: failures with `AttributeError: module 'pdf_trans.web.storage' has no attribute 'delete_task_directory'`.

- [ ] **Step 3: Implement the minimal safe deletion helper**

Add this function after `resolve_stored_path()` in `src/pdf_trans/web/storage.py`:

```python
def delete_task_directory(data_dir: Path, task_id: str) -> None:
    identifier = Path(task_id)
    if (
        identifier.is_absolute()
        or len(identifier.parts) != 1
        or identifier.parts[0] in {"", ".", ".."}
    ):
        raise StorageError("任务目录路径越界")

    tasks_root = (data_dir / "tasks").resolve()
    unresolved = tasks_root / identifier
    if unresolved.is_symlink():
        raise StorageError("任务目录路径越界")
    target = unresolved.resolve()
    if target.parent != tasks_root:
        raise StorageError("任务目录路径越界")
    if not target.exists():
        return
    if not target.is_dir():
        raise StorageError("任务目录路径越界")

    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise StorageError("无法删除任务文件") from exc
```

Do not inspect `data_dir / "runs"` and do not use a glob.

- [ ] **Step 4: Run storage tests and verify GREEN**

Run:

```bash
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pytest \
  tests/web/test_storage.py -v
```

Expected: all tests in `tests/web/test_storage.py` pass.

- [ ] **Step 5: Commit the storage unit**

```bash
git add src/pdf_trans/web/storage.py tests/web/test_storage.py
git commit -m "feat: delete web task directories safely"
```

---

### Task 2: Transactional Repository Deletion

**Files:**
- Modify: `src/pdf_trans/web/repository.py:3-11`
- Modify: `src/pdf_trans/web/repository.py:169-205`
- Test: `tests/web/test_repository.py`

**Interfaces:**
- Consumes: `task_id: str` and `cleanup: Callable[[], None]`.
- Produces: `TaskRepository.delete_task(task_id: str, *, cleanup: Callable[[], None]) -> None`.
- Raises: existing `TaskNotFound` and `InvalidTaskState`; propagates cleanup exceptions after rolling the database transaction back.

- [ ] **Step 1: Write failing repository deletion tests**

Append the following helper and tests to `tests/web/test_repository.py`:

```python
def _finish_task(repository, task_id: str, status: str) -> None:
    repository.create_task(
        task_id,
        f"{task_id}.pdf",
        f"tasks/{task_id}/upload/source.pdf",
    )
    repository.claim_next_task()
    if status == "succeeded":
        repository.mark_succeeded(
            task_id,
            normalized_path=f"tasks/{task_id}/normalized.json",
            markdown_path=f"tasks/{task_id}/rendered.md",
        )
    elif status == "failed":
        repository.mark_failed(task_id, "failed")
    else:
        assert status == "interrupted"
        assert repository.interrupt_running() == 1


@pytest.mark.parametrize(
    "status",
    ["succeeded", "failed", "interrupted"],
)
def test_repository_deletes_terminal_task_and_logs(
    repository,
    status,
) -> None:
    _finish_task(repository, "a", status)
    repository.append_log("a", "INFO", "before delete")
    cleanup_calls = []

    repository.delete_task(
        "a",
        cleanup=lambda: cleanup_calls.append("called"),
    )

    assert cleanup_calls == ["called"]
    with pytest.raises(TaskNotFound):
        repository.get_task("a")
    repository.create_task("a", "new.pdf", "tasks/a/upload/source.pdf")
    assert repository.list_logs("a") == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_repository_rejects_deleting_active_task(
    repository,
    status,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    if status == "running":
        repository.claim_next_task()
    cleanup_calls = []

    with pytest.raises(InvalidTaskState):
        repository.delete_task(
            "a",
            cleanup=lambda: cleanup_calls.append("called"),
        )

    assert cleanup_calls == []
    assert repository.get_task("a").status == status


def test_repository_delete_reports_missing_task(repository) -> None:
    with pytest.raises(TaskNotFound):
        repository.delete_task("missing", cleanup=lambda: None)


def test_repository_rolls_back_when_delete_cleanup_fails(repository) -> None:
    _finish_task(repository, "a", "failed")
    repository.append_log("a", "INFO", "preserve me")

    def fail_cleanup() -> None:
        raise OSError("locked")

    with pytest.raises(OSError, match="locked"):
        repository.delete_task("a", cleanup=fail_cleanup)

    assert repository.get_task("a").status == "failed"
    assert [log.message for log in repository.list_logs("a")] == [
        "preserve me"
    ]
```

- [ ] **Step 2: Run deletion tests and verify RED**

Run:

```bash
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pytest \
  tests/web/test_repository.py -k delete -v
```

Expected: failures with `AttributeError: 'TaskRepository' object has no attribute 'delete_task'`.

- [ ] **Step 3: Add state constants and imports**

Update the top of `src/pdf_trans/web/repository.py`:

```python
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, select, update
```

Define the state sets together:

```python
RECOVERABLE_STATES = {"failed", "interrupted"}
DELETABLE_STATES = RECOVERABLE_STATES | {"succeeded"}
```

- [ ] **Step 4: Implement transactional deletion**

Add this method after `_finish()` and before `append_log()`:

```python
    def delete_task(
        self,
        task_id: str,
        *,
        cleanup: Callable[[], None],
    ) -> None:
        with self._sessions.begin() as session:
            task = session.scalar(
                select(Task)
                .where(Task.id == task_id)
                .with_for_update()
            )
            if task is None:
                raise TaskNotFound(task_id)
            if task.status not in DELETABLE_STATES:
                raise InvalidTaskState(task.status)

            session.execute(
                delete(TaskLog).where(TaskLog.task_id == task_id)
            )
            session.delete(task)
            session.flush()
            cleanup()
```

The cleanup callback must stay after `flush()` and inside the `with self._sessions.begin()` block. That ordering gives the database transaction a chance to roll back the task and explicit log deletions when cleanup raises.

- [ ] **Step 5: Run repository tests and verify GREEN**

Run:

```bash
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pytest \
  tests/web/test_repository.py -v
```

Expected: all tests in `tests/web/test_repository.py` pass.

- [ ] **Step 6: Commit the repository unit**

```bash
git add src/pdf_trans/web/repository.py tests/web/test_repository.py
git commit -m "feat: delete terminal web tasks transactionally"
```

---

### Task 3: Hard-Delete HTTP Endpoint

**Files:**
- Modify: `src/pdf_trans/web/routes/tasks.py:6-11`
- Modify: `src/pdf_trans/web/routes/tasks.py:49-61`
- Test: `tests/web/test_task_routes.py`

**Interfaces:**
- Consumes: `TaskRepository.delete_task()` from Task 2 and `delete_task_directory()` from Task 1.
- Produces: `DELETE /tasks/{task_id}`.
- HTTP results: `204`, `404`, `409`, or `500` with a Chinese `detail` message.

- [ ] **Step 1: Add failing route tests**

Update imports in `tests/web/test_task_routes.py`:

```python
import pytest

from pdf_trans.web.repository import TaskNotFound
from pdf_trans.web.storage import StorageError, UploadValidationError
```

Append these tests:

```python
def _create_failed_task(repository, task_id: str) -> None:
    repository.create_task(
        task_id,
        f"{task_id}.pdf",
        f"tasks/{task_id}/upload/source.pdf",
    )
    repository.claim_next_task()
    repository.mark_failed(task_id, "failed")


def test_delete_task_removes_database_logs_and_web_files(
    web_client,
    repository,
) -> None:
    task_id = "12345678-1234-4abc-8def-1234567890ab"
    _create_failed_task(repository, task_id)
    repository.append_log(task_id, "INFO", "delete me")
    data_dir = web_client.app.state.settings.data_dir
    task_root = data_dir / "tasks" / task_id
    source = task_root / "upload/source.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.7")
    cli_manifest = data_dir / "runs/cli-keep/task.json"
    cli_manifest.parent.mkdir(parents=True)
    cli_manifest.write_text("{}", encoding="utf-8")

    response = web_client.delete(f"/tasks/{task_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert not task_root.exists()
    assert cli_manifest.read_text(encoding="utf-8") == "{}"
    with pytest.raises(TaskNotFound):
        repository.get_task(task_id)
    assert web_client.get(f"/tasks/{task_id}/logs").status_code == 404
    assert web_client.post(f"/tasks/{task_id}/resume").status_code == 404
    assert web_client.get(f"/tasks/{task_id}/view").status_code == 404
    assert web_client.get(f"/tasks/{task_id}/markdown").status_code == 404
    repository.create_task(
        task_id,
        "replacement.pdf",
        f"tasks/{task_id}/upload/source.pdf",
    )
    assert repository.list_logs(task_id) == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_delete_task_rejects_active_states(
    web_client,
    repository,
    status,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    if status == "running":
        repository.claim_next_task()
    task_root = web_client.app.state.settings.data_dir / "tasks/a"
    task_root.mkdir(parents=True)

    response = web_client.delete("/tasks/a")

    assert response.status_code == 409
    assert response.json()["detail"] == "当前任务状态不能删除"
    assert repository.get_task("a").status == status
    assert task_root.exists()


def test_delete_task_reports_missing_task(web_client) -> None:
    response = web_client.delete("/tasks/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "任务不存在"


def test_delete_task_preserves_database_when_storage_fails(
    web_client,
    repository,
    monkeypatch,
) -> None:
    _create_failed_task(repository, "a")
    repository.append_log("a", "INFO", "preserve me")

    def fail_delete(data_dir, task_id):
        raise StorageError("无法删除任务文件")

    monkeypatch.setattr(
        "pdf_trans.web.routes.tasks.delete_task_directory",
        fail_delete,
    )

    response = web_client.delete("/tasks/a")

    assert response.status_code == 500
    assert response.json()["detail"] == "无法删除任务文件"
    assert repository.get_task("a").status == "failed"
    assert [log.message for log in repository.list_logs("a")] == [
        "preserve me"
    ]
```

- [ ] **Step 2: Run route deletion tests and verify RED**

Run:

```bash
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pytest \
  tests/web/test_task_routes.py -k delete -v
```

Expected: endpoint tests fail with `405 Method Not Allowed`.

- [ ] **Step 3: Add the route imports**

Change the FastAPI response imports in `src/pdf_trans/web/routes/tasks.py` to:

```python
from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse
```

Change the storage imports to:

```python
from pdf_trans.web.storage import (
    StorageError,
    UploadValidationError,
    delete_task_directory,
    save_pdf_upload,
)
```

- [ ] **Step 4: Implement the DELETE endpoint**

Add this route between `resume_task()` and `task_events()`:

```python
@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(request: Request, task_id: str) -> Response:
    settings = request.app.state.settings
    try:
        request.app.state.repository.delete_task(
            task_id,
            cleanup=lambda: delete_task_directory(
                settings.data_dir,
                task_id,
            ),
        )
    except TaskNotFound as exc:
        raise HTTPException(404, "任务不存在") from exc
    except InvalidTaskState as exc:
        raise HTTPException(409, "当前任务状态不能删除") from exc
    except StorageError as exc:
        raise HTTPException(500, str(exc)) from exc
    return Response(status_code=204)
```

- [ ] **Step 5: Run all task-route tests and verify GREEN**

Run:

```bash
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pytest \
  tests/web/test_task_routes.py -v
```

Expected: all tests in `tests/web/test_task_routes.py` pass.

- [ ] **Step 6: Commit the API unit**

```bash
git add src/pdf_trans/web/routes/tasks.py tests/web/test_task_routes.py
git commit -m "feat: expose web task deletion API"
```

---

### Task 4: Confirmed Dashboard Delete Action

**Files:**
- Modify: `src/pdf_trans/web/templates/dashboard.html:44-53`
- Modify: `src/pdf_trans/web/static/dashboard.js:1-73`
- Modify: `src/pdf_trans/web/static/dashboard.js:133-165`
- Modify: `src/pdf_trans/web/static/app.css:17-43`
- Test: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `DELETE /tasks/{task_id}` from Task 3 and existing `activeTaskId`, `closeConsole()`, `uploadError`, and SSE task snapshots.
- Produces: a terminal-state-only `data-action="delete"` button and `deleteTask(row: HTMLElement, button: HTMLButtonElement)`.
- User copy: confirmation names the file and states that the original PDF, logs, and all parsed artifacts are permanently removed.

- [ ] **Step 1: Write failing page and asset tests**

Append this server-rendered test to `tests/web/test_pages.py`:

```python
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
```

Extend `test_dashboard_assets_define_responsive_drawer_and_status_styles()` with:

```python
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
```

- [ ] **Step 2: Run page tests and verify RED**

Run:

```bash
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pytest \
  tests/web/test_pages.py -v
```

Expected: the new assertions fail because the template, script, and stylesheet do not contain delete behavior.

- [ ] **Step 3: Render the terminal-state button in Jinja**

Add this block after the existing “查看” link block in `src/pdf_trans/web/templates/dashboard.html`:

```html
            {% if task.status in ("succeeded", "failed", "interrupted") %}
            <button class="danger" type="button"
                    data-action="delete">删除</button>
            {% endif %}
```

- [ ] **Step 4: Render the same action for SSE snapshots**

Add this set after the existing top-level DOM constants in `dashboard.js`:

```javascript
const deletableStatuses = new Set([
  'succeeded',
  'failed',
  'interrupted',
]);
```

Inside `renderTasks()`, after the successful task’s “查看” link block and before `row.append(...)`, add:

```javascript
    if (deletableStatuses.has(task.status)) {
      const deleteButton = actionButton('删除', 'delete');
      deleteButton.classList.add('danger');
      actions.append(deleteButton);
    }
```

- [ ] **Step 5: Implement confirmation, request state, and cleanup**

Add this function after `resumeTask()` in `dashboard.js`:

```javascript
async function deleteTask(row, button) {
  const taskId = row.dataset.taskId;
  const filename = row.dataset.filename || '该文件';
  const confirmed = window.confirm(
    `确定删除“${filename}”吗？\n\n` +
    '这会永久删除原始 PDF、日志和全部解析产物，无法恢复。'
  );
  if (!confirmed) return;

  uploadError.textContent = '';
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = '删除中…';
  try {
    const response = await fetch(`/tasks/${taskId}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      let message = '无法删除任务';
      try {
        const payload = await response.json();
        message = payload.detail || message;
      } catch {
        // Keep the generic message for a non-JSON server error.
      }
      throw new Error(message);
    }
    if (activeTaskId === taskId) {
      closeConsole();
    }
    row.remove();
  } catch (error) {
    uploadError.textContent =
      error instanceof Error ? error.message : '无法删除任务';
    button.disabled = false;
    button.textContent = originalLabel;
  }
}
```

Extend the delegated click handler before the `copy-id` branch:

```javascript
  } else if (action.dataset.action === 'delete') {
    deleteTask(row, action);
```

The resulting branch order must remain `console`, `resume`, `delete`, then `copy-id`.

- [ ] **Step 6: Add destructive and disabled styles**

Add these rules after `.button.primary` in `src/pdf_trans/web/static/app.css`:

```css
button.danger {
  border-color: #fecaca;
  background: #fff7f7;
  color: #b91c1c;
}

button.danger:hover:not(:disabled) {
  border-color: #ef4444;
  background: #fee2e2;
}

button:disabled {
  cursor: wait;
  opacity: 0.65;
}
```

- [ ] **Step 7: Run page tests and verify GREEN**

Run:

```bash
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pytest \
  tests/web/test_pages.py -v
```

Expected: all tests in `tests/web/test_pages.py` pass.

- [ ] **Step 8: Commit the dashboard unit**

```bash
git add \
  src/pdf_trans/web/templates/dashboard.html \
  src/pdf_trans/web/static/dashboard.js \
  src/pdf_trans/web/static/app.css \
  tests/web/test_pages.py
git commit -m "feat: add confirmed dashboard task deletion"
```

---

### Task 5: Full Regression and Scope Verification

**Files:**
- Verify only; no production file changes expected.

**Interfaces:**
- Consumes: all units from Tasks 1–4.
- Produces: fresh evidence that the full Python/Web suite passes and that the committed implementation did not modify CLI task management.

- [ ] **Step 1: Run the complete test suite**

Run:

```bash
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pytest -v
```

Expected: every collected test passes with zero failures and zero errors.

- [ ] **Step 2: Check formatting and repository state**

Run:

```bash
git diff --check
git status --short --branch
```

Expected: `git diff --check` produces no output; `git status` reports a clean feature branch.

- [ ] **Step 3: Audit changed paths against the agreed scope**

Run:

```bash
git diff --name-only main...HEAD
```

Expected implementation paths are limited to:

```text
src/pdf_trans/web/repository.py
src/pdf_trans/web/routes/tasks.py
src/pdf_trans/web/static/app.css
src/pdf_trans/web/static/dashboard.js
src/pdf_trans/web/storage.py
src/pdf_trans/web/templates/dashboard.html
tests/web/test_pages.py
tests/web/test_repository.py
tests/web/test_storage.py
tests/web/test_task_routes.py
```

The diff must not contain `src/pdf_trans/__main__.py`, `src/pdf_trans/task_runs.py`, `src/pdf_trans/task_diagnostics.py`, or CLI tests.

- [ ] **Step 4: Review the final commit sequence**

Run:

```bash
git log --oneline --decorate -5
```

Expected: separate commits exist for storage cleanup, repository deletion, HTTP API, and dashboard interaction, with no uncommitted implementation changes.

