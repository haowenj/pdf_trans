from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import nh3
from markdown_it import MarkdownIt

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


def render_safe_markdown(source: str, *, asset_base_url: str) -> str:
    rendered = PARSER.render(source)

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
