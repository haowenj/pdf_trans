# Configurable MinerU Hybrid Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow PDF Trans to select either local `hybrid-engine` parsing or GPUStack-backed `hybrid-http-client` parsing through global configuration while preserving the current default behavior.

**Architecture:** Define and validate the MinerU backend pair once in the core client module, then pass the resolved backend and optional model-server URL explicitly through CLI/Web settings, workflow orchestration, and `MinerUClient`. Build a fresh multipart form per request so local mode omits `server_url`, while remote mode includes it; both modes keep `effort=medium` and `image_analysis=false`.

**Tech Stack:** Python 3.11+, `argparse`, immutable dataclasses, `httpx`, `pytest`, Docker Compose, Markdown.

**Design:** [可配置 MinerU Hybrid 后端设计](../specs/2026-07-29-configurable-mineru-hybrid-backend-design.md)

## Global Constraints

- MinerU API compatibility target is MinerU 3.4.4.
- Supported backends are exactly `hybrid-engine` and `hybrid-http-client`.
- The default backend remains `hybrid-engine`.
- Both backends send `effort=medium` and `image_analysis=false`.
- `hybrid-http-client` requires a non-empty `server_url`; `hybrid-engine` ignores it and omits it from the request.
- `PDF_TRANS_MINERU_URL` remains the full MinerU API base URL and must not be confused with the GPUStack model-server URL.
- PDF Trans must never read, persist, transmit, or log `MINERU_VL_API_KEY`; that variable belongs to the MinerU service environment.
- Existing injected workflow clients, task resume behavior, parsing outputs, translation, and rendering must remain unchanged.
- Preserve the user's existing `.gitignore` modification and never stage it as part of these tasks.
- Production behavior must be introduced only after the corresponding test has failed for the expected reason.

---

## File Structure

- `src/pdf_trans/errors.py`: add the expected configuration error used by CLI, Web settings, and the client.
- `src/pdf_trans/client.py`: own supported-backend constants, normalized configuration, multipart-form generation, and MinerU HTTP communication.
- `src/pdf_trans/workflow.py`: pass backend configuration to a newly created `MinerUClient`.
- `src/pdf_trans/__main__.py`: expose environment-backed CLI switches and validate full-workflow configuration before creating a CLI task.
- `src/pdf_trans/web/config.py`: read and validate process-global Web backend settings.
- `src/pdf_trans/web/task_runner.py`: pass Web backend settings into the full workflow.
- `docker-compose.yml`: forward backend and GPUStack URL environment variables to the Web container.
- `README.md`: document local and GPUStack deployment modes and the MinerU-side API-key requirement.
- `tests/test_client.py`: verify validation and the exact multipart fields for both backends.
- `tests/test_workflow.py`: verify explicit backend propagation to the client.
- `tests/test_cli.py`: verify environment defaults, command-line overrides, and early validation.
- `tests/web/test_config.py`: verify Web defaults, remote settings, normalization, and invalid combinations.
- `tests/web/test_task_runner.py`: verify Web task propagation.
- `tests/test_deployment_config.py`: verify Compose forwards the two new environment variables.

---

### Task 1: MinerU backend configuration and request form

**Files:**
- Modify: `src/pdf_trans/errors.py`
- Modify: `src/pdf_trans/client.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Produces: `DEFAULT_MINERU_BACKEND: str`
- Produces: `SUPPORTED_MINERU_BACKENDS: tuple[str, ...]`
- Produces: `MinerUBackendConfig(backend: str, server_url: str | None)`
- Produces: `resolve_mineru_backend_config(backend: str, server_url: str | None) -> MinerUBackendConfig`
- Extends: `MinerUClient.__init__(..., backend: str = DEFAULT_MINERU_BACKEND, server_url: str | None = None)`
- Raises: `MinerUConfigError` for an unsupported backend or a missing remote URL.

- [ ] **Step 1: Write failing multipart and validation tests**

Add `DEFAULT_MINERU_BACKEND`, `MinerUClient`, `resolve_mineru_backend_config`, and `MinerUConfigError` imports to `tests/test_client.py`. Extend the existing default request assertion with:

```python
assert b'name="server_url"' not in seen_post_body
assert b'name="image_analysis"' in seen_post_body
assert b"false" in seen_post_body
```

Add a focused remote submission test:

```python
def test_submit_uses_remote_hybrid_backend_and_server_url(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    seen_post_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_post_body
        seen_post_body = request.read()
        return httpx.Response(
            202,
            json={
                "task_id": "remote-1",
                "status_url": "http://test/tasks/remote-1",
                "result_url": "http://test/tasks/remote-1/result",
            },
        )

    client = MinerUClient(
        backend="hybrid-http-client",
        server_url="http://gpustack:8000/",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.submit(pdf)

    assert b'name="backend"' in seen_post_body
    assert b"hybrid-http-client" in seen_post_body
    assert b'name="server_url"' in seen_post_body
    assert b"http://gpustack:8000" in seen_post_body
    assert b'name="image_analysis"' in seen_post_body
    assert b"false" in seen_post_body
```

Add configuration validation tests:

```python
def test_resolve_backend_defaults_to_local_and_ignores_server_url():
    config = resolve_mineru_backend_config(
        DEFAULT_MINERU_BACKEND,
        "http://unused.example/",
    )

    assert config.backend == "hybrid-engine"
    assert config.server_url is None


@pytest.mark.parametrize(
    ("backend", "server_url", "message"),
    [
        ("pipeline", None, "PDF_TRANS_MINERU_BACKEND"),
        ("hybrid-http-client", None, "PDF_TRANS_MINERU_SERVER_URL"),
        ("hybrid-http-client", "  ", "PDF_TRANS_MINERU_SERVER_URL"),
    ],
)
def test_resolve_backend_rejects_invalid_configuration(
    backend, server_url, message
):
    with pytest.raises(MinerUConfigError, match=message):
        resolve_mineru_backend_config(backend, server_url)
```

- [ ] **Step 2: Run the client tests and verify RED**

Run:

```bash
pytest -q tests/test_client.py
```

Expected: collection or assertion failures because the configuration symbols and `image_analysis=false` request field do not exist yet.

- [ ] **Step 3: Implement configuration normalization and fresh request forms**

Add to `src/pdf_trans/errors.py`:

```python
class MinerUConfigError(PDFTransError):
    """Raised when MinerU backend configuration is invalid."""
```

Replace the mutable fixed form in `src/pdf_trans/client.py` with:

```python
from types import MappingProxyType

from pdf_trans.errors import MinerUClientError, MinerUConfigError

DEFAULT_MINERU_BACKEND = "hybrid-engine"
REMOTE_MINERU_BACKEND = "hybrid-http-client"
SUPPORTED_MINERU_BACKENDS = (
    DEFAULT_MINERU_BACKEND,
    REMOTE_MINERU_BACKEND,
)

BASE_PARSE_FORM = MappingProxyType(
    {
        "parse_method": "auto",
        "effort": "medium",
        "formula_enable": "true",
        "table_enable": "true",
        "image_analysis": "false",
        "return_md": "false",
        "return_middle_json": "false",
        "return_model_output": "false",
        "return_content_list": "true",
        "return_images": "true",
        "response_format_zip": "true",
    }
)


@dataclass(frozen=True)
class MinerUBackendConfig:
    backend: str
    server_url: str | None


def resolve_mineru_backend_config(
    backend: str = DEFAULT_MINERU_BACKEND,
    server_url: str | None = None,
) -> MinerUBackendConfig:
    if backend not in SUPPORTED_MINERU_BACKENDS:
        allowed = ", ".join(SUPPORTED_MINERU_BACKENDS)
        raise MinerUConfigError(
            "PDF_TRANS_MINERU_BACKEND "
            f"必须是以下值之一：{allowed}；当前值：{backend!r}"
        )

    normalized_url = (server_url or "").strip().rstrip("/")
    if backend == REMOTE_MINERU_BACKEND:
        if not normalized_url:
            raise MinerUConfigError(
                "PDF_TRANS_MINERU_SERVER_URL 在 "
                "hybrid-http-client 模式下不能为空"
            )
        return MinerUBackendConfig(backend, normalized_url)
    return MinerUBackendConfig(backend, None)
```

Extend `MinerUClient.__init__` and store the resolved config:

```python
backend: str = DEFAULT_MINERU_BACKEND,
server_url: str | None = None,
```

```python
self._backend_config = resolve_mineru_backend_config(
    backend,
    server_url,
)
```

Add a request-form builder:

```python
def _parse_form(self) -> dict[str, str]:
    form = dict(BASE_PARSE_FORM)
    form["backend"] = self._backend_config.backend
    if self._backend_config.server_url is not None:
        form["server_url"] = self._backend_config.server_url
    return form
```

Change the `/tasks` request from `data=PARSE_FORM` to:

```python
data=self._parse_form(),
```

- [ ] **Step 4: Run the client tests and verify GREEN**

Run:

```bash
pytest -q tests/test_client.py
```

Expected: all client tests pass, including exact local/remote multipart behavior.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/pdf_trans/errors.py src/pdf_trans/client.py tests/test_client.py
git commit -m "feat: configure MinerU hybrid request backend"
```

---

### Task 2: Workflow propagation

**Files:**
- Modify: `src/pdf_trans/workflow.py`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `DEFAULT_MINERU_BACKEND`
- Extends: `process_pdf(..., mineru_backend: str = DEFAULT_MINERU_BACKEND, mineru_server_url: str | None = None)`
- Passes: the two values only when constructing the real `MinerUClient`; an injected `PDFParser` remains authoritative.

- [ ] **Step 1: Write a failing workflow propagation test**

Replace `test_process_pdf_passes_svr_url_to_created_client` with a test whose context client accepts and records all three connection values:

```python
def test_process_pdf_passes_mineru_config_to_created_client(
    tmp_path, monkeypatch
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    archive_bytes = make_result_zip([{"type": "text", "text": "正文"}])
    received = {}
    translator = FakeTranslator()

    class ContextClient:
        def __init__(self, *, svr_url, backend, server_url):
            received.update(
                svr_url=svr_url,
                backend=backend,
                server_url=server_url,
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def parse_pdf(self, pdf_path):
            return archive_bytes

    monkeypatch.setattr("pdf_trans.workflow.MinerUClient", ContextClient)

    process_pdf(
        pdf,
        svr_url="http://mineru.internal:7200",
        mineru_backend="hybrid-http-client",
        mineru_server_url="http://gpustack:8000",
        data_dir=tmp_path / "data",
        translator=translator,
    )

    assert received == {
        "svr_url": "http://mineru.internal:7200",
        "backend": "hybrid-http-client",
        "server_url": "http://gpustack:8000",
    }
```

- [ ] **Step 2: Run the workflow test and verify RED**

Run:

```bash
pytest -q tests/test_workflow.py::test_process_pdf_passes_mineru_config_to_created_client
```

Expected: failure because `process_pdf` does not accept `mineru_backend` or `mineru_server_url`.

- [ ] **Step 3: Add explicit workflow parameters**

Import the default:

```python
from pdf_trans.client import (
    DEFAULT_MINERU_BACKEND,
    DEFAULT_SVR_URL,
    MinerUClient,
)
```

Extend `_process_pdf_stages`:

```python
def _process_pdf_stages(
    pdf_path: Path,
    *,
    svr_url: str,
    mineru_backend: str,
    mineru_server_url: str | None,
    data_dir: Path | None,
    client: PDFParser | None,
    translator: TextTranslator | None,
    translation_max_retries: int | None,
    translation_concurrency: int | None,
) -> WorkflowResult:
```

Construct the real client with:

```python
with MinerUClient(
    svr_url=svr_url,
    backend=mineru_backend,
    server_url=mineru_server_url,
) as mineru_client:
```

Extend `process_pdf`:

```python
def process_pdf(
    pdf_path: Path,
    *,
    svr_url: str = DEFAULT_SVR_URL,
    mineru_backend: str = DEFAULT_MINERU_BACKEND,
    mineru_server_url: str | None = None,
    data_dir: Path | None = None,
    client: PDFParser | None = None,
    translator: TextTranslator | None = None,
    translation_max_retries: int | None = None,
    translation_concurrency: int | None = None,
) -> WorkflowResult:
```

Pass the new values to `_process_pdf_stages`:

```python
mineru_backend=mineru_backend,
mineru_server_url=mineru_server_url,
```

- [ ] **Step 4: Run workflow tests and verify GREEN**

Run:

```bash
pytest -q tests/test_workflow.py
```

Expected: all workflow tests pass; tests using an injected client continue to work without needing backend arguments.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/pdf_trans/workflow.py tests/test_workflow.py
git commit -m "feat: pass MinerU backend through workflow"
```

---

### Task 3: Environment-backed CLI switches

**Files:**
- Modify: `src/pdf_trans/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `DEFAULT_MINERU_BACKEND`, `resolve_mineru_backend_config`
- Adds CLI options: `--mineru-backend`, `--mineru-server-url`
- Environment defaults: `PDF_TRANS_MINERU_BACKEND`, `PDF_TRANS_MINERU_SERVER_URL`
- Guarantees: invalid full-workflow configuration fails before `start_cli_task`.

- [ ] **Step 1: Update test doubles and add failing CLI configuration tests**

Update every `fake_process`, `fail`, and `stop_after_capture` full-workflow double in `tests/test_cli.py` to accept:

```python
def fake_process(
    path,
    *,
    svr_url,
    mineru_backend,
    mineru_server_url,
    data_dir,
):
```

Record the two new values in the primary success test and assert:

```python
assert received["mineru_backend"] == "hybrid-engine"
assert received["mineru_server_url"] is None
```

Add environment and override tests:

```python
def test_main_reads_remote_mineru_config_from_environment(
    tmp_path, monkeypatch, capsys
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    received = {}

    def stop_after_capture(
        path,
        *,
        svr_url,
        mineru_backend,
        mineru_server_url,
        data_dir,
    ):
        received.update(
            backend=mineru_backend,
            server_url=mineru_server_url,
        )
        raise WorkflowError("stop after capture")

    monkeypatch.setenv(
        "PDF_TRANS_MINERU_BACKEND",
        "hybrid-http-client",
    )
    monkeypatch.setenv(
        "PDF_TRANS_MINERU_SERVER_URL",
        "http://gpustack:8000/",
    )
    monkeypatch.setattr(cli, "DEFAULT_DATA_DIR", tmp_path / "runtime")
    monkeypatch.setattr(cli, "process_pdf", stop_after_capture)

    assert cli.main([str(pdf)]) == 1
    assert received == {
        "backend": "hybrid-http-client",
        "server_url": "http://gpustack:8000",
    }
    capsys.readouterr()


def test_main_explicit_mineru_options_override_environment(
    tmp_path, monkeypatch, capsys
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    received = {}

    def stop_after_capture(
        path,
        *,
        svr_url,
        mineru_backend,
        mineru_server_url,
        data_dir,
    ):
        received.update(
            backend=mineru_backend,
            server_url=mineru_server_url,
        )
        raise WorkflowError("stop after capture")

    monkeypatch.setenv("PDF_TRANS_MINERU_BACKEND", "hybrid-engine")
    monkeypatch.setattr(cli, "DEFAULT_DATA_DIR", tmp_path / "runtime")
    monkeypatch.setattr(cli, "process_pdf", stop_after_capture)

    assert cli.main(
        [
            str(pdf),
            "--mineru-backend",
            "hybrid-http-client",
            "--mineru-server-url",
            "http://gpustack:9000/",
        ]
    ) == 1
    assert received == {
        "backend": "hybrid-http-client",
        "server_url": "http://gpustack:9000",
    }
    capsys.readouterr()


def test_main_rejects_remote_backend_without_url_before_starting_task(
    monkeypatch,
):
    started = []
    monkeypatch.delenv("PDF_TRANS_MINERU_SERVER_URL", raising=False)
    monkeypatch.setattr(
        cli,
        "start_cli_task",
        lambda *args, **kwargs: started.append((args, kwargs)),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["paper.pdf", "--mineru-backend", "hybrid-http-client"])

    assert exc_info.value.code == 2
    assert started == []
```

- [ ] **Step 2: Run CLI tests and verify RED**

Run:

```bash
pytest -q tests/test_cli.py
```

Expected: failures because the parser and workflow call do not expose the new configuration.

- [ ] **Step 3: Implement environment defaults, options, and early validation**

Add imports:

```python
import os

from pdf_trans.client import (
    DEFAULT_MINERU_BACKEND,
    DEFAULT_SVR_URL,
    resolve_mineru_backend_config,
)
from pdf_trans.errors import MinerUConfigError, PDFTransError
```

Add parser options:

```python
parser.add_argument(
    "--mineru-backend",
    default=os.environ.get(
        "PDF_TRANS_MINERU_BACKEND",
        DEFAULT_MINERU_BACKEND,
    ),
    help=(
        "MinerU 解析后端：hybrid-engine 或 hybrid-http-client"
    ),
)
parser.add_argument(
    "--mineru-server-url",
    default=os.environ.get("PDF_TRANS_MINERU_SERVER_URL"),
    help="hybrid-http-client 使用的 OpenAI 兼容模型服务地址",
)
```

Reuse one parser instance in `main`:

```python
parser = build_parser()
args = parser.parse_args(arguments)
if (args.pdf_path is None) == (args.translate_only is None):
    parser.error("必须且只能指定 PDF 路径或 --translate-only")
```

For a full workflow, validate before `start_cli_task`:

```python
mineru_config = None
if args.pdf_path is not None:
    try:
        mineru_config = resolve_mineru_backend_config(
            args.mineru_backend,
            args.mineru_server_url,
        )
    except MinerUConfigError as exc:
        parser.error(str(exc))
```

Pass the resolved values:

```python
assert mineru_config is not None
result = process_pdf(
    args.pdf_path,
    svr_url=args.svr_url,
    mineru_backend=mineru_config.backend,
    mineru_server_url=mineru_config.server_url,
    data_dir=task.root,
)
```

- [ ] **Step 4: Run CLI tests and verify GREEN**

Run:

```bash
pytest -q tests/test_cli.py
```

Expected: all CLI tests pass; translate-only mode remains independent of MinerU backend configuration.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/pdf_trans/__main__.py tests/test_cli.py
git commit -m "feat: add MinerU backend CLI configuration"
```

---

### Task 4: Web settings and task propagation

**Files:**
- Modify: `src/pdf_trans/web/config.py`
- Modify: `src/pdf_trans/web/task_runner.py`
- Test: `tests/web/test_config.py`
- Test: `tests/web/test_task_runner.py`

**Interfaces:**
- Extends: `WebSettings` with `mineru_backend: str` and `mineru_server_url: str | None`
- Environment defaults: `PDF_TRANS_MINERU_BACKEND=hybrid-engine`, no server URL.
- Passes: both fields to `process_pdf` only for a full parse attempt.

- [ ] **Step 1: Write failing Web settings tests**

Extend the default assertions in `tests/web/test_config.py`:

```python
assert settings.mineru_backend == "hybrid-engine"
assert settings.mineru_server_url is None
```

Add the remote variables to the existing environment test:

```python
"PDF_TRANS_MINERU_BACKEND": "hybrid-http-client",
"PDF_TRANS_MINERU_SERVER_URL": "http://gpustack:8000/",
```

Assert normalized values:

```python
assert settings.mineru_backend == "hybrid-http-client"
assert settings.mineru_server_url == "http://gpustack:8000"
```

Add invalid configuration tests:

```python
from pdf_trans.errors import MinerUConfigError


@pytest.mark.parametrize(
    "environ",
    [
        {"PDF_TRANS_MINERU_BACKEND": "pipeline"},
        {"PDF_TRANS_MINERU_BACKEND": "hybrid-http-client"},
        {
            "PDF_TRANS_MINERU_BACKEND": "hybrid-http-client",
            "PDF_TRANS_MINERU_SERVER_URL": " ",
        },
    ],
)
def test_settings_reject_invalid_mineru_configuration(
    tmp_path: Path, environ
) -> None:
    with pytest.raises(MinerUConfigError, match="PDF_TRANS_MINERU_"):
        WebSettings.from_env(environ=environ, project_root=tmp_path)
```

- [ ] **Step 2: Write a failing TaskRunner propagation assertion**

Update the `settings()` helper in `tests/web/test_task_runner.py`:

```python
mineru_backend="hybrid-http-client",
mineru_server_url="http://gpustack:8000",
```

Update the full-workflow double:

```python
def full(
    path,
    *,
    svr_url,
    mineru_backend,
    mineru_server_url,
    data_dir,
):
    received.update(
        path=path,
        svr_url=svr_url,
        mineru_backend=mineru_backend,
        mineru_server_url=mineru_server_url,
        data_dir=data_dir,
    )
```

Add assertions:

```python
assert received["mineru_backend"] == "hybrid-http-client"
assert received["mineru_server_url"] == "http://gpustack:8000"
```

- [ ] **Step 3: Run Web tests and verify RED**

Run:

```bash
pytest -q tests/web/test_config.py tests/web/test_task_runner.py
```

Expected: failures because `WebSettings` and `TaskRunner` lack the new fields.

- [ ] **Step 4: Implement validated Web settings**

Import:

```python
from pdf_trans.client import (
    DEFAULT_MINERU_BACKEND,
    resolve_mineru_backend_config,
)
```

Extend `WebSettings`:

```python
mineru_url: str
mineru_backend: str
mineru_server_url: str | None
host: str
port: int
```

Resolve the configuration before returning the dataclass:

```python
mineru_config = resolve_mineru_backend_config(
    values.get(
        "PDF_TRANS_MINERU_BACKEND",
        DEFAULT_MINERU_BACKEND,
    ),
    values.get("PDF_TRANS_MINERU_SERVER_URL"),
)
```

Populate:

```python
mineru_backend=mineru_config.backend,
mineru_server_url=mineru_config.server_url,
```

- [ ] **Step 5: Pass Web settings into the workflow**

Update `TaskRunner.run()`:

```python
result = self.services.process_pdf(
    source,
    svr_url=self.settings.mineru_url,
    mineru_backend=self.settings.mineru_backend,
    mineru_server_url=self.settings.mineru_server_url,
    data_dir=attempt_dir,
)
```

The only direct `WebSettings(...)` construction is the `settings()` helper in
`tests/web/test_task_runner.py`; keep the two values added in Step 2:

```python
mineru_backend="hybrid-http-client",
mineru_server_url="http://gpustack:8000",
```

- [ ] **Step 6: Run Web tests and verify GREEN**

Run:

```bash
pytest -q tests/web/test_config.py tests/web/test_task_runner.py
```

Expected: all selected Web tests pass, including resume behavior that does not call MinerU.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/pdf_trans/web/config.py src/pdf_trans/web/task_runner.py tests/web/test_config.py tests/web/test_task_runner.py
git commit -m "feat: configure MinerU backend for web tasks"
```

---

### Task 5: Compose forwarding, operator documentation, and full verification

**Files:**
- Create: `tests/test_deployment_config.py`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Compose forwards `PDF_TRANS_MINERU_BACKEND` and `PDF_TRANS_MINERU_SERVER_URL`.
- README distinguishes MinerU API URL, GPUStack model URL, model source, and API-key ownership.

- [ ] **Step 1: Write a failing Compose forwarding test**

Create `tests/test_deployment_config.py`:

```python
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_compose_forwards_mineru_backend_configuration() -> None:
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert (
        "PDF_TRANS_MINERU_BACKEND: "
        "${PDF_TRANS_MINERU_BACKEND:-hybrid-engine}"
    ) in compose
    assert (
        "PDF_TRANS_MINERU_SERVER_URL: "
        "${PDF_TRANS_MINERU_SERVER_URL:-}"
    ) in compose
```

- [ ] **Step 2: Run the deployment test and verify RED**

Run:

```bash
pytest -q tests/test_deployment_config.py
```

Expected: failure because Compose does not forward either variable.

- [ ] **Step 3: Add Compose environment forwarding**

Add directly after `PDF_TRANS_MINERU_URL` in `docker-compose.yml`:

```yaml
      PDF_TRANS_MINERU_BACKEND: ${PDF_TRANS_MINERU_BACKEND:-hybrid-engine}
      PDF_TRANS_MINERU_SERVER_URL: ${PDF_TRANS_MINERU_SERVER_URL:-}
```

- [ ] **Step 4: Run the deployment test and verify GREEN**

Run:

```bash
pytest -q tests/test_deployment_config.py
```

Expected: one passing test.

- [ ] **Step 5: Document both deployment modes**

Update the CLI section of `README.md` with:

```bash
python3 -m pdf_trans /path/to/document.pdf \
  --svr-url http://mineru.example:8000 \
  --mineru-backend hybrid-http-client \
  --mineru-server-url http://gpustack.example:8000
```

Add Web environment examples:

```dotenv
# 本地 Hybrid（默认）
PDF_TRANS_MINERU_BACKEND=hybrid-engine
PDF_TRANS_MINERU_SERVER_URL=

# GPUStack VLM 推理
PDF_TRANS_MINERU_BACKEND=hybrid-http-client
PDF_TRANS_MINERU_SERVER_URL=http://gpustack.example:8000
```

Document the MinerU service-side environment separately:

```dotenv
MINERU_MODEL_SOURCE=local
MINERU_VL_API_KEY=replace-with-gpustack-key
```

State explicitly:

- `PDF_TRANS_MINERU_URL` points to the complete MinerU `/tasks` service.
- `PDF_TRANS_MINERU_SERVER_URL` points to GPUStack and is forwarded to MinerU as `server_url`.
- `MINERU_MODEL_SOURCE=local` keeps local Pipeline models on the MinerU machine.
- `MINERU_VL_API_KEY` must be set on the MinerU service, not PDF Trans.
- Both supported modes use `effort=medium` and skip image/chart semantic analysis.

- [ ] **Step 6: Verify documentation terms and Compose rendering**

Run:

```bash
rg -n "PDF_TRANS_MINERU_BACKEND|PDF_TRANS_MINERU_SERVER_URL|MINERU_VL_API_KEY|MINERU_MODEL_SOURCE" README.md docker-compose.yml
```

Expected: every variable appears in the relevant examples and explanations.

If Docker Compose is installed, additionally run:

```bash
TRANSLATION_BASE_URL=http://translator \
TRANSLATION_API_KEY=test \
TRANSLATION_MODEL=test \
docker compose config --quiet
```

Expected: exit code `0`. If Docker Compose is unavailable, record that the automated text assertion is the available verification.

- [ ] **Step 7: Run the complete test suite**

Run:

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 8: Build the package**

Run:

```bash
python -m build
```

Expected: source distribution and wheel build successfully.

- [ ] **Step 9: Review the final diff**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; `.gitignore` remains the only unrelated user modification and is not staged.

- [ ] **Step 10: Commit Task 5**

```bash
git add tests/test_deployment_config.py docker-compose.yml README.md
git commit -m "docs: configure GPUStack-backed MinerU parsing"
```

---

## Final Verification Checklist

- [ ] `hybrid-engine` remains the zero-configuration default.
- [ ] Local requests omit `server_url`.
- [ ] Remote requests include normalized `server_url`.
- [ ] Both requests include `image_analysis=false` and `effort=medium`.
- [ ] Invalid backend combinations fail before a CLI task or Web worker starts.
- [ ] CLI explicit values override environment defaults.
- [ ] Web tasks use process-global configuration.
- [ ] Translate-only and resume paths do not require valid MinerU remote configuration.
- [ ] No code reads or logs `MINERU_VL_API_KEY`.
- [ ] Compose forwards only the PDF Trans backend and server URL.
- [ ] README explains the two service URLs and MinerU-side model/API-key configuration.
- [ ] Full tests and package build pass.
- [ ] The user's `.gitignore` modification remains untouched.
