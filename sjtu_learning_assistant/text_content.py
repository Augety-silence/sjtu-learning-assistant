"""Safe conversion and sanitization of untrusted HTML content."""

from __future__ import annotations

import hashlib
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

_ALLOWED_TAGS = frozenset(
    {
        "p", "br", "div", "ul", "ol", "li", "table", "thead", "tbody",
        "tr", "th", "td", "b", "strong", "i", "em", "u", "pre", "code",
        "blockquote", "a", "img",
    }
)
_VOID_TAGS = frozenset({"br", "img"})
_SUPPRESSED_TAGS = frozenset(
    {"script", "style", "iframe", "form", "object", "embed", "template", "noscript"}
)
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "div", "dl", "dt",
        "dd", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol",
        "p", "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead",
        "tr", "ul",
    }
)
_HIDDEN_TAGS = _SUPPRESSED_TAGS | {"head"}
_HTML_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


def _canvas_image_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != "oc.sjtu.edu.cn"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return None
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def _valid_resource_id(value: str) -> bool:
    return bool(value) and len(value) <= 512 and not any(
        ord(character) < 32 or ord(character) == 127 for character in value
    )


class _HTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.suppressed_tags: list[str] = []
        self.resource_urls: dict[str, str] = {}
        self.resource_ids: set[str] = set()

    def _safe_attributes(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> list[tuple[str, str]]:
        values = {name.casefold(): value for name, value in attrs if value is not None}
        if tag == "a":
            href = values.get("href")
            if href:
                parsed = urlsplit(href.strip())
                if parsed.scheme.casefold() == "https" and parsed.netloc:
                    return [("href", href.strip())]
            return []
        if tag == "img":
            src = values.get("src", "").strip()
            if src.casefold().startswith("cid:"):
                resource_id = src[4:].strip().strip("<>").strip()
                if _valid_resource_id(resource_id):
                    self.resource_ids.add(resource_id)
                    return [("data-resource-id", resource_id)]
            canvas_url = _canvas_image_url(src)
            if canvas_url is not None:
                resource_id = "canvas-" + hashlib.sha256(
                    canvas_url.encode("utf-8")
                ).hexdigest()
                self.resource_ids.add(resource_id)
                self.resource_urls[resource_id] = canvas_url
                return [("data-resource-id", resource_id)]
            resource_id = (values.get("data-resource-id") or "").strip()
            if _valid_resource_id(resource_id):
                self.resource_ids.add(resource_id)
                return [("data-resource-id", resource_id)]
        return []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if self.suppressed_tags:
            if tag in _SUPPRESSED_TAGS:
                self.suppressed_tags.append(tag)
            return
        if tag in _SUPPRESSED_TAGS:
            self.suppressed_tags.append(tag)
            return
        if tag not in _ALLOWED_TAGS:
            return
        safe_attributes = self._safe_attributes(tag, attrs)
        if tag == "img" and not safe_attributes:
            return
        attributes = "".join(
            f' {name}="{escape(value, quote=True)}"'
            for name, value in safe_attributes
        )
        self.parts.append(f"<{tag}{attributes}>")
        if tag not in _VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in _SUPPRESSED_TAGS:
            return
        self.handle_starttag(tag, attrs)
        if tag in self.open_tags:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self.suppressed_tags:
            if tag == self.suppressed_tags[-1]:
                self.suppressed_tags.pop()
            return
        if tag not in self.open_tags:
            return
        while self.open_tags:
            opened = self.open_tags.pop()
            self.parts.append(f"</{opened}>")
            if opened == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.suppressed_tags:
            self.parts.append(escape(data))

    def render(self) -> str:
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")
        return "".join(self.parts)


def _sanitize_html_details(
    value: str | None,
) -> tuple[str | None, dict[str, str], frozenset[str]]:
    if not isinstance(value, str) or not value:
        return None, {}, frozenset()
    parser = _HTMLSanitizer()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        pass
    result = parser.render().strip()
    return result or None, parser.resource_urls, frozenset(parser.resource_ids)


def sanitize_html(value: str | None) -> str | None:
    """Return strictly whitelisted HTML suitable for storage and later rendering."""
    return _sanitize_html_details(value)[0]


def sanitize_html_with_resources(
    value: str | None,
) -> tuple[str | None, dict[str, str]]:
    """Sanitize HTML and return only server-derived Canvas image resources."""
    html, resources, _resource_ids = _sanitize_html_details(value)
    return html, resources


def extract_html_resource_ids(value: str | None) -> frozenset[str]:
    """Re-sanitize HTML and return the resource IDs present in the safe output."""
    return _sanitize_html_details(value)[2]


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
        return normalize_plain_text("".join(parser.parts))
    return normalize_plain_text("".join(parser.parts))


def content_to_plain_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    if _HTML_TAG.search(value):
        return html_to_plain_text(value)
    return normalize_plain_text(value)
