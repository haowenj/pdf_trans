# 翻译模型思考开关实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 `httpx` OpenAI 兼容翻译客户端增加可选的三态思考配置，并按 GPUStack/vLLM 的 Qwen3.6 请求格式发送开关。

**Architecture:** 配置只在 `OpenAICompatibleTranslator` 内解析和保存，值为 `bool | None`。`translate()` 继续发送普通 HTTP JSON；仅在值不是 `None` 时加入顶层 `chat_template_kwargs.enable_thinking`，使普通文本与结构化表格翻译自动共享行为。

**Tech Stack:** Python 3.11+、httpx、pytest、Docker Compose、Markdown。

## Global Constraints

- 环境变量名称必须是 `TRANSLATION_ENABLE_THINKING`。
- 未配置、空字符串或纯空格必须解析为 `None`，并且请求体完全不出现 `chat_template_kwargs`。
- 仅接受大小写不敏感、允许首尾空格的 `true` 和 `false`。
- 显式布尔值必须映射为请求体顶层的 `{"chat_template_kwargs": {"enable_thinking": <bool>}}`。
- 不发送 Alibaba Cloud Model Studio 使用的顶层 `enable_thinking`。
- 不增加 OpenAI SDK、通用额外 JSON、模型名称推断或 `reasoning_effort`。
- 不修改本地 `.env`、翻译提示词、重试、并发和结果解析逻辑。
- 所有生产代码变更必须遵循测试先行：先看到目标测试因缺少行为而失败，再写最小实现。

---

### Task 1: 解析并保存可选三态配置

**Files:**
- Modify: `tests/test_translation_client.py:16-155`
- Modify: `src/pdf_trans/translation_client.py:28-140`

**Interfaces:**
- Consumes: 环境变量 `TRANSLATION_ENABLE_THINKING`，其原始类型为 `str` 或缺失。
- Produces: `_parse_optional_boolean(name: str, value: str) -> bool | None`；`OpenAICompatibleTranslator.enable_thinking: bool | None`；构造函数参数 `enable_thinking: bool | None = None`。

- [ ] **Step 1: 为缺省、合法值、非法值和构造函数类型约束编写失败测试**

先在 `tests/test_translation_client.py` 中隔离调用测试的外部环境，确保开发机上即使
设置过该可选变量也不会污染不相关用例：

```python
@pytest.fixture(autouse=True)
def clear_translation_thinking_environment(monkeypatch):
    monkeypatch.delenv("TRANSLATION_ENABLE_THINKING", raising=False)
```

然后扩充默认配置测试，并新增：

```python
def test_from_env_leaves_thinking_unconfigured_by_default(monkeypatch):
    monkeypatch.setenv(
        "TRANSLATION_BASE_URL",
        "http://translate.example/v1",
    )
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")
    monkeypatch.delenv("TRANSLATION_ENABLE_THINKING", raising=False)

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.enable_thinking is None
    translator.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", None),
        ("   ", None),
        ("true", True),
        ("TRUE", True),
        (" false ", False),
        ("FaLsE", False),
    ],
)
def test_from_env_parses_optional_thinking_configuration(
    monkeypatch,
    value,
    expected,
):
    monkeypatch.setenv(
        "TRANSLATION_BASE_URL",
        "http://translate.example/v1",
    )
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")
    monkeypatch.setenv("TRANSLATION_ENABLE_THINKING", value)

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.enable_thinking is expected
    translator.close()


@pytest.mark.parametrize(
    "value",
    ["1", "0", "yes", "no", "enabled", "tru"],
)
def test_from_env_rejects_invalid_thinking_configuration(
    monkeypatch,
    value,
):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "configured")
    monkeypatch.setenv("TRANSLATION_API_KEY", "configured")
    monkeypatch.setenv("TRANSLATION_MODEL", "configured")
    monkeypatch.setenv("TRANSLATION_ENABLE_THINKING", value)

    with pytest.raises(
        TranslationConfigError,
        match="TRANSLATION_ENABLE_THINKING",
    ):
        OpenAICompatibleTranslator.from_env()


@pytest.mark.parametrize("value", [0, 1, "false", object()])
def test_constructor_rejects_non_boolean_thinking_configuration(value):
    with pytest.raises(
        TranslationConfigError,
        match="TRANSLATION_ENABLE_THINKING",
    ):
        OpenAICompatibleTranslator(
            base_url="http://translate.example/v1",
            api_key="secret",
            model="paper-model",
            enable_thinking=value,
        )
```

- [ ] **Step 2: 运行聚焦测试，确认因配置尚未实现而失败**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_translation_client.py::test_from_env_leaves_thinking_unconfigured_by_default \
  tests/test_translation_client.py::test_from_env_parses_optional_thinking_configuration \
  tests/test_translation_client.py::test_from_env_rejects_invalid_thinking_configuration \
  tests/test_translation_client.py::test_constructor_rejects_non_boolean_thinking_configuration \
  -q
```

Expected: FAIL，错误表现为缺少 `enable_thinking` 属性或构造函数不接受该参数。

- [ ] **Step 3: 实现最小配置解析和实例状态**

在 `src/pdf_trans/translation_client.py` 的解析函数区域加入：

```python
def _parse_optional_boolean(
    name: str,
    value: str,
) -> bool | None:
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise TranslationConfigError(
        f"{name} 只能是 true、false 或不配置"
    )
```

将构造函数签名扩展为：

```python
def __init__(
    self,
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float = DEFAULT_TRANSLATION_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_TRANSLATION_MAX_RETRIES,
    concurrency: int = DEFAULT_TRANSLATION_CONCURRENCY,
    enable_thinking: bool | None = None,
    http_client: httpx.Client | None = None,
) -> None:
```

在现有配置校验后加入：

```python
if enable_thinking is not None and type(enable_thinking) is not bool:
    raise TranslationConfigError(
        "TRANSLATION_ENABLE_THINKING 只能是 true、false 或不配置"
    )
```

保存公开只读状态：

```python
self.enable_thinking = enable_thinking
```

在 `from_env()` 中解析并传递：

```python
enable_thinking = _parse_optional_boolean(
    "TRANSLATION_ENABLE_THINKING",
    os.environ.get("TRANSLATION_ENABLE_THINKING", ""),
)
```

```python
return cls(
    base_url=values["TRANSLATION_BASE_URL"],
    api_key=values["TRANSLATION_API_KEY"],
    model=values["TRANSLATION_MODEL"],
    timeout_seconds=timeout,
    max_retries=max_retries,
    concurrency=concurrency,
    enable_thinking=enable_thinking,
)
```

- [ ] **Step 4: 运行配置测试和整个翻译客户端测试**

Run:

```bash
.venv/bin/python -m pytest tests/test_translation_client.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交三态配置解析**

```bash
git add src/pdf_trans/translation_client.py tests/test_translation_client.py
git commit -m "feat: parse optional translation thinking config"
```

---

### Task 2: 条件构造 GPUStack/vLLM 请求参数

**Files:**
- Modify: `tests/test_translation_client.py:157-245`
- Modify: `src/pdf_trans/translation_client.py:152-180`

**Interfaces:**
- Consumes: Task 1 产生的 `OpenAICompatibleTranslator.enable_thinking: bool | None`。
- Produces: `translate()` 在显式配置时发送顶层 `chat_template_kwargs.enable_thinking`，在 `None` 时保持原始 JSON。

- [ ] **Step 1: 编写显式开关和结构化输出共存的失败测试**

保留现有 `test_translate_sends_compatible_request_and_returns_only_content`
对默认请求体的完整相等断言，并补充：

```python
@pytest.mark.parametrize("enabled", [True, False])
def test_translate_sends_configured_vllm_thinking_switch(enabled):
    seen = {}

    def handler(request):
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "译文"}}]},
        )

    translator = OpenAICompatibleTranslator(
        base_url="http://translate.example/v1",
        api_key="secret",
        model="qwen3.6-plus",
        enable_thinking=enabled,
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler)
        ),
    )

    assert translator.translate("Source") == "译文"
    assert seen["payload"]["chat_template_kwargs"] == {
        "enable_thinking": enabled,
    }
    assert "enable_thinking" not in seen["payload"]
```

把现有 `test_translate_includes_explicit_response_format` 中的构造函数改为显式
`enable_thinking=False`，并把最终断言改为：

```python
assert seen["payload"]["response_format"] == response_format
assert seen["payload"]["chat_template_kwargs"] == {
    "enable_thinking": False,
}
assert set(seen["payload"]) == {
    "model",
    "messages",
    "response_format",
    "chat_template_kwargs",
}
```

在默认请求测试中补充：

```python
assert "chat_template_kwargs" not in seen["payload"]
```

- [ ] **Step 2: 运行请求体测试，确认显式配置尚未进入 JSON**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_translation_client.py::test_translate_sends_compatible_request_and_returns_only_content \
  tests/test_translation_client.py::test_translate_sends_configured_vllm_thinking_switch \
  tests/test_translation_client.py::test_translate_includes_explicit_response_format \
  -q
```

Expected: FAIL；默认未配置用例继续通过，显式开关用例因缺少
`chat_template_kwargs` 失败。

- [ ] **Step 3: 在普通 HTTP JSON 中条件加入聊天模板参数**

在 `src/pdf_trans/translation_client.py` 的 `translate()` 中，初始化 `payload`
之后、处理 `response_format` 之前加入：

```python
if self.enable_thinking is not None:
    payload["chat_template_kwargs"] = {
        "enable_thinking": self.enable_thinking,
    }
```

不要添加 `extra_body` 包装层，也不要添加顶层 `enable_thinking`。`httpx.post(...,
json=payload)` 保持不变。

- [ ] **Step 4: 运行请求体测试和翻译相关回归测试**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_translation_client.py \
  tests/test_translation.py \
  tests/test_table_translation.py \
  -q
```

Expected: PASS。

- [ ] **Step 5: 提交请求体开关**

```bash
git add src/pdf_trans/translation_client.py tests/test_translation_client.py
git commit -m "feat: send configured vllm thinking switch"
```

---

### Task 3: 透传 Docker 配置并补充中文文档

**Files:**
- Modify: `tests/test_deployment_config.py:1-20`
- Modify: `docker-compose.yml:19-25`
- Modify: `README.md:122-211`

**Interfaces:**
- Consumes: Task 1 定义的环境变量 `TRANSLATION_ENABLE_THINKING`。
- Produces: Docker Compose 可选透传；README 中的 GPUStack/vLLM 配置和三态行为说明。

- [ ] **Step 1: 编写部署配置和文档的失败测试**

在 `tests/test_deployment_config.py` 中新增：

```python
def test_compose_forwards_optional_translation_thinking_configuration():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert (
        "TRANSLATION_ENABLE_THINKING: "
        "${TRANSLATION_ENABLE_THINKING:-}"
    ) in compose


def test_readme_documents_optional_translation_thinking_configuration():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "TRANSLATION_ENABLE_THINKING=false" in readme
    assert "chat_template_kwargs" in readme
    assert "未配置时不发送" in readme
    assert "GPUStack" in readme
    assert "vLLM" in readme
```

- [ ] **Step 2: 运行部署测试，确认配置和文档尚未出现**

Run:

```bash
.venv/bin/python -m pytest tests/test_deployment_config.py -q
```

Expected: FAIL，缺少 Compose 变量和 README 说明。

- [ ] **Step 3: 更新 Docker Compose**

在 `docker-compose.yml` 的翻译变量区域加入：

```yaml
      TRANSLATION_ENABLE_THINKING: ${TRANSLATION_ENABLE_THINKING:-}
```

- [ ] **Step 4: 更新 README 的 Docker 示例、shell 示例和说明**

在 Docker `.env` 示例中加入：

```dotenv
TRANSLATION_ENABLE_THINKING=false
```

在 shell 配置示例中加入：

```bash
export TRANSLATION_ENABLE_THINKING="false"
```

在翻译变量说明中加入以下完整语义：

```markdown
- `TRANSLATION_ENABLE_THINKING` 为可选的 GPUStack/vLLM Qwen3.6 思考开关：
  `true` 开启，`false` 关闭；未配置或为空时不发送
  `chat_template_kwargs`，用于兼容不支持该参数的模型；
```

再补充请求格式说明：

```markdown
项目仍通过普通 HTTP 请求调用 `/chat/completions`。显式配置思考开关时，请求体
顶层增加 `chat_template_kwargs.enable_thinking`；未配置时请求体保持原样。
```

- [ ] **Step 5: 运行部署测试、差异检查和全量测试**

Run:

```bash
.venv/bin/python -m pytest tests/test_deployment_config.py -q
git diff --check
.venv/bin/python -m pytest -q
```

Expected: 部署测试 PASS，`git diff --check` 无输出，全量测试无失败。

- [ ] **Step 6: 提交部署和文档变更**

```bash
git add docker-compose.yml README.md tests/test_deployment_config.py
git commit -m "docs: configure translation thinking mode"
```

---

## 最终验证

- [ ] **Step 1: 检查分支和工作区**

Run:

```bash
git branch --show-current
git status --short
git diff --check
```

Expected: 当前分支为 `codex/translation-thinking-config`，工作区为空，
`git diff --check` 无输出。

- [ ] **Step 2: 运行完整测试套件**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: 全部测试通过；如果只出现项目既有的 Starlette/httpx 弃用警告，记录但
不把它误报为本次回归。

- [ ] **Step 3: 核对最终提交范围**

Run:

```bash
git diff --stat main...HEAD
git log --oneline main..HEAD
```

Expected: 仅包含设计、计划、翻译客户端、对应测试、Docker Compose 和 README
变更，不包含 `.env` 或其他用户配置文件。
