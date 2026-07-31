# 翻译模型思考开关设计

## 背景

当前翻译客户端只配置 OpenAI 兼容接口地址、API Key 和模型名称，请求体不包含
思考模式参数。新的翻译模型是通过 GPUStack 和 vLLM 本地部署的 Qwen3.6，
需要允许调用方按配置开启或关闭思考。

部分旧模型不支持思考参数，收到未知字段时会直接报错。因此，本次配置必须保留
“未配置”状态，不能在缺省时自动发送 `true` 或 `false`。

## 目标

- 新增可选环境变量 `TRANSLATION_ENABLE_THINKING`。
- 显式配置 `true` 或 `false` 时，控制 Qwen3.6 的思考模式。
- 未配置、空字符串或纯空格时，不向模型请求体添加任何思考参数。
- 普通文本翻译和结构化表格翻译使用相同配置。
- 非法配置在发起翻译请求前失败，并给出明确的中文错误。
- 保持现有模型、翻译重试、并发和失败回退行为不变。

## 不在本次范围内

- 不根据模型名称自动推断是否支持思考。
- 不增加任意 JSON 请求参数透传配置。
- 不支持 `reasoning_effort` 等多档推理强度。
- 不修改 GPUStack 或 vLLM 的部署参数。
- 不修改翻译提示词、重试逻辑、并发逻辑或翻译结果解析。

## 方案选择

### 采用方案：可选三态布尔配置

在 `OpenAICompatibleTranslator` 中增加：

```python
enable_thinking: bool | None = None
```

三种状态的含义如下：

| 配置状态 | 客户端值 | 请求行为 |
| --- | --- | --- |
| 未配置、空字符串、纯空格 | `None` | 不发送思考参数 |
| `true`，大小写不敏感 | `True` | 显式开启思考 |
| `false`，大小写不敏感 | `False` | 显式关闭思考 |

其他值，包括 `1`、`0`、`yes` 和拼写错误，均视为非法配置。

### 未采用的方案

- 通用 `TRANSLATION_EXTRA_BODY_JSON`：扩展性更强，但缺少类型约束，容易把错误推迟
  到模型调用阶段，也超出本次需求。
- 根据模型名称自动判断：模型别名、路由名称和推理后端可能变化，自动判断容易误发
  参数。
- 缺省为 `false`：会向不支持该参数的旧模型发送未知字段，破坏向后兼容。

## 请求体映射

Qwen3.6 的自部署用法通过聊天模板参数控制思考模式。项目直接使用 `httpx` 发送
JSON，而不是 OpenAI Python SDK，因此在 `/chat/completions` 请求体顶层加入：

```json
{
  "chat_template_kwargs": {
    "enable_thinking": false
  }
}
```

当客户端值为 `None` 时，整个 `chat_template_kwargs` 字段都不出现。项目不会发送
顶层 `enable_thinking`；该形式用于 Alibaba Cloud Model Studio，而不是本次确认的
GPUStack + vLLM 本地部署链路。

`response_format` 与 `chat_template_kwargs` 是两个独立的顶层字段。表格翻译传入
结构化输出格式时，两者可以同时存在，不得互相覆盖。

参考：

- [GPUStack OpenAI 兼容推理接口](https://docs.gpustack.ai/latest/integrations/inference-apis/)
- [Qwen3.6 自部署非思考模式](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/main/README.md)

## 配置流

1. `OpenAICompatibleTranslator.from_env()` 读取
   `TRANSLATION_ENABLE_THINKING`。
2. 专用解析函数把缺失或空值解析为 `None`，把 `true`/`false` 解析为布尔值。
3. 解析结果传入 `OpenAICompatibleTranslator` 构造函数并保存在实例上。
4. `translate()` 构造每次请求体；仅当值不是 `None` 时加入
   `chat_template_kwargs.enable_thinking`。
5. 普通文本和表格翻译继续共用 `translate()`，不在上层工作流重复处理配置。

## Docker 与文档

`docker-compose.yml` 透传可选变量：

```yaml
TRANSLATION_ENABLE_THINKING: ${TRANSLATION_ENABLE_THINKING:-}
```

空缺时容器内得到空字符串，客户端按 `None` 处理。README 增加以下示例：

```dotenv
TRANSLATION_ENABLE_THINKING=false
```

README 同时明确：

- 不配置时不发送思考参数；
- `true` 开启、`false` 关闭；
- 当前字段适用于 GPUStack + vLLM 的 Qwen3.6 自部署方式；
- 不支持该参数的模型应保持未配置。

项目中的本地 `.env` 含用户环境和密钥，本次不读取、不修改、不提交该文件。

## 校验与错误处理

- 环境变量解析允许首尾空格，且 `true`/`false` 大小写不敏感。
- 非空且不是 `true`/`false` 的值抛出 `TranslationConfigError`，错误信息包含
  `TRANSLATION_ENABLE_THINKING` 和允许值。
- 构造函数只接受 `bool` 或 `None`，避免 Python 中 `1 == True` 导致错误配置
  被接受。
- 配置错误发生在任何 HTTP 请求之前。
- 模型不支持显式参数而返回 HTTP 错误时，继续沿用现有
  `TranslationClientError`、重试和单段失败处理，不新增静默回退。静默删除参数会
  违背显式配置的含义。

## 测试设计

在 `tests/test_translation_client.py` 中使用现有 `httpx.MockTransport` 覆盖：

- 环境变量缺失、空字符串或纯空格时解析为 `None`；
- `true`、`TRUE`、带首尾空格的 `false` 等合法值；
- `1`、`0`、`yes`、拼写错误等非法值；
- 未配置时请求体字段集合与现有版本完全一致；
- 显式 `true` 和 `false` 时发送正确的嵌套布尔字段；
- `response_format` 与思考字段同时存在；
- 构造函数拒绝非布尔、非 `None` 的值。

在 `tests/test_deployment_config.py` 中验证 Compose 透传变量，在 README 相关测试
或部署配置测试中验证中文配置说明。最后运行完整测试套件，确认普通文本、表格、
CLI、Web 和工作流没有回归。

## 验收标准

- 未设置 `TRANSLATION_ENABLE_THINKING` 时，现有请求 JSON 完全不变。
- 设置为 `false` 时，GPUStack/vLLM 收到
  `chat_template_kwargs.enable_thinking=false`。
- 设置为 `true` 时，GPUStack/vLLM 收到
  `chat_template_kwargs.enable_thinking=true`。
- 合法配置同时适用于普通文本和结构化表格翻译。
- 非法配置在模型调用前以中文错误失败。
- Docker Compose 和 README 完整说明三态行为。
- 全量自动化测试通过。
