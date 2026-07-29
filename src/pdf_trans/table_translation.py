from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser


_TARGET_TAGS = frozenset({"td", "th", "caption"})
_HIDDEN_TAGS = frozenset({"script", "style", "template"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_ENGLISH_RE = re.compile(r"[A-Za-z]")


class TableTranslationError(ValueError):
    """One table cannot be translated or safely reconstructed."""


@dataclass(frozen=True)
class TableTextNode:
    node_id: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class PreparedTableTranslation:
    original_html: str
    nodes: tuple[TableTextNode, ...]
    structure: tuple[tuple[object, ...], ...]

    def build_request(self) -> str:
        if not self.nodes:
            raise TableTranslationError("表格没有待翻译节点")
        return json.dumps(
            {
                "task": (
                    "Translate every item text from English to Simplified "
                    "Chinese. Return JSON only with the same IDs and shape "
                    '{"translations":[{"id":"...","text":"..."}]}.'
                ),
                "items": [
                    {"id": node.node_id, "text": node.text}
                    for node in self.nodes
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def apply_response(self, response: str) -> str:
        translations = _parse_response(
            response,
            tuple(node.node_id for node in self.nodes),
        )
        translated = self.original_html
        for node in reversed(self.nodes):
            translated = (
                translated[: node.start]
                + escape(translations[node.node_id], quote=False)
                + translated[node.end :]
            )
        translated_structure = _parse_html(translated, collect_nodes=False)
        if translated_structure.structure != self.structure:
            raise TableTranslationError("翻译后的 HTML 结构校验失败")
        return translated


@dataclass(frozen=True)
class _ParsedHTML:
    nodes: tuple[TableTextNode, ...]
    structure: tuple[tuple[object, ...], ...]


class _TableHTMLParser(HTMLParser):
    def __init__(self, source: str, *, collect_nodes: bool) -> None:
        super().__init__(convert_charrefs=False)
        self._collect_nodes = collect_nodes
        self._line_offsets = [0]
        for match in re.finditer(r"\n", source):
            self._line_offsets.append(match.end())
        self._stack: list[str] = []
        self._nodes: list[TableTextNode] = []
        self._structure: list[tuple[object, ...]] = []
        self._saw_table = False

    @property
    def result(self) -> _ParsedHTML:
        if self._stack:
            raise TableTranslationError(
                "HTML 标签未闭合：" + ", ".join(self._stack)
            )
        if not self._saw_table:
            raise TableTranslationError("HTML 中缺少 table 标签")
        return _ParsedHTML(tuple(self._nodes), tuple(self._structure))

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._structure.append(("start", tag, tuple(attrs)))
        if tag == "table":
            self._saw_table = True
        if tag not in _VOID_TAGS:
            self._stack.append(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._structure.append(("self", tag, tuple(attrs)))
        if tag == "table":
            self._saw_table = True

    def handle_endtag(self, tag: str) -> None:
        if not self._stack or self._stack[-1] != tag:
            expected = self._stack[-1] if self._stack else "无"
            raise TableTranslationError(
                f"HTML 结束标签不匹配：期望 {expected}，实际 {tag}"
            )
        self._stack.pop()
        self._structure.append(("end", tag))

    def handle_data(self, data: str) -> None:
        if not self._collect_nodes:
            return
        if "table" not in self._stack:
            return
        if not any(tag in _TARGET_TAGS for tag in self._stack):
            return
        if any(tag in _HIDDEN_TAGS for tag in self._stack):
            return
        stripped = data.strip()
        if not stripped or _ENGLISH_RE.search(stripped) is None:
            return
        data_start = self._absolute_position()
        content_start = data_start + len(data) - len(data.lstrip())
        content_end = data_start + len(data.rstrip())
        self._nodes.append(
            TableTextNode(
                node_id=f"table-text-{len(self._nodes) + 1:04d}",
                text=stripped,
                start=content_start,
                end=content_end,
            )
        )

    def _absolute_position(self) -> int:
        line, offset = self.getpos()
        return self._line_offsets[line - 1] + offset


def _parse_html(source: str, *, collect_nodes: bool) -> _ParsedHTML:
    if not isinstance(source, str) or not source.strip():
        raise TableTranslationError("HTML 必须是非空字符串")
    parser = _TableHTMLParser(source, collect_nodes=collect_nodes)
    try:
        parser.feed(source)
        parser.close()
        return parser.result
    except TableTranslationError:
        raise
    except Exception as exc:
        raise TableTranslationError(f"HTML 解析失败：{exc}") from exc


def _parse_response(
    response: str,
    expected_ids: tuple[str, ...],
) -> dict[str, str]:
    try:
        payload = json.loads(response)
    except (TypeError, json.JSONDecodeError) as exc:
        raise TableTranslationError("模型响应不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise TableTranslationError("模型响应必须是 JSON 对象")
    values = payload.get("translations")
    if not isinstance(values, list):
        raise TableTranslationError("模型响应 translations 必须是数组")
    translations: dict[str, str] = {}
    for value in values:
        if not isinstance(value, dict):
            raise TableTranslationError("模型翻译项必须是对象")
        node_id = value.get("id")
        text = value.get("text")
        if not isinstance(node_id, str) or not node_id:
            raise TableTranslationError("模型翻译项缺少有效 ID")
        if node_id in translations:
            raise TableTranslationError("模型结果存在重复 ID")
        if not isinstance(text, str) or not text.strip():
            raise TableTranslationError("模型译文不能为空")
        translations[node_id] = text.strip()

    if len(values) != len(expected_ids):
        raise TableTranslationError("模型结果数量不一致")
    if set(translations) != set(expected_ids):
        raise TableTranslationError("模型结果 ID 集合不一致")
    return translations


def prepare_table_translation(table_html: str) -> PreparedTableTranslation:
    parsed = _parse_html(table_html, collect_nodes=True)
    return PreparedTableTranslation(
        original_html=table_html,
        nodes=parsed.nodes,
        structure=parsed.structure,
    )
