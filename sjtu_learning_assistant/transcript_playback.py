"""Pure, evidence-bounded projections of persisted learning chunks onto video time."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from sjtu_learning_assistant.transcript_pipeline import Cue


def _cue_map(cues: Sequence[Cue | Mapping[str, Any]]) -> dict[str, Cue]:
    result = {}
    for item in cues:
        cue = item if isinstance(item, Cue) else Cue(item["start_ms"], item["end_ms"], item["text"], item["cue_id"])
        if cue.cue_id and 0 <= cue.start_ms < cue.end_ms:
            result[cue.cue_id] = cue
    return result


def build_learning_segments(cues, mapped_chunks, summary=None, *, provisional=True):
    """Expose finished chunks immediately; use source cue times, never model times."""
    by_id = _cue_map(cues)
    chunks = list(mapped_chunks.values()) if isinstance(mapped_chunks, Mapping) else list(mapped_chunks)
    segments, ranges, seen = [], [], set()

    def add(row, title, content, kind, source):
        evidence = row.get("evidence", [])
        refs = [by_id.get(e.get("cue_id")) for e in evidence if isinstance(e, Mapping)]
        refs = [c for c in refs if c is not None]
        if not refs or not isinstance(content, str) or not content.strip():
            return False
        start, end = min(c.start_ms for c in refs), max(c.end_ms for c in refs)
        key = (start, end, title, content, kind)
        if key in seen:
            return True
        seen.add(key)
        digest = hashlib.sha256(repr(key).encode()).hexdigest()[:20]
        segments.append(dict(id=digest, start_ms=start, end_ms=end, title=str(title)[:200],
                             content=content[:4000], kind=kind, source=source, provisional=bool(provisional)))
        return True

    def project(value):
        count = len(segments)
        for row in value.get("knowledge_points", []):
            add(row, row.get("concept", "知识点"), row.get("statement", ""), row.get("kind", "knowledge"), "根据字幕内容归纳")
        for row in value.get("emphasized_points", []):
            add(row, "课堂强调", row.get("text", ""), "emphasis", "课堂明确讲述")
        for row in value.get("classroom_examples", []):
            add(row, "课堂案例", row.get("example", ""), "example", "课堂明确讲述")
        for row in value.get("practice_items", []):
            answers = row.get("answer_key", []) or row.get("rubric", [])
            content = str(row.get("prompt", "")) + ("\n答案要点：" + "；".join(answers) if answers else "")
            add(row, "同步练习", content, "practice", "根据课堂内容归纳")
        return len(segments) > count

    # A final validated summary takes precedence over provisional chunk extracts.
    has_final_learning = isinstance(summary, Mapping) and project(summary)
    for chunk in chunks:
        completed = [by_id.get(row.get("cue_id")) for row in chunk.get("cleaned_transcript", []) if isinstance(row, Mapping)]
        completed = [c for c in completed if c is not None]
        ranges.extend([c.start_ms, c.end_ms] for c in completed)
        if not has_final_learning and not project(chunk) and completed:
            add({"evidence": [{"cue_id": c.cue_id} for c in completed]},
                "字幕摘录", "\n".join(c.text for c in completed), "transcript", "字幕原文")
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return dict(segments=sorted(segments, key=lambda s: (s["start_ms"], s["end_ms"], s["id"])), available_ranges=merged)


def corrected_source_cues(cues, semantic_chunks, corrections):
    """Map accepted single-cue changes by exact offsets; ambiguous alignment fails closed."""
    by_id = _cue_map(cues)
    chunks = {c.get("chunk_id"): c for c in semantic_chunks if isinstance(c, Mapping)}
    changes = {}
    for correction in corrections:
        if not correction.get("changes"):
            continue
        chunk = chunks.get(correction.get("chunk_id"))
        if not chunk:
            return None
        ids = chunk.get("cue_ids", [])
        if not ids or any(i not in by_id for i in ids):
            return None
        source = "\n".join(by_id[i].text for i in ids)
        if source != chunk.get("current_text"):
            return None
        spans, cursor = [], 0
        for i in ids:
            spans.append((cursor, cursor + len(by_id[i].text), i))
            cursor += len(by_id[i].text) + 1
        for change in correction["changes"]:
            start, end = change.get("start"), change.get("end")
            replacement = change.get("corrected")
            if type(start) is not int or type(end) is not int or not isinstance(replacement, str):
                return None
            if not 0 <= start < end <= len(source) or source[start:end] != change.get("original"):
                return None
            matches = [(a, i) for a, b, i in spans if a <= start < end <= b]
            if len(matches) != 1 or change.get("cue_ids") != [matches[0][1]]:
                return None
            offset, cue_id = matches[0]
            changes.setdefault(cue_id, []).append((start - offset, end - offset, replacement))
    updated = []
    for cue in by_id.values():
        text, last = cue.text, len(cue.text) + 1
        for start, end, replacement in sorted(changes.get(cue.cue_id, []), reverse=True):
            if end > last:
                return None
            text = text[:start] + replacement + text[end:]
            last = start
        if not text.strip():
            return None
        updated.append(Cue(cue.start_ms, cue.end_ms, text, cue.cue_id))
    return updated


def cues_to_vtt(cues):
    def timestamp(ms):
        seconds, milliseconds = divmod(ms, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"

    def payload(value):
        # WebVTT cue identifiers/payloads must not contain "-->" or line breaks;
        # model replacements could otherwise end a cue early or forge timings.
        return " ".join(str(value).split()).replace("-->", "→")
    return "WEBVTT\n\n" + "\n\n".join(
        f"{payload(cue.cue_id)}\n{timestamp(cue.start_ms)} --> {timestamp(cue.end_ms)}\n{payload(cue.text)}"
        for cue in _cue_map(cues).values()
    ) + "\n"
