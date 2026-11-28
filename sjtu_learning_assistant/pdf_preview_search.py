"""Extract bounded, read-only PDF search text from the already validated preview bytes."""
from io import BytesIO
from typing import Any


def pdf_preview_search(payload: bytes) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    remaining = 400_000
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(payload), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            return {"search_pages": [], "search_note": "PDF 已加密，暂无可搜索文字。"}
        total = len(reader.pages)
        truncated = total > 500
        for index in range(min(total, 500)):
            if remaining <= 0:
                truncated = True
                break
            raw = reader.pages[index].extract_text() or ""
            text = raw[:remaining]
            truncated = truncated or len(raw) > remaining
            remaining -= len(text)
            if text.strip():
                pages.append({"page": index + 1, "text": text})
        note = "仅搜索已提取的前 500 页或 40 万字。" if truncated else ""
        if not pages:
            note = "暂无可提取文字，请使用该文件的可读 Markdown 版本搜索。"
        return {"search_pages": pages, "search_note": note}
    except Exception:
        # Search extraction must not break the original PDF preview.
        return {"search_pages": [], "search_note": "PDF 文字提取未完成，原文件仍可预览。"}
