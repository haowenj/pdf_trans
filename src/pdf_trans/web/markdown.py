from __future__ import annotations

import html
import secrets
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import nh3
from markdown_it import MarkdownIt

PARSER = MarkdownIt("commonmark", {"html": True})
_MATH_DELIMITERS = (
    ("$$", "$$"),
    (r"\[", r"\]"),
    (r"\(", r"\)"),
    ("$", "$"),
)
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


def _is_escaped(source: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and source[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _opening_at(source: str, position: int) -> tuple[str, str] | None:
    for opening, closing in _MATH_DELIMITERS:
        if not source.startswith(opening, position):
            continue
        if _is_escaped(source, position):
            continue
        if opening == "$" and source.startswith("$$", position):
            continue
        return opening, closing
    return None


def _find_closing(
    source: str,
    start: int,
    closing: str,
) -> int | None:
    position = start
    while True:
        position = source.find(closing, position)
        if position < 0:
            return None
        if _is_escaped(source, position):
            position += len(closing)
            continue
        if closing == "$" and source.startswith("$$", position):
            position += 2
            continue
        return position


def _protect_math(
    source: str,
) -> tuple[str, tuple[_ProtectedMath, ...]]:
    nonce = secrets.token_hex(16).upper()
    parts: list[str] = []
    records: list[_ProtectedMath] = []
    copied_until = 0
    position = 0

    while position < len(source):
        delimiters = _opening_at(source, position)
        if delimiters is None:
            position += 1
            continue
        opening, closing = delimiters
        closing_at = _find_closing(
            source,
            position + len(opening),
            closing,
        )
        if closing_at is None:
            position += len(opening)
            continue

        formula_end = closing_at + len(closing)
        placeholder = (
            f"PDFTRANSMATH{nonce}{len(records):08d}TOKEN"
        )
        parts.append(source[copied_until:position])
        parts.append(placeholder)
        records.append(
            _ProtectedMath(
                placeholder=placeholder,
                source=source[position:formula_end],
            )
        )
        copied_until = formula_end
        position = formula_end

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
