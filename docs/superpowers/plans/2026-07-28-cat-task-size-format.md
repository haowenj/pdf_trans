# cat_task File Size Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display `cat_task` file sizes in decimal KB, switching to MB only when the size exceeds `1000 KB`.

**Architecture:** Add one private formatting helper to `task_diagnostics.py` and keep `DiagnosticEntry.size` as the exact byte count. `format_task_snapshot()` will use the helper only at the presentation boundary, leaving discovery and ZIP behavior unchanged.

**Tech Stack:** Python 3.11+, standard-library formatting, pytest 8.

## Global Constraints

- Use decimal units: `1 KB = 1000 B` and `1 MB = 1000 KB`.
- Display sizes less than or equal to `1000 KB` in KB.
- Display sizes greater than `1000 KB` in MB.
- Always retain two decimal places.
- Preserve `[missing]` output for missing or unreadable files.
- Do not change file collection, byte-size calculation, or ZIP contents.

---

### Task 1: Format diagnostic sizes as KB or MB

**Files:**
- Modify: `tests/test_task_diagnostics.py:245-268`
- Modify: `src/pdf_trans/task_diagnostics.py:223-240`

**Interfaces:**
- Consumes: `DiagnosticEntry.size -> int | None`.
- Produces: `_format_file_size(size: int) -> str`.
- Changes: `format_task_snapshot(snapshot: TaskSnapshot) -> str` uses the new formatter.

- [ ] **Step 1: Write failing presentation tests**

Add a focused boundary test to `tests/test_task_diagnostics.py`:

```python
@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (500, "0.50 KB"),
        (1_000_000, "1000.00 KB"),
        (1_000_001, "1.00 MB"),
        (1_250_000, "1.25 MB"),
    ],
)
def test_format_uses_decimal_kb_and_switches_to_mb_above_threshold(
    tmp_path,
    size,
    expected,
):
    snapshot = TaskSnapshot(
        task_id=TASK_ID,
        source="cli",
        root=tmp_path,
        entries=(
            DiagnosticEntry(
                "result.bin",
                source_path=None,
                inline_bytes=b"x" * size,
            ),
        ),
        warnings=(),
    )

    assert f"{expected}  result.bin" in format_task_snapshot(snapshot)
```

Update the existing formatter assertion:

```python
assert "0.00 KB  task.log" in text
```

Keep the existing assertion unchanged:

```python
assert "[missing] referenced/rendered.md" in text
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
pytest tests/test_task_diagnostics.py \
  -k 'format_includes or format_uses_decimal' -v
```

Expected: five failures because the current output still contains byte values such as `1 B` and `500 B`.

- [ ] **Step 3: Implement the decimal size formatter**

Add this helper immediately before `format_task_snapshot()` in
`src/pdf_trans/task_diagnostics.py`:

```python
def _format_file_size(size: int) -> str:
    size_kb = size / 1000
    if size_kb > 1000:
        return f"{size_kb / 1000:.2f} MB"
    return f"{size_kb:.2f} KB"
```

Replace the existing byte display in `format_task_snapshot()`:

```python
lines.append(
    f"  {_format_file_size(size)}  {entry.display_path}"
)
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```bash
pytest tests/test_task_diagnostics.py \
  -k 'format_includes or format_uses_decimal' -v
```

Expected: all five selected cases pass, including the exact `1000 KB`
boundary and the missing-file output.

- [ ] **Step 5: Run regression verification**

Run:

```bash
pytest -q
git diff --check
```

Expected: all tests pass and `git diff --check` emits no output.

- [ ] **Step 6: Commit the implementation**

```bash
git add src/pdf_trans/task_diagnostics.py tests/test_task_diagnostics.py
git commit -m "feat: format task artifact sizes"
```
