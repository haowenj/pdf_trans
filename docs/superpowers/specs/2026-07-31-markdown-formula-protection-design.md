# Markdown 公式保护设计

## 背景

Web 阅读器先使用 Markdown-It 把 `rendered.md` 转成 HTML，再在浏览器中调用 KaTeX auto-render。Markdown-It 不识别数学公式边界，因此可能把 `$...$` 内的 `_` 或 `*` 当作 Markdown 强调语法。两个相邻公式中的下划线还可能跨公式配对，生成 `<em>` 节点并拆散原本成对的 `$`，导致 KaTeX 后续错位解析。

任务 `b948a6d2-de9b-4f55-9e07-787635a2eee5` 已复现该问题：独立公式通过了严格 KaTeX 校验，但修复后的 `\mathrm{C}_{4}` 与 `\mathrm{C}_{7}` 被 CommonMark 解析成跨公式斜体。

## 目标

- Markdown-It 不解析公式内容中的 Markdown 标记。
- 保留现有 `$...$`、`$$...$$`、`\(...\)`、`\[...\]` 公式格式和前端 KaTeX 渲染方式。
- 公式恢复时保持 HTML 安全，不能绕过现有 nh3 清洗。
- 不改变翻译模型、公式修复规则或 `rendered.md` 的文件格式。
- 重新生成指定任务的 `rendered.md`，并验证 Web HTML 不再出现跨公式 `<em>`。

## 方案

在 `render_safe_markdown` 内增加一次局部保护与恢复：

1. Markdown-It 解析前扫描受支持的数学定界符。
2. 将完整公式跨度替换为带随机 nonce 的纯字母数字占位符。占位符不包含 Markdown 或 HTML 特殊字符。
3. 使用现有 CommonMark 配置生成 HTML。
4. 把占位符替换为经过 HTML 转义的原始公式文本。
5. 将恢复后的 HTML 交给现有 nh3 清洗。
6. 浏览器继续使用现有 KaTeX auto-render 处理公式。

随机 nonce 避免用户正文与占位符意外冲突。恢复内容必须先做 HTML 转义，确保公式中的 `<`、`>`、`&` 等字符仍是文本，不能成为 HTML。

未闭合的数学定界符不作保护，保持当前渲染行为；扫描器必须跳过转义的定界符，并优先识别 `$$...$$`，避免把块公式拆成两个行内公式。

## 错误处理

- 如果占位符在 Markdown 结果中缺失、重复或顺序异常，停止恢复并抛出明确错误，避免静默生成内容错乱的页面。
- 公式本身是否可被 KaTeX 解析仍由现有公式审计和 KaTeX 负责；本改动只保证 Markdown 不破坏公式边界。
- nh3 仍是最终 HTML 安全边界。

## 测试

新增 Web Markdown 回归测试，至少覆盖：

- 两个相邻的带下标公式不会产生跨公式 `<em>`。
- 四种现有数学定界符能够原样保留。
- 公式内的 HTML 特殊字符被转义。
- 普通 Markdown 斜体继续生效。
- 未闭合及已转义的美元符号保持原有文本行为。
- 既有危险 HTML 清洗与相对图片路径测试继续通过。

对指定任务重新调用现有 `render_content_list_file`，加载其 `translated_content_list.json` 与 `formula_audit.json`。生成后通过 `render_safe_markdown` 检查目标段落，确认公式之间没有跨界 `<em>`，同时保留全部公式定界符。
