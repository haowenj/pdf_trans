from __future__ import annotations

import html
import secrets
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import nh3
from markdown_it import MarkdownIt

from pdf_trans.formula_scanner import scan_formula_spans

PARSER = MarkdownIt("commonmark", {"html": True})
ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "td": {"rowspan", "colspan"},
    "th": {"rowspan", "colspan"},
}


@dataclass(frozen=True)
class _ProtectedMath:
    placeholder: str
    source: str


def _protect_math(
    source: str,
) -> tuple[str, tuple[_ProtectedMath, ...]]:
    nonce = secrets.token_hex(16).upper()
    parts: list[str] = []
    records: list[_ProtectedMath] = []
    copied_until = 0

    for span in scan_formula_spans(source):
        placeholder = (
            f"PDFTRANSMATH{nonce}{len(records):08d}TOKEN"
        )
        parts.append(source[copied_until : span.start])
        parts.append(placeholder)
        records.append(
            _ProtectedMath(
                placeholder=placeholder,
                source=span.raw_formula,
            )
        )
        copied_until = span.end

    parts.append(source[copied_until:])
    return "".join(parts), tuple(records)


def _restore_math(
    rendered: str,
    records: tuple[_ProtectedMath, ...],
) -> str:
    positions: list[int] = []
    for record in records:
        if rendered.count(record.placeholder) != 1:
            raise ValueError("Markdown 公式占位符数量异常")
        positions.append(rendered.index(record.placeholder))
    if positions != sorted(positions):
        raise ValueError("Markdown 公式占位符顺序异常")

    restored = rendered
    for record in records:
        restored = restored.replace(
            record.placeholder,
            html.escape(record.source, quote=True),
            1,
        )
    return restored


def render_safe_markdown(source: str, *, asset_base_url: str) -> str:
    protected, math_records = _protect_math(source)
    rendered = PARSER.render(protected)
    rendered = _restore_math(rendered, math_records)

    def rewrite_relative(url: str) -> str | None:
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            return url
        decoded = unquote(parsed.path)
        path = PurePosixPath(decoded)
        if path.is_absolute() or ".." in path.parts:
            return None
        safe_path = "/".join(quote(part, safe="") for part in path.parts)
        if not safe_path:
            return None
        return f"{asset_base_url.rstrip('/')}/{safe_path}"

    return nh3.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        clean_content_tags={"script", "style", "iframe", "form", "object"},
        link_rel="noopener noreferrer",
        url_schemes={"http", "https"},
        url_relative=rewrite_relative,
    )
