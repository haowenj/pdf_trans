# MinerU 内容清洗器实施计划（Implementation Plan）

> **供智能体执行者使用：** 必须使用 `superpowers:subagent-driven-development`
>（推荐）或 `superpowers:executing-plans`，按任务逐项执行本计划。步骤使用
> checkbox（`- [ ]`）跟踪。

**Goal（目标）：** 构建一个最小化 Python 命令行程序，上传单个 PDF 到指定地址的
MinerU 3.4.4 异步接口（默认使用本地服务），下载并安全解压结果 ZIP，清洗其中的
content list，并输出数量统计。

**Architecture（架构）：** 以 `cleaner`、`archive`、`client`、`workflow` 和命令行入口五个职责边界
组织代码。纯数据清洗与 ZIP 处理不依赖网络；MinerU 客户端通过注入 `httpx.Client`、
休眠函数和时钟实现确定性测试；工作流只负责编排这些组件。

**Tech Stack（技术栈）：** Python 3.11+、标准库（`argparse`、`dataclasses`、`json`、
`pathlib`、`zipfile`）、`httpx`、`pytest`。

**设计依据：**
[中文设计文档](../specs/2026-07-26-mineru-content-cleaner-design.md)

## Global Constraints（全局约束）

- MinerU 版本为 3.4.4；服务地址通过 `svr_url` 传递，默认值为
  `http://127.0.0.1:7100`，请求逻辑不得写死该地址。
- `svr_url` 是本程序连接 MinerU API 的基础地址，不作为 MinerU multipart
  表单中的 `server_url` 字段发送。
- 命令行接收一个 PDF 文件路径，并提供可选参数 `--svr-url`；不提供 Web
  接口或批量上传。
- MinerU 参数固定为 `hybrid-engine`、`auto`、`medium`，启用公式、表格、content
  list、图片和 ZIP；关闭 Markdown、middle JSON 和 model output。
- 仅删除 `header`、`footer`、`page_number`，以及 `text.strip()` 为空的 `text`。
- 所有其他类型按原顺序和原字段保留，包括 `chart`、`ref_text` 和未知类型。
- 输出文件名固定为 `cleaned_content_list.json`，位于原始 content list 同目录。
- MinerU 产物统一写入项目根目录下的 `data/`，整个目录由 Git 忽略。
- 不实现 Markdown 生成、跨页段落处理、跨页表格合并、Document IR、Agent、数据库或
  Web 接口。
- 生产代码必须在对应失败测试之后编写。

## 文件结构

- `.gitignore`：忽略 `data/`、虚拟环境、缓存和构建产物。
- `pyproject.toml`：声明 Python 版本、`httpx`、`pytest` 和 `src` 包布局。
- `README.md`：记录安装、运行、输出位置、过滤规则和测试命令。
- `src/mineru_cleaner/__init__.py`：包元数据。
- `src/mineru_cleaner/errors.py`：项目内所有可预期错误的公共异常层级。
- `src/mineru_cleaner/cleaner.py`：纯过滤规则、JSON 读写和统计数据。
- `src/mineru_cleaner/archive.py`：ZIP 路径校验、解压和本次 content list 定位。
- `src/mineru_cleaner/client.py`：使用传入的 `svr_url` 完成 MinerU 异步任务提交、
  轮询和结果下载。
- `src/mineru_cleaner/workflow.py`：PDF 验证、服务地址传递与完整处理流程编排。
- `src/mineru_cleaner/__main__.py`：PDF 路径、`--svr-url`、用户输出和退出码。
- `tests/test_cleaner.py`：清洗规则和字段保真测试。
- `tests/test_archive.py`：安全解压和文件定位测试。
- `tests/test_client.py`：MinerU HTTP 协议测试。
- `tests/test_workflow.py`：无真实网络依赖的端到端工作流测试。
- `tests/test_cli.py`：命令行输出和错误处理测试。

---

### 任务 1：项目骨架与纯内容清洗

**文件：**

- 创建：`.gitignore`
- 创建：`pyproject.toml`
- 创建：`src/mineru_cleaner/__init__.py`
- 创建：`src/mineru_cleaner/errors.py`
- 创建：`src/mineru_cleaner/cleaner.py`
- 创建并测试：`tests/test_cleaner.py`

**接口：**

- 输入：MinerU content list 的 Python `list[Any]` 或 JSON 文件路径。
- 输出：`CleaningStats`、`clean_items(items)`、`clean_content_list_file(source,
  output)`。
- 后续任务依赖：`MinerUCleanerError`、`ContentListError`、`CleaningStats` 和
  `clean_content_list_file`。

- [ ] **步骤 1：创建项目配置和 Git 忽略规则**

创建 `.gitignore`：

```gitignore
data/
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
*.egg-info/
dist/
build/
```

创建 `pyproject.toml`：

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "mineru-content-cleaner"
version = "0.1.0"
description = "Parse a PDF with MinerU and clean its content list."
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27,<1",
]

[project.optional-dependencies]
test = [
    "pytest>=8,<9",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **步骤 2：安装项目测试依赖**

运行：

```bash
python -m pip install -e '.[test]'
```

预期：安装成功，命令退出码为 0。

- [ ] **步骤 3：先编写清洗行为测试**

创建 `tests/test_cleaner.py`：

```python
import json

import pytest

from mineru_cleaner.cleaner import (
    CleaningStats,
    clean_content_list_file,
    clean_items,
)
from mineru_cleaner.errors import ContentListError


def test_clean_items_removes_only_explicitly_filtered_entries():
    body = {
        "type": "text",
        "text": "正文",
        "page_idx": 1,
        "bbox": [1, 2, 3, 4],
    }
    heading = {
        "type": "text",
        "text": "标题",
        "text_level": 2,
        "page_idx": 1,
    }
    image = {
        "type": "image",
        "img_path": "images/a.jpg",
        "image_caption": ["图 1"],
        "page_idx": 2,
    }
    table = {
        "type": "table",
        "table_body": "<table><tr><td>A</td></tr></table>",
        "table_caption": ["表 1"],
        "page_idx": 3,
    }
    chart = {"type": "chart", "img_path": "images/chart.jpg", "content": ""}
    reference = {"type": "ref_text", "text": "1. Reference"}
    unknown = {"type": "future_type", "payload": {"unchanged": True}}
    items = [
        {"type": "header", "text": "页眉"},
        body,
        {"type": "footer", "text": "页脚"},
        heading,
        {"type": "page_number", "text": "2"},
        {"type": "text", "text": " \n\t "},
        image,
        table,
        chart,
        reference,
        unknown,
    ]

    cleaned, stats = clean_items(items)

    assert cleaned == [body, heading, image, table, chart, reference, unknown]
    assert cleaned[0] is body
    assert cleaned[2] is image
    assert cleaned[3] is table
    assert stats == CleaningStats(before_count=11, filtered_count=4, after_count=7)


def test_clean_items_preserves_text_with_missing_or_non_string_text_value():
    items = [
        {"type": "text"},
        {"type": "text", "text": None},
        {"type": "text", "text": 0},
    ]

    cleaned, stats = clean_items(items)

    assert cleaned == items
    assert stats == CleaningStats(before_count=3, filtered_count=0, after_count=3)


def test_clean_content_list_file_writes_utf8_json_and_counts(tmp_path):
    source = tmp_path / "paper_content_list.json"
    output = tmp_path / "cleaned_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"type": "header", "text": "页眉"},
                {"type": "text", "text": "中文正文", "page_idx": 0},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    stats = clean_content_list_file(source, output)

    assert stats == CleaningStats(before_count=2, filtered_count=1, after_count=1)
    assert json.loads(output.read_text(encoding="utf-8")) == [
        {"type": "text", "text": "中文正文", "page_idx": 0}
    ]
    assert "中文正文" in output.read_text(encoding="utf-8")


def test_clean_content_list_file_rejects_non_array_json(tmp_path):
    source = tmp_path / "paper_content_list.json"
    source.write_text('{"type": "text"}', encoding="utf-8")

    with pytest.raises(ContentListError, match="顶层必须是数组"):
        clean_content_list_file(source, tmp_path / "cleaned_content_list.json")
```

- [ ] **步骤 4：运行测试并确认测试因功能尚未实现而失败**

运行：

```bash
pytest tests/test_cleaner.py -v
```

预期：测试收集阶段失败，包含
`ModuleNotFoundError: No module named 'mineru_cleaner'`。

- [ ] **步骤 5：实现异常类型和纯清洗逻辑**

创建 `src/mineru_cleaner/__init__.py`：

```python
"""MinerU content-list cleaning package."""

__version__ = "0.1.0"
```

创建 `src/mineru_cleaner/errors.py`：

```python
class MinerUCleanerError(Exception):
    """Base exception for expected application failures."""


class ContentListError(MinerUCleanerError):
    """Raised when a content-list file cannot be processed."""


class ArchiveError(MinerUCleanerError):
    """Raised when a MinerU result archive cannot be processed."""


class MinerUClientError(MinerUCleanerError):
    """Raised when communication with MinerU fails."""


class WorkflowError(MinerUCleanerError):
    """Raised when workflow input validation fails."""
```

创建 `src/mineru_cleaner/cleaner.py`：

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mineru_cleaner.errors import ContentListError


@dataclass(frozen=True)
class CleaningStats:
    before_count: int
    filtered_count: int
    after_count: int


def _should_filter(item: Any) -> bool:
    if not isinstance(item, dict):
        return False

    item_type = item.get("type")
    if item_type in {"header", "footer", "page_number"}:
        return True

    text = item.get("text")
    return item_type == "text" and isinstance(text, str) and not text.strip()


def clean_items(items: list[Any]) -> tuple[list[Any], CleaningStats]:
    cleaned = [item for item in items if not _should_filter(item)]
    before_count = len(items)
    after_count = len(cleaned)
    return cleaned, CleaningStats(
        before_count=before_count,
        filtered_count=before_count - after_count,
        after_count=after_count,
    )


def clean_content_list_file(source: Path, output: Path) -> CleaningStats:
    try:
        with source.open("r", encoding="utf-8") as handle:
            items = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentListError(f"无法读取 content list：{exc}") from exc

    if not isinstance(items, list):
        raise ContentListError("content list 的 JSON 顶层必须是数组")

    cleaned, stats = clean_items(items)
    try:
        with output.open("w", encoding="utf-8") as handle:
            json.dump(cleaned, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise ContentListError(f"无法写入清洗结果：{exc}") from exc
    return stats
```

- [ ] **步骤 6：运行清洗测试并确认通过**

运行：

```bash
pytest tests/test_cleaner.py -v
```

预期：4 个测试全部通过。

- [ ] **步骤 7：提交项目骨架和清洗逻辑**

```bash
git add .gitignore pyproject.toml src/mineru_cleaner tests/test_cleaner.py
git commit -m "feat: add content list cleaning"
```

---

### 任务 2：ZIP 安全解压与 content list 定位

**文件：**

- 创建：`src/mineru_cleaner/archive.py`
- 创建并测试：`tests/test_archive.py`

**接口：**

- 输入：ZIP 字节和目标 `Path`。
- 输出：`extract_zip(archive_bytes, output_dir) -> tuple[Path, ...]`；
  `find_content_list(extracted_paths) -> Path`。
- 后续任务依赖：工作流使用返回的本次解压路径集合定位唯一 content list。

- [ ] **步骤 1：先编写 ZIP 行为测试**

创建 `tests/test_archive.py`：

```python
from io import BytesIO
from zipfile import ZipFile

import pytest

from mineru_cleaner.archive import extract_zip, find_content_list
from mineru_cleaner.errors import ArchiveError


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_extract_zip_preserves_structure_and_finds_current_content_list(tmp_path):
    archive_bytes = make_zip(
        {
            "paper/hybrid_auto/paper_content_list.json": b"[]",
            "paper/hybrid_auto/paper_content_list_v2.json": b"[]",
            "paper/hybrid_auto/images/a.jpg": b"image",
        }
    )

    extracted = extract_zip(archive_bytes, tmp_path / "data")
    content_list = find_content_list(extracted)

    assert content_list == (
        tmp_path / "data/paper/hybrid_auto/paper_content_list.json"
    ).resolve()
    assert (tmp_path / "data/paper/hybrid_auto/images/a.jpg").read_bytes() == b"image"


def test_find_content_list_ignores_files_from_previous_runs(tmp_path):
    previous = tmp_path / "data/old/hybrid_auto/old_content_list.json"
    previous.parent.mkdir(parents=True)
    previous.write_text("[]", encoding="utf-8")
    archive_bytes = make_zip(
        {"new/hybrid_auto/new_content_list.json": b"[]"}
    )

    extracted = extract_zip(archive_bytes, tmp_path / "data")

    assert find_content_list(extracted).name == "new_content_list.json"


@pytest.mark.parametrize("unsafe_name", ["../escape.json", "/absolute.json"])
def test_extract_zip_rejects_unsafe_paths(tmp_path, unsafe_name):
    archive_bytes = make_zip({unsafe_name: b"unsafe"})

    with pytest.raises(ArchiveError, match="不安全"):
        extract_zip(archive_bytes, tmp_path / "data")


@pytest.mark.parametrize("names, expected_count", [([], 0), (["a", "b"], 2)])
def test_find_content_list_requires_exactly_one_match(
    tmp_path, names, expected_count
):
    first = tmp_path / "a_content_list.json"
    second = tmp_path / "b_content_list.json"
    paths = {"a": first, "b": second}
    for name in names:
        paths[name].write_text("[]", encoding="utf-8")

    with pytest.raises(ArchiveError, match=f"找到 {expected_count} 个"):
        find_content_list(paths[name] for name in names)
```

- [ ] **步骤 2：运行测试并确认因模块尚未实现而失败**

运行：

```bash
pytest tests/test_archive.py -v
```

预期：测试收集阶段失败，包含
`ModuleNotFoundError: No module named 'mineru_cleaner.archive'`。

- [ ] **步骤 3：实现安全解压和精确定位**

创建 `src/mineru_cleaner/archive.py`：

```python
from __future__ import annotations

from io import BytesIO
from pathlib import Path, PurePosixPath
from shutil import copyfileobj
from typing import Iterable
from zipfile import BadZipFile, ZipFile

from mineru_cleaner.errors import ArchiveError


def extract_zip(archive_bytes: bytes, output_dir: Path) -> tuple[Path, ...]:
    output_root = output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            for member in archive.infolist():
                normalized_name = member.filename.replace("\\", "/")
                member_path = PurePosixPath(normalized_name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ArchiveError(f"ZIP 包含不安全路径：{member.filename}")

                target = (output_root / Path(*member_path.parts)).resolve()
                if target != output_root and output_root not in target.parents:
                    raise ArchiveError(f"ZIP 包含不安全路径：{member.filename}")

                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as destination:
                    copyfileobj(source, destination)
                extracted.append(target)
    except ArchiveError:
        raise
    except (BadZipFile, OSError) as exc:
        raise ArchiveError(f"无法解压 MinerU 结果：{exc}") from exc

    return tuple(extracted)


def find_content_list(extracted_paths: Iterable[Path]) -> Path:
    matches = sorted(
        path.resolve()
        for path in extracted_paths
        if path.is_file() and path.name.endswith("_content_list.json")
    )
    if len(matches) != 1:
        raise ArchiveError(
            f"本次 MinerU 结果中应有 1 个 content list，实际找到 {len(matches)} 个"
        )
    return matches[0]
```

- [ ] **步骤 4：运行 ZIP 测试和已有测试**

运行：

```bash
pytest tests/test_archive.py tests/test_cleaner.py -v
```

预期：10 个测试全部通过。

- [ ] **步骤 5：提交 ZIP 处理逻辑**

```bash
git add src/mineru_cleaner/archive.py tests/test_archive.py
git commit -m "feat: safely extract MinerU results"
```

---

### 任务 3：MinerU 异步 HTTP 客户端

**文件：**

- 创建：`src/mineru_cleaner/client.py`
- 创建并测试：`tests/test_client.py`

**接口：**

- 输入：`svr_url`、PDF `Path`，以及可选注入的 `httpx.Client`、休眠函数和
  单调时钟。
- 输出：`TaskSubmission`；`MinerUClient.parse_pdf(pdf_path) -> bytes`。
- 后续任务依赖：工作流调用 `parse_pdf` 获得 ZIP 字节。

- [ ] **步骤 1：先编写任务提交、轮询、下载和失败测试**

创建 `tests/test_client.py`：

```python
import httpx
import pytest

from mineru_cleaner.client import MinerUClient
from mineru_cleaner.errors import MinerUClientError


def test_parse_pdf_submits_polls_and_downloads_zip(tmp_path):
    pdf = tmp_path / "示例.pdf"
    pdf.write_bytes(b"%PDF-1.7 sample")
    statuses = iter(["pending", "processing", "completed"])
    seen_post_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_post_body
        if request.method == "POST" and request.url.path == "/tasks":
            assert str(request.url).startswith("http://mineru.example:7200/")
            seen_post_body = request.read()
            return httpx.Response(
                202,
                json={
                    "task_id": "task-1",
                    "status_url": "http://127.0.0.1:7100/tasks/task-1",
                    "result_url": "http://127.0.0.1:7100/tasks/task-1/result",
                },
            )
        if request.url.path == "/tasks/task-1":
            return httpx.Response(200, json={"status": next(statuses)})
        if request.url.path == "/tasks/task-1/result":
            return httpx.Response(
                200,
                content=b"PK fake zip",
                headers={"content-type": "application/zip"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = MinerUClient(
        svr_url="http://mineru.example:7200/",
        http_client=http_client,
        sleep=lambda _: None,
    )

    result = client.parse_pdf(pdf)

    assert result == b"PK fake zip"
    assert b'name="files"; filename="' in seen_post_body
    assert "示例.pdf".encode() in seen_post_body
    for field, value in {
        "backend": "hybrid-engine",
        "parse_method": "auto",
        "effort": "medium",
        "formula_enable": "true",
        "table_enable": "true",
        "return_md": "false",
        "return_middle_json": "false",
        "return_model_output": "false",
        "return_content_list": "true",
        "return_images": "true",
        "response_format_zip": "true",
    }.items():
        assert f'name="{field}"'.encode() in seen_post_body
        assert value.encode() in seen_post_body


def test_parse_pdf_reports_failed_task(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "task_id": "task-2",
                    "status_url": "http://test/tasks/task-2",
                    "result_url": "http://test/tasks/task-2/result",
                },
            )
        return httpx.Response(
            200,
            json={"status": "failed", "error": "模型执行失败"},
        )

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )

    with pytest.raises(MinerUClientError, match="模型执行失败"):
        client.parse_pdf(pdf)


def test_wait_for_completion_times_out_without_polling(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")
    clock_values = iter([0.0, 1801.0])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "task_id": "task-3",
                    "status_url": "http://test/tasks/task-3",
                    "result_url": "http://test/tasks/task-3/result",
                },
            )
        raise AssertionError("deadline expired before status request")

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
        clock=lambda: next(clock_values),
    )

    with pytest.raises(MinerUClientError, match="等待任务超时"):
        client.parse_pdf(pdf)


def test_submit_rejects_malformed_task_response(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(202, json={"task_id": "missing-urls"})
    )
    client = MinerUClient(http_client=httpx.Client(transport=transport))

    with pytest.raises(MinerUClientError, match="无效任务响应"):
        client.parse_pdf(pdf)


def test_wait_for_completion_rejects_unknown_status(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "task_id": "task-4",
                    "status_url": "http://test/tasks/task-4",
                    "result_url": "http://test/tasks/task-4/result",
                },
            )
        return httpx.Response(200, json={"status": "mystery"})

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(MinerUClientError, match="未知任务状态"):
        client.parse_pdf(pdf)


def test_download_rejects_non_zip_result(tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "task_id": "task-5",
                    "status_url": "http://test/tasks/task-5",
                    "result_url": "http://test/tasks/task-5/result",
                },
            )
        if request.url.path == "/tasks/task-5":
            return httpx.Response(200, json={"status": "completed"})
        return httpx.Response(
            200,
            json={"detail": "not a zip"},
            headers={"content-type": "application/json"},
        )

    client = MinerUClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(MinerUClientError, match="结果不是 ZIP"):
        client.parse_pdf(pdf)
```

- [ ] **步骤 2：运行客户端测试并确认因模块尚未实现而失败**

运行：

```bash
pytest tests/test_client.py -v
```

预期：测试收集阶段失败，包含
`ModuleNotFoundError: No module named 'mineru_cleaner.client'`。

- [ ] **步骤 3：实现 MinerU 异步客户端**

创建 `src/mineru_cleaner/client.py`：

```python
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from mineru_cleaner.errors import MinerUClientError

DEFAULT_SVR_URL = "http://127.0.0.1:7100"
POLL_INTERVAL_SECONDS = 2.0
TASK_TIMEOUT_SECONDS = 30 * 60

PARSE_FORM = {
    "backend": "hybrid-engine",
    "parse_method": "auto",
    "effort": "medium",
    "formula_enable": "true",
    "table_enable": "true",
    "return_md": "false",
    "return_middle_json": "false",
    "return_model_output": "false",
    "return_content_list": "true",
    "return_images": "true",
    "response_format_zip": "true",
}


@dataclass(frozen=True)
class TaskSubmission:
    task_id: str
    status_url: str
    result_url: str


class MinerUClient:
    def __init__(
        self,
        *,
        svr_url: str = DEFAULT_SVR_URL,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._svr_url = svr_url.rstrip("/")
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0),
            follow_redirects=True,
        )
        self._sleep = sleep
        self._clock = clock

    def __enter__(self) -> "MinerUClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def parse_pdf(self, pdf_path: Path) -> bytes:
        submission = self.submit(pdf_path)
        self.wait_for_completion(submission)
        return self.download_result(submission)

    def submit(self, pdf_path: Path) -> TaskSubmission:
        try:
            with pdf_path.open("rb") as pdf_file:
                response = self._http.post(
                    f"{self._svr_url}/tasks",
                    data=PARSE_FORM,
                    files={
                        "files": (
                            pdf_path.name,
                            pdf_file,
                            "application/pdf",
                        )
                    },
                )
        except (OSError, httpx.HTTPError) as exc:
            raise MinerUClientError(f"提交 MinerU 任务失败：{exc}") from exc

        if response.status_code != 202:
            raise MinerUClientError(
                f"提交 MinerU 任务失败：HTTP {response.status_code} {response.text}"
            )
        payload = self._json_object(response, "任务提交")
        values = [payload.get(key) for key in ("task_id", "status_url", "result_url")]
        if not all(isinstance(value, str) and value for value in values):
            raise MinerUClientError("MinerU 返回了无效任务响应")
        return TaskSubmission(
            task_id=values[0],
            status_url=values[1],
            result_url=values[2],
        )

    def wait_for_completion(self, submission: TaskSubmission) -> None:
        deadline = self._clock() + TASK_TIMEOUT_SECONDS
        while self._clock() < deadline:
            try:
                response = self._http.get(submission.status_url)
            except httpx.HTTPError as exc:
                raise MinerUClientError(f"查询 MinerU 任务失败：{exc}") from exc
            if response.status_code != 200:
                raise MinerUClientError(
                    f"查询 MinerU 任务失败：HTTP {response.status_code} {response.text}"
                )

            payload = self._json_object(response, "任务状态")
            status = payload.get("status")
            if status in {"pending", "processing"}:
                self._sleep(POLL_INTERVAL_SECONDS)
                continue
            if status == "completed":
                return
            if status == "failed":
                detail = payload.get("error") or json.dumps(payload, ensure_ascii=False)
                raise MinerUClientError(f"MinerU 任务失败：{detail}")
            raise MinerUClientError(f"MinerU 返回未知任务状态：{status!r}")

        raise MinerUClientError(
            f"等待任务超时：{submission.task_id}，超过 {TASK_TIMEOUT_SECONDS} 秒"
        )

    def download_result(self, submission: TaskSubmission) -> bytes:
        try:
            response = self._http.get(submission.result_url)
        except httpx.HTTPError as exc:
            raise MinerUClientError(f"下载 MinerU 结果失败：{exc}") from exc
        if response.status_code != 200:
            raise MinerUClientError(
                f"下载 MinerU 结果失败：HTTP {response.status_code} {response.text}"
            )
        content_type = response.headers.get("content-type", "")
        if "application/zip" not in content_type.lower():
            raise MinerUClientError(
                f"MinerU 结果不是 ZIP：content-type={content_type or 'unknown'}"
            )
        return response.content

    @staticmethod
    def _json_object(response: httpx.Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise MinerUClientError(f"MinerU {label}响应不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise MinerUClientError(f"MinerU {label}响应必须是 JSON 对象")
        return payload
```

- [ ] **步骤 4：运行客户端测试并确认通过**

运行：

```bash
pytest tests/test_client.py -v
```

预期：6 个测试全部通过。

- [ ] **步骤 5：运行当前全部测试**

运行：

```bash
pytest -v
```

预期：16 个测试全部通过。

- [ ] **步骤 6：提交 MinerU 客户端**

```bash
git add src/mineru_cleaner/client.py tests/test_client.py
git commit -m "feat: call MinerU async task API"
```

---

### 任务 4：端到端工作流编排

**文件：**

- 创建：`src/mineru_cleaner/workflow.py`
- 创建并测试：`tests/test_workflow.py`

**接口：**

- 输入：一个 PDF `Path`、`svr_url`、可选 `data_dir` 和可选 MinerU 客户端。
- 输出：`WorkflowResult`；
  `process_pdf(pdf_path, svr_url=DEFAULT_SVR_URL, data_dir=None, client=None)`。
- 后续任务依赖：命令行入口调用 `process_pdf` 并展示其统计数据和输出路径。

- [ ] **步骤 1：先编写工作流测试**

创建 `tests/test_workflow.py`：

```python
import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from mineru_cleaner.errors import WorkflowError
from mineru_cleaner.workflow import process_pdf


class FakeMinerUClient:
    def __init__(self, archive_bytes: bytes) -> None:
        self.archive_bytes = archive_bytes
        self.received_path = None

    def parse_pdf(self, pdf_path):
        self.received_path = pdf_path
        return self.archive_bytes


def make_result_zip(items) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "paper/hybrid_auto/paper_content_list.json",
            json.dumps(items, ensure_ascii=False),
        )
        archive.writestr("paper/hybrid_auto/images/a.jpg", b"image")
    return buffer.getvalue()


def test_process_pdf_runs_complete_workflow(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    body = {
        "type": "text",
        "text": "正文",
        "page_idx": 0,
        "bbox": [1, 2, 3, 4],
    }
    client = FakeMinerUClient(
        make_result_zip(
            [
                {"type": "header", "text": "页眉"},
                body,
                {"type": "chart", "img_path": "images/a.jpg"},
            ]
        )
    )

    result = process_pdf(pdf, data_dir=tmp_path / "data", client=client)

    assert client.received_path == pdf.resolve()
    assert result.before_count == 3
    assert result.filtered_count == 1
    assert result.after_count == 2
    assert result.output_path == (
        tmp_path / "data/paper/hybrid_auto/cleaned_content_list.json"
    ).resolve()
    assert json.loads(result.output_path.read_text(encoding="utf-8")) == [
        body,
        {"type": "chart", "img_path": "images/a.jpg"},
    ]
    assert (
        tmp_path / "data/paper/hybrid_auto/images/a.jpg"
    ).read_bytes() == b"image"


def test_process_pdf_passes_svr_url_to_created_client(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    archive_bytes = make_result_zip([{"type": "text", "text": "正文"}])
    received = {}

    class ContextClient:
        def __init__(self, *, svr_url):
            received["svr_url"] = svr_url

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def parse_pdf(self, pdf_path):
            return archive_bytes

    monkeypatch.setattr("mineru_cleaner.workflow.MinerUClient", ContextClient)

    process_pdf(
        pdf,
        svr_url="http://mineru.internal:7200",
        data_dir=tmp_path / "data",
    )

    assert received["svr_url"] == "http://mineru.internal:7200"


@pytest.mark.parametrize(
    "filename, create_file",
    [
        ("missing.pdf", False),
        ("document.txt", True),
    ],
)
def test_process_pdf_rejects_invalid_input(tmp_path, filename, create_file):
    source = tmp_path / filename
    if create_file:
        source.write_text("not pdf", encoding="utf-8")

    with pytest.raises(WorkflowError, match="PDF"):
        process_pdf(source, data_dir=tmp_path / "data")
```

- [ ] **步骤 2：运行工作流测试并确认因模块尚未实现而失败**

运行：

```bash
pytest tests/test_workflow.py -v
```

预期：测试收集阶段失败，包含
`ModuleNotFoundError: No module named 'mineru_cleaner.workflow'`。

- [ ] **步骤 3：实现输入验证和工作流编排**

创建 `src/mineru_cleaner/workflow.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mineru_cleaner.archive import extract_zip, find_content_list
from mineru_cleaner.cleaner import clean_content_list_file
from mineru_cleaner.client import DEFAULT_SVR_URL, MinerUClient
from mineru_cleaner.errors import WorkflowError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


class PDFParser(Protocol):
    def parse_pdf(self, pdf_path: Path) -> bytes:
        ...


@dataclass(frozen=True)
class WorkflowResult:
    source_path: Path
    output_path: Path
    before_count: int
    filtered_count: int
    after_count: int


def validate_pdf_path(pdf_path: Path) -> Path:
    resolved = pdf_path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise WorkflowError(f"PDF 文件不存在或不是普通文件：{pdf_path}")
    if resolved.suffix.lower() != ".pdf":
        raise WorkflowError(f"输入文件必须是 PDF：{pdf_path}")
    return resolved


def process_pdf(
    pdf_path: Path,
    *,
    svr_url: str = DEFAULT_SVR_URL,
    data_dir: Path | None = None,
    client: PDFParser | None = None,
) -> WorkflowResult:
    resolved_pdf = validate_pdf_path(pdf_path)
    output_root = (data_dir or DEFAULT_DATA_DIR).resolve()

    if client is None:
        with MinerUClient(svr_url=svr_url) as mineru_client:
            archive_bytes = mineru_client.parse_pdf(resolved_pdf)
    else:
        archive_bytes = client.parse_pdf(resolved_pdf)

    extracted_paths = extract_zip(archive_bytes, output_root)
    source_path = find_content_list(extracted_paths)
    output_path = source_path.parent / "cleaned_content_list.json"
    stats = clean_content_list_file(source_path, output_path)
    return WorkflowResult(
        source_path=source_path,
        output_path=output_path.resolve(),
        before_count=stats.before_count,
        filtered_count=stats.filtered_count,
        after_count=stats.after_count,
    )
```

- [ ] **步骤 4：运行工作流测试并确认通过**

运行：

```bash
pytest tests/test_workflow.py -v
```

预期：4 个测试全部通过。

- [ ] **步骤 5：运行当前全部测试**

运行：

```bash
pytest -v
```

预期：20 个测试全部通过。

- [ ] **步骤 6：提交工作流**

```bash
git add src/mineru_cleaner/workflow.py tests/test_workflow.py
git commit -m "feat: orchestrate PDF processing workflow"
```

---

### 任务 5：命令行入口与使用文档

**文件：**

- 创建：`src/mineru_cleaner/__main__.py`
- 创建：`README.md`
- 创建并测试：`tests/test_cli.py`

**接口：**

- 输入：`main(argv: list[str] | None = None) -> int`。
- 参数：必填 `pdf_path`；可选 `--svr-url`，默认
  `http://127.0.0.1:7100`。
- 输出：成功时向标准输出写入三项数量和结果路径并返回 0；可预期错误写入标准错误并返回 1。

- [ ] **步骤 1：先编写命令行输出与错误测试**

创建 `tests/test_cli.py`：

```python
from pathlib import Path

from mineru_cleaner import __main__ as cli
from mineru_cleaner.client import DEFAULT_SVR_URL
from mineru_cleaner.errors import WorkflowError
from mineru_cleaner.workflow import WorkflowResult


def test_main_prints_counts_and_output_path(tmp_path, monkeypatch, capsys):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    output = tmp_path / "data/paper/hybrid_auto/cleaned_content_list.json"
    received = {}

    def fake_process(path, *, svr_url):
        received["path"] = path
        received["svr_url"] = svr_url
        return WorkflowResult(
            source_path=Path("paper_content_list.json"),
            output_path=output,
            before_count=10,
            filtered_count=3,
            after_count=7,
        )

    monkeypatch.setattr(cli, "process_pdf", fake_process)

    exit_code = cli.main([str(pdf)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert received == {"path": pdf, "svr_url": DEFAULT_SVR_URL}
    assert "处理前数量：10" in captured.out
    assert "过滤数量：3" in captured.out
    assert "处理后数量：7" in captured.out
    assert f"输出文件：{output}" in captured.out
    assert captured.err == ""


def test_main_passes_custom_svr_url(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    received = {}

    def fake_process(path, *, svr_url):
        received["svr_url"] = svr_url
        return WorkflowResult(
            source_path=Path("paper_content_list.json"),
            output_path=Path("cleaned_content_list.json"),
            before_count=1,
            filtered_count=0,
            after_count=1,
        )

    monkeypatch.setattr(cli, "process_pdf", fake_process)

    exit_code = cli.main(
        [str(pdf), "--svr-url", "http://mineru.example:7200"]
    )

    assert exit_code == 0
    assert received["svr_url"] == "http://mineru.example:7200"


def test_main_reports_expected_error(monkeypatch, capsys):
    def fail(path, *, svr_url):
        raise WorkflowError("PDF 文件不存在")

    monkeypatch.setattr(cli, "process_pdf", fail)

    exit_code = cli.main(["missing.pdf"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "错误：PDF 文件不存在" in captured.err
```

- [ ] **步骤 2：运行命令行测试并确认因入口尚未实现而失败**

运行：

```bash
pytest tests/test_cli.py -v
```

预期：测试收集阶段失败，包含无法导入
`mineru_cleaner.__main__` 的错误。

- [ ] **步骤 3：实现命令行入口**

创建 `src/mineru_cleaner/__main__.py`：

```python
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from mineru_cleaner.client import DEFAULT_SVR_URL
from mineru_cleaner.errors import MinerUCleanerError
from mineru_cleaner.workflow import process_pdf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m mineru_cleaner",
        description="使用 MinerU 解析 PDF 并清洗 content list。",
    )
    parser.add_argument("pdf_path", type=Path, help="要解析的 PDF 文件路径")
    parser.add_argument(
        "--svr-url",
        default=DEFAULT_SVR_URL,
        help=f"MinerU API 服务地址（默认：{DEFAULT_SVR_URL}）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = process_pdf(args.pdf_path, svr_url=args.svr_url)
    except (MinerUCleanerError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"处理前数量：{result.before_count}")
    print(f"过滤数量：{result.filtered_count}")
    print(f"处理后数量：{result.after_count}")
    print(f"输出文件：{result.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **步骤 4：运行命令行测试并确认通过**

运行：

```bash
pytest tests/test_cli.py -v
```

预期：3 个测试全部通过。

- [ ] **步骤 5：编写最小使用说明**

创建 `README.md`：

````markdown
# MinerU Content Cleaner

上传一个 PDF 到 MinerU 3.4.4 异步接口，下载解析结果，并清洗其中的
content list。

## 环境

- Python 3.11+
- MinerU 3.4.4 服务；默认地址为 `http://127.0.0.1:7100`

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 使用

```bash
python -m mineru_cleaner /path/to/document.pdf
```

连接其他地址的 MinerU：

```bash
python -m mineru_cleaner /path/to/document.pdf \
  --svr-url http://mineru.example:7100
```

MinerU 结果解压到项目的 `data/` 目录。清洗结果保存在原始 content list
同目录的 `cleaned_content_list.json` 中。命令输出处理前数量、过滤数量、
处理后数量和结果文件路径。

程序只删除以下内容：

- `header`
- `footer`
- `page_number`
- `type` 为 `text` 且 `text.strip()` 为空的条目

其他条目按原顺序、原字段保留。

## 测试

```bash
python -m pip install -e '.[test]'
pytest -v
```
````

- [ ] **步骤 6：运行全部测试和命令行帮助**

运行：

```bash
pytest -v
python -m mineru_cleaner --help
```

预期：23 个测试全部通过；帮助输出包含
`使用 MinerU 解析 PDF 并清洗 content list`、位置参数 `pdf_path` 和
可选参数 `--svr-url`。

- [ ] **步骤 7：提交命令行和文档**

```bash
git add src/mineru_cleaner/__main__.py tests/test_cli.py README.md
git commit -m "feat: add MinerU cleaner CLI"
```

---

### 任务 6：完整验证

**文件：**

- 不创建或修改生产文件。
- 验证产物：`data/` 下的 MinerU 结果和 `cleaned_content_list.json`，由 Git 忽略。

**接口：**

- 输入：本地 MinerU 健康接口和用户提供的
  `/Users/wenjuhao/Downloads/processes-12-02888-v2.pdf`。
- 输出：通过的测试、成功的真实解析、正确的统计关系和干净的 Git 工作区。

- [ ] **步骤 1：检查代码格式问题、测试和 Git 忽略规则**

运行：

```bash
git diff --check
pytest -v
git check-ignore -v data/example
```

预期：`git diff --check` 无输出；23 个测试全部通过；最后一条命令显示
`.gitignore` 中的 `data/` 规则。

- [ ] **步骤 2：确认本地 MinerU 3.4.4 健康**

运行：

```bash
curl -fsS http://127.0.0.1:7100/health
```

预期：返回 JSON，包含 `"status":"healthy"` 和 `"version":"3.4.4"`。

- [ ] **步骤 3：使用样例 PDF 执行真实端到端解析**

运行：

```bash
python -m mineru_cleaner \
  /Users/wenjuhao/Downloads/processes-12-02888-v2.pdf
```

预期：命令退出码为 0，输出处理前数量、过滤数量、处理后数量和
`cleaned_content_list.json` 的绝对路径。

- [ ] **步骤 4：验证真实结果的数量关系、过滤规则和 Git 状态**

对样例 PDF 的固定输出路径执行以下只读检查：

```bash
python -c 'import json,sys; p=sys.argv[1]; a=json.load(open(p,encoding="utf-8")); assert all(x.get("type") not in {"header","footer","page_number"} for x in a if isinstance(x,dict)); assert all(not (x.get("type")=="text" and isinstance(x.get("text"),str) and not x["text"].strip()) for x in a if isinstance(x,dict)); print(len(a))' data/processes-12-02888-v2/hybrid_auto/cleaned_content_list.json
git status --short
```

预期：Python 检查输出处理后数组长度且退出码为 0；`git status --short`
不显示 `data/` 下的任何文件。

- [ ] **步骤 5：确认最终 Git 状态**

```bash
git status --short --branch
```

预期：显示当前分支；没有未提交的跟踪文件改动，也不显示 `data/` 下的解析产物。
