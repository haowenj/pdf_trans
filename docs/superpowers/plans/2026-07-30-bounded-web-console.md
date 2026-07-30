# Bounded Web Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Web Console responsive by retaining only the latest 200 logs in browser memory and DOM while preserving a downloadable complete log.

**Architecture:** Add a repository query and HTTP endpoint dedicated to the fixed 200-row recent window, plus a streaming full-log download endpoint. Replace per-line frontend rendering with a bounded pending queue and one `requestAnimationFrame` batch, and make every Console lifecycle transition cancel and clear its owned resources.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Jinja2, browser-native JavaScript, pytest.

## Global Constraints

- The browser log window, pending render queue, `logMessages`, and rendered log DOM must each contain no more than 200 entries.
- Complete logs remain persisted in the database and are available through a streaming UTF-8 text download.
- The existing `GET /tasks/{task_id}/logs?after_id=<id>` and SSE reconnect semantics remain compatible.
- Keep log-level colors, connection status, autoscroll, and copy-visible-logs behavior.
- Do not add a frontend framework, virtualization library, runtime dependency, or Node.js build step.
- Use test-driven development: run each new test red before changing production code.

---

## File Structure

- `src/pdf_trans/web/repository.py`: owns the fixed-size, correctly ordered recent-log database query.
- `src/pdf_trans/web/routes/logs.py`: exposes recent logs and streams the complete log download.
- `src/pdf_trans/web/static/dashboard.js`: owns the bounded browser queue, batched rendering, SSE lifecycle, cancellation, and cleanup.
- `src/pdf_trans/web/templates/dashboard.html`: exposes the complete-log download control.
- `tests/web/test_repository.py`: verifies the repository window boundary and order.
- `tests/web/test_task_routes.py`: verifies recent-log and complete-download HTTP behavior.
- `tests/web/test_pages.py`: verifies the shipped page assets contain the bounded lifecycle and download integration.

### Task 1: Fixed Recent-Log Repository Window

**Files:**
- Modify: `tests/web/test_repository.py`
- Modify: `src/pdf_trans/web/repository.py`

**Interfaces:**
- Consumes: existing `TaskRepository.get_task(task_id)` and `TaskLog.id`.
- Produces: `RECENT_LOG_LIMIT: int = 200` and `TaskRepository.list_recent_logs(task_id: str) -> list[LogView]`.

- [ ] **Step 1: Write the failing repository test**

Add this test after `test_repository_persists_ordered_logs_and_success_artifacts`:

```python
def test_repository_lists_only_latest_200_logs_in_ascending_order(
    repository,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    created = [
        repository.append_log("a", "INFO", f"log-{index}")
        for index in range(205)
    ]

    recent = repository.list_recent_logs("a")

    assert len(recent) == 200
    assert [log.id for log in recent] == [
        log.id for log in created[-200:]
    ]
    assert recent[0].message == "log-5"
    assert recent[-1].message == "log-204"
```

- [ ] **Step 2: Run the repository test and verify RED**

Run:

```bash
pytest tests/web/test_repository.py::test_repository_lists_only_latest_200_logs_in_ascending_order -v
```

Expected: FAIL with `AttributeError: 'TaskRepository' object has no attribute 'list_recent_logs'`.

- [ ] **Step 3: Implement the fixed recent window**

Add the constant beside the repository state constants:

```python
RECENT_LOG_LIMIT = 200
```

Add this method after `list_logs`:

```python
def list_recent_logs(self, task_id: str) -> list[LogView]:
    self.get_task(task_id)
    statement = (
        select(TaskLog)
        .where(TaskLog.task_id == task_id)
        .order_by(TaskLog.id.desc())
        .limit(RECENT_LOG_LIMIT)
    )
    with self._sessions() as session:
        logs = [_log_view(log) for log in session.scalars(statement)]
    logs.reverse()
    return logs
```

- [ ] **Step 4: Run repository tests and verify GREEN**

Run:

```bash
pytest tests/web/test_repository.py -v
```

Expected: all tests in `tests/web/test_repository.py` PASS.

- [ ] **Step 5: Commit the repository window**

```bash
git add src/pdf_trans/web/repository.py tests/web/test_repository.py
git commit -m "feat: bound recent web task logs"
```

### Task 2: Recent-Log and Complete-Download Routes

**Files:**
- Modify: `tests/web/test_task_routes.py`
- Modify: `src/pdf_trans/web/routes/logs.py`

**Interfaces:**
- Consumes: `TaskRepository.list_recent_logs(task_id)` from Task 1 and existing `TaskRepository.list_logs(task_id, after_id=0, limit=500)`.
- Produces: `GET /tasks/{task_id}/logs/recent`, `GET /tasks/{task_id}/logs/download`, and `iter_log_lines(repository, task_id) -> Iterator[str]`.

- [ ] **Step 1: Write failing route tests**

Extend `test_missing_task_routes_return_404` with:

```python
assert web_client.get("/tasks/missing/logs/recent").status_code == 404
assert web_client.get("/tasks/missing/logs/download").status_code == 404
```

Add these tests after `test_log_history_uses_after_id`:

```python
def test_recent_log_route_returns_latest_200_in_order(
    web_client,
    repository,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    for index in range(205):
        repository.append_log("a", "INFO", f"log-{index}")

    response = web_client.get("/tasks/a/logs/recent")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 200
    assert payload[0]["message"] == "log-5"
    assert payload[-1]["message"] == "log-204"
    assert [item["id"] for item in payload] == sorted(
        item["id"] for item in payload
    )


def test_complete_log_download_streams_every_log(
    web_client,
    repository,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    for index in range(505):
        level = "WARNING" if index == 504 else "INFO"
        repository.append_log("a", level, f"log-{index}")

    response = web_client.get("/tasks/a/logs/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment;" in response.headers["content-disposition"]
    assert 'filename="task-a.log"' in response.headers["content-disposition"]
    lines = response.text.splitlines()
    assert len(lines) == 505
    assert lines[0] == "[INFO] log-0"
    assert lines[-1] == "[WARNING] log-504"
```

- [ ] **Step 2: Run the route tests and verify RED**

Run:

```bash
pytest tests/web/test_task_routes.py::test_recent_log_route_returns_latest_200_in_order tests/web/test_task_routes.py::test_complete_log_download_streams_every_log -v
```

Expected: both tests FAIL with HTTP `404`.

- [ ] **Step 3: Add route imports and the streaming formatter**

Change the imports in `src/pdf_trans/web/routes/logs.py` to include `Iterator`,
`StreamingResponse`, and `TaskRepository`:

```python
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from pdf_trans.web.repository import TaskNotFound, TaskRepository
```

Add the page size and generator below `router`:

```python
DOWNLOAD_PAGE_SIZE = 500


def iter_log_lines(
    repository: TaskRepository,
    task_id: str,
) -> Iterator[str]:
    cursor = 0
    while True:
        logs = repository.list_logs(
            task_id,
            after_id=cursor,
            limit=DOWNLOAD_PAGE_SIZE,
        )
        if not logs:
            return
        for log in logs:
            cursor = log.id
            yield f"[{log.level}] {log.message}\n"
```

- [ ] **Step 4: Add the recent and download endpoints**

Add these routes after `task_logs` and before the SSE route:

```python
@router.get("/tasks/{task_id}/logs/recent")
def recent_task_logs(
    request: Request,
    task_id: str,
) -> list[dict[str, object]]:
    try:
        logs = request.app.state.repository.list_recent_logs(task_id)
    except TaskNotFound as exc:
        raise HTTPException(404, "任务不存在") from exc
    return [log_to_dict(log) for log in logs]


@router.get("/tasks/{task_id}/logs/download")
def download_task_logs(
    request: Request,
    task_id: str,
) -> StreamingResponse:
    repository = request.app.state.repository
    try:
        repository.get_task(task_id)
    except TaskNotFound as exc:
        raise HTTPException(404, "任务不存在") from exc
    return StreamingResponse(
        iter_log_lines(repository, task_id),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="task-{task_id}.log"'
            )
        },
    )
```

- [ ] **Step 5: Run route and stream tests and verify GREEN**

Run:

```bash
pytest tests/web/test_task_routes.py tests/web/test_streams.py -v
```

Expected: all selected tests PASS, including the unchanged `after_id` and SSE reconnect tests.

- [ ] **Step 6: Commit the log routes**

```bash
git add src/pdf_trans/web/routes/logs.py tests/web/test_task_routes.py
git commit -m "feat: expose recent and downloadable task logs"
```

### Task 3: Bounded Batched Browser Console

**Files:**
- Modify: `tests/web/test_pages.py`
- Modify: `src/pdf_trans/web/templates/dashboard.html`
- Modify: `src/pdf_trans/web/static/dashboard.js`

**Interfaces:**
- Consumes: `GET /tasks/{task_id}/logs/recent`, `GET /tasks/{task_id}/logs/download`, and the existing log SSE endpoint.
- Produces: a fixed `MAX_CONSOLE_LOGS = 200` browser window with batched DOM rendering and complete lifecycle cleanup.

- [ ] **Step 1: Write the failing page-asset assertions**

In `test_dashboard_renders_confirmed_layout`, add:

```python
assert 'id="console-download"' in response.text
```

In `test_dashboard_assets_define_responsive_drawer_and_status_styles`, add:

```python
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
```

- [ ] **Step 2: Run the page tests and verify RED**

Run:

```bash
pytest tests/web/test_pages.py::test_dashboard_renders_confirmed_layout tests/web/test_pages.py::test_dashboard_assets_define_responsive_drawer_and_status_styles -v
```

Expected: both tests FAIL because the download link and bounded rendering lifecycle are absent.

- [ ] **Step 3: Add the complete-log download control**

In the Console footer, place this link before the existing copy button:

```html
<a id="console-download" class="button" href="#" download>
  下载完整日志
</a>
```

- [ ] **Step 4: Replace per-line state with bounded lifecycle state**

At the top of `dashboard.js`, add the download element and replace the current log state with:

```javascript
const consoleDownload = document.querySelector('#console-download');
const MAX_CONSOLE_LOGS = 200;

let logSource = null;
let historyController = null;
let renderFrame = null;
let consoleGeneration = 0;
let activeTaskId = null;
let activeFilename = '';
let pendingLogs = [];
let logMessages = [];
let lastLogId = 0;
```

Replace `appendLog` with these functions:

```javascript
function flushLogs() {
  renderFrame = null;
  if (pendingLogs.length === 0) return;

  const logs = pendingLogs;
  pendingLogs = [];
  const overflow = Math.max(
    0,
    logMessages.length + logs.length - MAX_CONSOLE_LOGS
  );
  if (overflow > 0) {
    logMessages.splice(0, overflow);
    for (let index = 0; index < overflow; index += 1) {
      if (consoleOutput.firstChild) consoleOutput.firstChild.remove();
    }
  }

  const fragment = document.createDocumentFragment();
  for (const log of logs) {
    const message = `[${log.level}] ${log.message}`;
    logMessages.push(message);
    const line = document.createElement('span');
    line.className = `log-${log.level.toLowerCase()}`;
    line.textContent = `${message}\n`;
    fragment.append(line);
  }
  consoleOutput.append(fragment);
  if (autoscroll.checked) {
    consoleOutput.scrollTop = consoleOutput.scrollHeight;
  }
}


function enqueueLog(log) {
  if (log.id <= lastLogId) return;
  lastLogId = log.id;
  pendingLogs.push(log);
  if (pendingLogs.length > MAX_CONSOLE_LOGS) {
    pendingLogs.splice(
      0,
      pendingLogs.length - MAX_CONSOLE_LOGS
    );
  }
  if (renderFrame === null) {
    renderFrame = requestAnimationFrame(flushLogs);
  }
}
```

- [ ] **Step 5: Replace history loading and SSE connection with generation-safe versions**

Replace `loadHistory`, `connectLogs`, `openConsole`, and `closeConsole` with:

```javascript
async function loadHistory(taskId, signal) {
  const response = await fetch(`/tasks/${taskId}/logs/recent`, { signal });
  if (!response.ok) throw new Error('无法读取历史日志');
  return response.json();
}


function connectLogs(taskId, generation) {
  const source = new EventSource(
    `/tasks/${taskId}/logs/events?after_id=${lastLogId}`
  );
  logSource = source;
  source.addEventListener('open', () => {
    if (generation === consoleGeneration) {
      connection.textContent = '● 实时连接';
    }
  });
  source.addEventListener('log', (event) => {
    if (generation === consoleGeneration) {
      enqueueLog(JSON.parse(event.data));
    }
  });
  source.addEventListener('error', () => {
    if (generation === consoleGeneration) {
      connection.textContent = '正在重连';
    }
  });
}


function resetConsoleResources() {
  consoleGeneration += 1;
  if (historyController) historyController.abort();
  historyController = null;
  if (logSource) logSource.close();
  logSource = null;
  if (renderFrame !== null) cancelAnimationFrame(renderFrame);
  renderFrame = null;
  pendingLogs = [];
  logMessages = [];
  lastLogId = 0;
  consoleOutput.replaceChildren();
}


async function openConsole(taskId, filename) {
  resetConsoleResources();
  const generation = consoleGeneration;
  const controller = new AbortController();
  historyController = controller;
  activeTaskId = taskId;
  activeFilename = filename;
  consoleTitle.textContent = filename;
  consoleDownload.href =
    `/tasks/${encodeURIComponent(taskId)}/logs/download`;
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
  connection.textContent = '正在加载';
  try {
    const logs = await loadHistory(taskId, controller.signal);
    if (generation !== consoleGeneration) return;
    logs.forEach(enqueueLog);
    connectLogs(taskId, generation);
  } catch (error) {
    if (
      generation === consoleGeneration &&
      error.name !== 'AbortError'
    ) {
      connection.textContent = error.message;
    }
  } finally {
    if (generation === consoleGeneration) {
      historyController = null;
    }
  }
}


function closeConsole() {
  resetConsoleResources();
  activeTaskId = null;
  activeFilename = '';
  consoleTitle.textContent = '';
  consoleDownload.removeAttribute('href');
  connection.textContent = '未连接';
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
}
```

- [ ] **Step 6: Run page tests and verify GREEN**

Run:

```bash
pytest tests/web/test_pages.py -v
```

Expected: all tests in `tests/web/test_pages.py` PASS.

- [ ] **Step 7: Verify JavaScript syntax when Node.js is available**

Run:

```bash
node --check src/pdf_trans/web/static/dashboard.js
```

Expected: exit code `0` with no output. If Node.js is unavailable, record that fact and perform the browser verification in Task 4.

- [ ] **Step 8: Commit the bounded browser Console**

```bash
git add src/pdf_trans/web/static/dashboard.js src/pdf_trans/web/templates/dashboard.html tests/web/test_pages.py
git commit -m "fix: bound web console rendering"
```

### Task 4: Regression and Browser Verification

**Files:**
- Modify only if a verification failure reveals a scoped defect in files changed by Tasks 1–3.

**Interfaces:**
- Consumes: the complete implementation from Tasks 1–3.
- Produces: fresh verification evidence for the repository, HTTP routes, page assets, SSE compatibility, and browser lifecycle.

- [ ] **Step 1: Run the focused Web test suite**

Run:

```bash
pytest tests/web -v
```

Expected: all Web tests PASS.

- [ ] **Step 2: Run the complete project test suite**

Run:

```bash
pytest -q
```

Expected: all project tests PASS with no failures or errors.

- [ ] **Step 3: Check formatting and whitespace**

Run:

```bash
git diff --check HEAD~3..HEAD
```

Expected: exit code `0` with no output.

- [ ] **Step 4: Verify the Console in a browser**

Start the application using its configured development command, open a task with more than 200 database
logs, and verify all of the following in browser developer tools:

```text
document.querySelector('#console-output').childElementCount === 200
```

Then append a new task log and verify:

```text
document.querySelector('#console-output').childElementCount === 200
```

Close the Console and verify:

```text
document.querySelector('#console-output').childElementCount === 0
```

Also verify that rapid task switching shows only the final selected task, the connection status is not
changed by an older request, automatic scrolling follows one batched update, and “下载完整日志”
downloads more than 200 lines for the test task.

- [ ] **Step 5: Inspect final scope**

Run:

```bash
git status --short
git log -4 --oneline
git diff 066f271..HEAD --stat
```

Expected: only the files named in this plan changed, the three implementation commits are present, and
the worktree is clean.
