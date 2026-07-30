# 表格翻译 JSON Schema 结构化输出设计

## 目标

表格翻译请求使用 OpenAI 兼容接口的 `response_format` JSON Schema
结构化输出，降低模型返回非 JSON 或错误结构的概率。普通段落翻译继续返回纯文本。
继续使用现有 `httpx` 请求方式，不新增 OpenAI SDK 或其他运行时依赖。

## 请求接口

`OpenAICompatibleTranslator.translate()` 增加可选的 `response_format` 参数。
请求仍发送至 `{TRANSLATION_BASE_URL}/chat/completions`，并继续使用 Bearer API Key。

普通段落调用不传 `response_format`，请求体保持为：

```json
{
  "model": "configured-model",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ]
}
```

表格调用显式传入 JSON Schema，请求体增加与 `model`、`messages` 同级的字段：

```json
{
  "model": "configured-model",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "table_translation",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {
          "translations": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "id": {
                  "type": "string",
                  "enum": ["table-text-0001"]
                },
                "text": {
                  "type": "string",
                  "minLength": 1
                }
              },
              "required": ["id", "text"],
              "additionalProperties": false
            }
          }
        },
        "required": ["translations"],
        "additionalProperties": false
      }
    }
  }
}
```

`id.enum` 根据当前表格的待翻译节点动态生成。JSON Schema 约束允许的 ID，
现有本地校验继续验证数量、重复 ID、ID 集合和空译文，确保每个节点恰好返回一次。

## 组件职责

- `translation_client.py`：接受可选 `response_format`，仅在调用方传入时把它加入
  HTTP JSON 请求体。
- `table_translation.py`：根据当前表格节点生成结构化输出约束。
- `translation.py`：表格翻译时传入该约束；普通段落调用方式不变。

表格结构化输出约束属于表格协议，不由通用 HTTP 客户端推断，避免通过提示词内容
或输入格式隐式识别请求类型。

## 错误处理

HTTP 状态码、响应 envelope 和空 content 仍由 `OpenAICompatibleTranslator` 处理。
模型 content 返回后，继续由现有 `_parse_response()` 严格解析和校验。结构化输出
请求失败时沿用现有重试策略；重试耗尽后仅将对应表格标记为 `failed`，不终止其他
内容的翻译。

## 测试

- 验证普通段落请求不包含 `response_format`。
- 验证通用客户端会把显式传入的 `response_format` 原样放入请求体。
- 验证表格生成的 JSON Schema 使用 `json_schema`、`strict: true`、
  `additionalProperties: false`，并包含当前节点 ID。
- 验证表格翻译实际调用时传入结构化输出约束。
- 运行全部测试，确认现有翻译、重试、断点和 Web 流程不受影响。

