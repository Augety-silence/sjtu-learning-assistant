"""有界的执行轨迹预览；仅保存已返回的可读字段。"""
from collections.abc import Mapping


def build_tool_preview(result, clip, redact_body):
    def first(row, keys):
        return next((row[key] for key in keys if isinstance(row.get(key), str) and row[key].strip()), "")

    def item(row):
        title = first(row, ("title", "name", "course", "filename", "display_name"))
        if not title:
            return None
        detail = [first(row, ("course_name", "course", "source_label")), first(row, ("term", "occurred_at", "due_at"))]
        for key, label in (("topics", "主题"), ("documents", "资料")):
            rows = row.get(key)
            if isinstance(rows, list):
                names = [clip(first(x, ("title", "name", "filename")), 80) for x in rows[:4] if isinstance(x, Mapping)]
                if any(names):
                    detail.append(label + "：" + "、".join(x for x in names if x))
        excerpt = first(row, ("snippet", "preview", "excerpt", "body", "text"))
        if not excerpt and isinstance(row.get("passages"), list):
            excerpt = "\n".join(first(x, ("text", "snippet")) for x in row["passages"][:2] if isinstance(x, Mapping))
        return dict(title=clip(title, 180), detail=clip(" · ".join(x for x in detail if x and x != title), 480), excerpt=redact_body(excerpt, 480)[0])

    rows = result.get("items")
    candidates = rows if isinstance(rows, list) else [result]
    items = [value for row in candidates[:6] if isinstance(row, Mapping) and (value := item(row))]
    text, text_truncated = redact_body(first(result, ("text", "body")), 1600)
    if not items and not text:
        return None
    return dict(version=1, items=items, text=text, text_truncated=text_truncated,
                more_items=isinstance(rows, list) and len(rows) > 6)
