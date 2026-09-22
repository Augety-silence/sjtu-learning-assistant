"""Safe conversion of untrusted HTML-like content to plain text."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "div", "dl", "dt",
        "dd", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol",
        "p", "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead",
        "tr", "ul",
    }
)
_HIDDEN_TAGS = frozenset({"head", "script", "style", "template", "noscript"})
_HTML_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized in _HIDDEN_TAGS:
            self.hidden_depth += 1
        elif not self.hidden_depth and normalized in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.hidden_depth and tag.casefold() in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in _HIDDEN_TAGS:
            if self.hidden_depth:
                self.hidden_depth -= 1
        elif not self.hidden_depth and normalized in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def normalize_plain_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [re.sub(r"[\t\f\v ]+", " ", line).strip() for line in value.split("\n")]
    compact: list[str] = []
    for line in lines:
        if line or (compact and compact[-1]):
            compact.append(line)
    text = re.sub(r"\n{2,}", "\n", "\n".join(compact)).strip()
    return text or None


def html_to_plain_text(value: str | None) -> str | None:
    """Parse HTML without rendering or executing it and return visible text only."""
    if not isinstance(value, str) or not value:
        return None
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        # Never return the original markup on a parser failure.
        return normalize_plain_text("".join(parser.parts))
    return normalize_plain_text("".join(parser.parts))


def content_to_plain_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    if _HTML_TAG.search(value):
        return html_to_plain_text(value)
    return normalize_plain_text(value)
