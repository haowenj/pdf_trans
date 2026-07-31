# Beijing Task Time Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep task timestamps stored as UTC while displaying task creation time as fixed Beijing time in both the initial dashboard and live task updates.

**Architecture:** Add one presentation-only formatter that treats naive database datetimes as UTC, converts all inputs to `Asia/Shanghai`, and returns `YYYY-MM-DD HH:mm:ss`. Reuse it in the server-rendered dashboard and in an additive SSE display field; preserve all existing raw timestamp fields.

**Tech Stack:** Python 3.12+, `datetime`, `zoneinfo`, FastAPI/Jinja2, browser JavaScript, pytest

## Global Constraints

- Database values and migrations remain unchanged.
- Naive datetimes are interpreted as UTC.
- Display timezone is fixed to `Asia/Shanghai`, independent of browser timezone.
- Display format is exactly `%Y-%m-%d %H:%M:%S`.
- Existing SSE timestamp fields remain backward compatible.
- Task ordering and log timestamp display remain unchanged.

---

### Task 1: Shared Beijing Time Formatter and SSE Field

**Files:**
- Create: `src/pdf_trans/web/time_display.py`
- Modify: `src/pdf_trans/web/streams.py`
- Test: `tests/web/test_streams.py`

**Interfaces:**
- Consumes: `datetime.datetime`, including naive UTC values returned by SQLite
- Produces: `format_beijing_time(value: datetime) -> str`
- Produces: additive `created_at_display: str` in `task_to_dict`

- [ ] **Step 1: Write failing SSE serialization tests**

Add imports and tests to `tests/web/test_streams.py`:

```python
from datetime import datetime, timezone
from types import SimpleNamespace

from pdf_trans.web.streams import (
    log_event_stream,
    task_event_stream,
    task_to_dict,
)


@pytest.mark.parametrize(
    "created_at",
    [
        datetime(2026, 7, 31, 0, 54, 28),
        datetime(2026, 7, 31, 0, 54, 28, tzinfo=timezone.utc),
    ],
)
def test_task_dict_displays_naive_and_aware_utc_as_beijing_time(
    created_at,
) -> None:
    task = SimpleNamespace(
        id="a",
        original_filename="a.pdf",
        status="queued",
        attempt_count=0,
        error_message=None,
        created_at=created_at,
        updated_at=created_at,
        started_at=None,
        finished_at=None,
    )

    payload = task_to_dict(task)

    assert payload["created_at_display"] == "2026-07-31 08:54:28"
    assert payload["created_at"] == created_at.isoformat()
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/web/test_streams.py::test_task_dict_displays_naive_and_aware_utc_as_beijing_time -q
```

Expected: two failures with `KeyError: 'created_at_display'`.

- [ ] **Step 3: Implement the shared formatter and additive SSE field**

Create `src/pdf_trans/web/time_display.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")
DISPLAY_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_beijing_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_TIMEZONE).strftime(DISPLAY_FORMAT)
```

Modify `src/pdf_trans/web/streams.py`:

```python
from pdf_trans.web.time_display import format_beijing_time
```

Add this entry beside the existing `created_at` value in `task_to_dict`:

```python
"created_at_display": format_beijing_time(task.created_at),
```

- [ ] **Step 4: Run focused and stream tests**

Run:

```bash
.venv/bin/python -m pytest tests/web/test_streams.py -q
```

Expected: all tests in `tests/web/test_streams.py` pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/pdf_trans/web/time_display.py src/pdf_trans/web/streams.py tests/web/test_streams.py
git commit -m "feat: serialize Beijing task display time"
```

### Task 2: Initial Dashboard and Live Rendering

**Files:**
- Modify: `src/pdf_trans/web/routes/pages.py`
- Modify: `src/pdf_trans/web/templates/dashboard.html`
- Modify: `src/pdf_trans/web/static/dashboard.js`
- Modify: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `format_beijing_time(value: datetime) -> str` from Task 1
- Consumes: SSE `created_at_display: str` from Task 1
- Produces: identical `YYYY-MM-DD HH:mm:ss` task creation time on initial render and live rerender

- [ ] **Step 1: Write failing initial-render and JavaScript tests**

Add imports to `tests/web/test_pages.py`:

```python
from datetime import datetime

from pdf_trans.web.repository import TaskView
```

Add:

```python
def test_dashboard_displays_task_creation_time_as_beijing_time(
    web_client,
    repository,
    monkeypatch,
) -> None:
    created_at = datetime(2026, 7, 31, 0, 54, 28)
    task = TaskView(
        id="a",
        original_filename="a.pdf",
        status="queued",
        attempt_count=0,
        source_pdf_path="tasks/a/upload/source.pdf",
        normalized_path=None,
        markdown_path=None,
        error_message=None,
        created_at=created_at,
        updated_at=created_at,
        started_at=None,
        finished_at=None,
    )
    monkeypatch.setattr(repository, "list_tasks", lambda: [task])

    response = web_client.get("/")

    assert "2026-07-31 08:54:28 · 第 0 次执行" in response.text
    assert "2026-07-31T00:54:28" not in response.text
```

Extend `test_dashboard_assets_define_responsive_drawer_and_status_styles`:

```python
assert "task.created_at_display" in script
assert "new Date(task.created_at).toLocaleString()" not in script
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/web/test_pages.py::test_dashboard_displays_task_creation_time_as_beijing_time tests/web/test_pages.py::test_dashboard_assets_define_responsive_drawer_and_status_styles -q
```

Expected: both tests fail because the template emits raw ISO time and JavaScript uses browser-local formatting.

- [ ] **Step 3: Register the filter and update both rendering paths**

Modify `src/pdf_trans/web/routes/pages.py`:

```python
from pdf_trans.web.time_display import format_beijing_time

templates.env.filters["beijing_time"] = format_beijing_time
```

Replace the task time in `src/pdf_trans/web/templates/dashboard.html`:

```jinja2
<small>{{ task.created_at|beijing_time }} · 第 {{ task.attempt_count }} 次执行</small>
```

Replace the detail assignment in `src/pdf_trans/web/static/dashboard.js`:

```javascript
detail.textContent =
  `${task.created_at_display} · 第 ${task.attempt_count} 次执行`;
```

- [ ] **Step 4: Run focused Web tests**

Run:

```bash
.venv/bin/python -m pytest tests/web/test_pages.py tests/web/test_streams.py -q
```

Expected: all focused tests pass.

- [ ] **Step 5: Run the complete suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass with no new warnings.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/pdf_trans/web/routes/pages.py src/pdf_trans/web/templates/dashboard.html src/pdf_trans/web/static/dashboard.js tests/web/test_pages.py
git commit -m "fix: display task times in Beijing timezone"
```
