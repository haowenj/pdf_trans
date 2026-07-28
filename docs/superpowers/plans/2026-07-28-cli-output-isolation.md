# CLI Output Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every full CLI PDF run write beneath a unique UUID directory so same-named inputs cannot overwrite each other.

**Architecture:** Keep `process_pdf(data_dir=...)` unchanged and make the CLI own its run namespace. Each full CLI invocation generates one UUID and passes `DEFAULT_DATA_DIR / "runs" / str(uuid)` to the workflow; Web continues to pass its existing task/attempt directory.

**Tech Stack:** Python 3.11+, `uuid` standard library, `pathlib`, pytest 8.

## Global Constraints

- Every full CLI PDF workflow uses an independent, collision-resistant output directory.
- The directory layout is `data/runs/<run-uuid>/<mineru-archive-layout>/...`.
- `process_pdf(data_dir=...)` keeps its current behavior and signature.
- Web keeps `tasks/<task-uuid>/attempts/<attempt-count>/...`.
- `--translate-only` keeps writing next to the explicitly supplied normalized file.
- No new runtime dependency is introduced.

---

### Task 1: Isolate Full CLI Runs

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/pdf_trans/__main__.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `pdf_trans.workflow.DEFAULT_DATA_DIR: pathlib.Path`, `uuid.uuid4() -> uuid.UUID`, and the existing `process_pdf(pdf_path, *, svr_url, data_dir)` interface.
- Produces: one explicit `data_dir` per full CLI invocation with the form `DEFAULT_DATA_DIR / "runs" / <uuid>`.

- [ ] **Step 1: Write the failing CLI isolation regression test**

Add imports and a test that invokes the CLI twice with the same PDF while capturing the output roots:

```python
from uuid import UUID


def test_main_uses_unique_run_directory_for_each_full_workflow(
    tmp_path, monkeypatch, capsys
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    data_root = tmp_path / "data"
    received_data_dirs = []

    def stop_after_capture(path, *, svr_url, data_dir=None):
        received_data_dirs.append(data_dir)
        raise WorkflowError("stop after capture")

    monkeypatch.setattr(cli, "DEFAULT_DATA_DIR", data_root)
    monkeypatch.setattr(cli, "process_pdf", stop_after_capture)

    assert cli.main([str(pdf)]) == 1
    assert cli.main([str(pdf)]) == 1

    assert len(received_data_dirs) == 2
    assert received_data_dirs[0] != received_data_dirs[1]
    for data_dir in received_data_dirs:
        assert data_dir.parent == data_root / "runs"
        UUID(data_dir.name)
```

This test deliberately allows `data_dir=None` so the current implementation reaches the expected `WorkflowError`; before the fix, its path assertions fail because both captured values are `None`.

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```bash
pytest tests/test_cli.py::test_main_uses_unique_run_directory_for_each_full_workflow -v
```

Expected: FAIL at `assert received_data_dirs[0] != received_data_dirs[1]` because both values are `None`.

- [ ] **Step 3: Update existing CLI test doubles for the new explicit argument**

Change every full-workflow `fake_process`/`fail` test double in `tests/test_cli.py` from:

```python
def fake_process(path, *, svr_url):
```

to:

```python
def fake_process(path, *, svr_url, data_dir):
```

Where an existing assertion checks received arguments, capture `data_dir` and assert:

```python
assert received["data_dir"].parent == cli.DEFAULT_DATA_DIR / "runs"
UUID(received["data_dir"].name)
```

Keep translation-only doubles unchanged because that path does not call `process_pdf()`.

- [ ] **Step 4: Implement the minimal CLI run namespace**

Update `src/pdf_trans/__main__.py` imports:

```python
import uuid

from pdf_trans.workflow import (
    DEFAULT_DATA_DIR,
    process_pdf,
    process_translation_file,
)
```

Replace the full-workflow call with:

```python
        run_data_dir = DEFAULT_DATA_DIR / "runs" / str(uuid.uuid4())
        result = process_pdf(
            args.pdf_path,
            svr_url=args.svr_url,
            data_dir=run_data_dir,
        )
```

Do not create the directory in the CLI; `extract_zip()` remains responsible for creating the supplied output root.

- [ ] **Step 5: Run CLI tests and verify GREEN**

Run:

```bash
pytest tests/test_cli.py -v
```

Expected: all CLI tests PASS, including the two-invocation isolation regression.

- [ ] **Step 6: Document the CLI directory layout**

In `README.md` under “输出文件”, distinguish CLI and Web layouts:

```text
CLI 每次完整解析都会生成独立运行目录：

data/runs/<run-uuid>/<mineru-archive-layout>/...

Web 面板继续使用任务与执行次数隔离：

data/web/tasks/<task-id>/attempts/<attempt-count>/...
```

State that `--translate-only` keeps writing beside its supplied normalized file.

- [ ] **Step 7: Verify Web isolation remains unchanged**

Run:

```bash
pytest tests/web/test_task_runner.py::test_runner_starts_full_workflow_when_no_checkpoint_exists -v
```

Expected: PASS and the test continues to assert `data_dir == tmp_path / "tasks/a/attempts/1"`.

- [ ] **Step 8: Run full verification**

Run:

```bash
pytest -v
python -m build
git diff --check
```

Expected: the complete test suite passes, both sdist and wheel build successfully, and `git diff --check` produces no output.

- [ ] **Step 9: Commit the implementation**

```bash
git add tests/test_cli.py src/pdf_trans/__main__.py README.md
git commit -m "fix: isolate CLI parsing outputs"
```
