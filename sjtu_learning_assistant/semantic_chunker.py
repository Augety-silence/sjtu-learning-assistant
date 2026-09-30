"""Deterministic, non-LLM semantic chunking for timestamped subtitles."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .course_learning_schemas import SubtitleChunk

_TERMINAL = re.compile(r"[。！？!?；;.!…][\"'”’）)】〕〉》]?$|\n\s*$")
_SENTENCE_PART = re.compile(r".*?(?:[。！？!?；;.!…]+(?:[\"'”’）)】〕〉》]+)?|$)")
_ENGLISH_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_+.#'-]{1,}")
_CJK_BIGRAM = re.compile(r"(?=([\u3400-\u9fff]{2}))")
_CJK_SPACE = re.compile(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])")
_TOPIC_MARKERS = (
    "接下来", "下面", "另一个", "下一节", "新主题", "再来看", "话题转到",
    "now let", "next", "another topic", "moving on", "turn to",
)
_STOP_WORDS = frozenset({
    "about", "after", "again", "also", "and", "are", "because", "before",
    "but", "for", "from", "have", "into", "not", "that", "the", "then",
    "this", "with", "我们", "这个", "那个", "就是", "然后", "所以", "可以",
    "一个", "这里", "现在", "大家", "什么", "怎么", "因为", "但是", "不是",
})


@dataclass(frozen=True)
class PreprocessedCue:
    """A cue with an immutable copy of its source text and conservative cleanup."""

    cue_id: str
    start_ms: int
    end_ms: int
    raw_text: str
    cleaned_text: str
    chapter_id: str | None
    start_index: int
    end_index: int
    source_cue_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Piece:
    cue_id: str
    start_ms: int
    end_ms: int
    text: str
    chapter_id: str | None
    start_index: int
    end_index: int
    source_cue_ids: tuple[str, ...]


class SemanticChunker:
    """Split cues using timing, punctuation, size, and a lexical topic proxy."""

    def __init__(
        self,
        *,
        target_chars: int = 1200,
        hard_limit_chars: int = 1800,
        min_chunk_chars: int | None = None,
        pause_ms: int = 1800,
        context_chars: int = 320,
    ) -> None:
        if type(target_chars) is not int or target_chars < 16:
            raise ValueError("target_chars must be an integer >= 16")
        if type(hard_limit_chars) is not int or hard_limit_chars < target_chars:
            raise ValueError("hard_limit_chars must be >= target_chars")
        if min_chunk_chars is None:
            min_chunk_chars = max(8, target_chars // 3)
        if type(min_chunk_chars) is not int or not 1 <= min_chunk_chars <= target_chars:
            raise ValueError("min_chunk_chars must be in [1, target_chars]")
        if type(pause_ms) is not int or pause_ms < 0:
            raise ValueError("pause_ms must be a non-negative integer")
        if type(context_chars) is not int or context_chars < 0:
            raise ValueError("context_chars must be a non-negative integer")
        self.target_chars = target_chars
        self.hard_limit_chars = hard_limit_chars
        self.min_chunk_chars = min_chunk_chars
        self.pause_ms = pause_ms
        self.context_chars = context_chars

    def chunk(
        self,
        cues: Sequence[object],
        *,
        course_id: str,
        video_id: str,
        chapter_id: str | None = None,
    ) -> list[SubtitleChunk]:
        _identifier(course_id, "course_id")
        _identifier(video_id, "video_id")
        if chapter_id is not None:
            _identifier(chapter_id, "chapter_id")
        normalized = preprocess_cues(cues, chapter_id=chapter_id)
        pieces = [piece for cue in normalized for piece in _split_cue(cue, self.hard_limit_chars)]
        if not pieces:
            return []

        groups: list[list[_Piece]] = []
        current: list[_Piece] = []
        current_size = 0
        for piece in pieces:
            addition = len(piece.text) + (1 if current else 0)
            if current and self._should_break(current, current_size, piece, addition):
                groups.append(current)
                current = []
                current_size = 0
                addition = len(piece.text)
            current.append(piece)
            current_size += addition
        if current:
            groups.append(current)

        texts = [_join_text(piece.text for piece in group) for group in groups]
        chunks: list[SubtitleChunk] = []
        for index, group in enumerate(groups):
            current_text = texts[index]
            previous_context = texts[index - 1][-self.context_chars :] if index and self.context_chars else ""
            next_context = texts[index + 1][: self.context_chars] if index + 1 < len(texts) and self.context_chars else ""
            cue_ids = tuple(dict.fromkeys(cue_id for piece in group for cue_id in piece.source_cue_ids))
            effective_chapter = _group_chapter(group, chapter_id)
            start_index = min(piece.start_index for piece in group)
            end_index = max(piece.end_index for piece in group)
            chunk_id = _chunk_id(
                course_id=course_id,
                video_id=video_id,
                chapter_id=effective_chapter,
                start_index=start_index,
                end_index=end_index,
                cue_ids=cue_ids,
                current_text=current_text,
            )
            chunks.append(SubtitleChunk(
                chunk_id=chunk_id,
                course_id=course_id,
                video_id=video_id,
                chapter_id=effective_chapter,
                start_index=start_index,
                end_index=end_index,
                previous_context=previous_context,
                current_text=current_text,
                next_context=next_context,
                cue_ids=cue_ids,
            ))
        return chunks

    def _should_break(
        self,
        current: list[_Piece],
        current_size: int,
        incoming: _Piece,
        addition: int,
    ) -> bool:
        previous = current[-1]
        if incoming.chapter_id != previous.chapter_id:
            return True
        if current_size + addition > self.hard_limit_chars:
            return True
        if current_size < self.min_chunk_chars:
            return False

        pause = max(0, incoming.start_ms - previous.end_ms)
        punctuation = bool(_TERMINAL.search(previous.text.rstrip()))
        topic_change = _topic_changed(current, incoming.text)
        near_target = current_size >= max(self.min_chunk_chars, int(self.target_chars * 0.72))
        reached_target = current_size >= self.target_chars

        if pause >= self.pause_ms:
            return True
        if topic_change and (near_target or pause >= self.pause_ms // 2):
            return True
        if punctuation and near_target:
            return True
        if reached_target:
            return True
        return False


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > 4096:
        raise ValueError(f"{name} is too long")
    return value


def _field(source: object, name: str, default: object = None) -> object:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _cue_text(source: object) -> str:
    for name in ("raw_text", "raw", "text", "cleaned_text"):
        value = _field(source, name)
        if value is not None:
            if type(value) is not str:
                raise ValueError(f"cue {name} must be a string")
            return value
    raise ValueError("cue is missing text")


def clean_subtitle_text(value: str) -> str:
    """Remove only whitespace noise; wording, including negation, is preserved."""

    if type(value) is not str:
        raise ValueError("subtitle text must be a string")
    text = unicodedata.normalize("NFC", value)
    text = " ".join(text.replace("\u200b", "").replace("\ufeff", "").split())
    text = _CJK_SPACE.sub("", text)
    return text.strip()


def preprocess_cues(
    cues: Sequence[object],
    *,
    chapter_id: str | None = None,
) -> list[PreprocessedCue]:
    """Copy and conservatively normalize cue data without mutating the input."""

    if isinstance(cues, (str, bytes)) or not isinstance(cues, Sequence):
        raise ValueError("cues must be a sequence")
    if chapter_id is not None:
        _identifier(chapter_id, "chapter_id")
    result: list[PreprocessedCue] = []
    previous_start = -1
    for index, source in enumerate(cues):
        raw = _cue_text(source)
        cleaned = clean_subtitle_text(raw)
        if not cleaned:
            continue
        start = _field(source, "start_ms")
        end = _field(source, "end_ms")
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise ValueError(f"cue[{index}] has invalid time range")
        if start < previous_start:
            raise ValueError("cues must be ordered by start_ms")
        previous_start = start
        cue_id_value = _field(source, "cue_id", "")
        if cue_id_value == "":
            cue_id_value = f"cue-{index + 1:06d}"
        cue_id = _identifier(cue_id_value, f"cue[{index}].cue_id")
        own_chapter = _field(source, "chapter_id", chapter_id)
        if own_chapter is None:
            own_chapter = chapter_id
        elif type(own_chapter) is not str or not own_chapter.strip():
            raise ValueError(f"cue[{index}].chapter_id must be null or a non-empty string")

        current = PreprocessedCue(
            cue_id=cue_id,
            start_ms=start,
            end_ms=end,
            raw_text=raw,
            cleaned_text=cleaned,
            chapter_id=own_chapter,
            start_index=index,
            end_index=index,
            source_cue_ids=(cue_id,),
        )
        if result and _is_obvious_duplicate(result[-1], current):
            previous = result[-1]
            result[-1] = replace(
                previous,
                end_ms=max(previous.end_ms, current.end_ms),
                end_index=index,
                source_cue_ids=previous.source_cue_ids + (cue_id,),
            )
        else:
            result.append(current)
    return result


def _is_obvious_duplicate(previous: PreprocessedCue, current: PreprocessedCue) -> bool:
    return (
        previous.chapter_id == current.chapter_id
        and previous.cleaned_text == current.cleaned_text
        and current.start_ms <= previous.end_ms + 500
    )


def _sentence_units(text: str) -> list[str]:
    units = [match.group(0).strip() for match in _SENTENCE_PART.finditer(text)]
    return [unit for unit in units if unit]


def _split_oversized_unit(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    words = text.split(" ")
    if len(words) > 1:
        parts: list[str] = []
        buffer = ""
        for word in words:
            if len(word) > limit:
                if buffer:
                    parts.append(buffer)
                    buffer = ""
                parts.extend(word[offset : offset + limit] for offset in range(0, len(word), limit))
            elif not buffer:
                buffer = word
            elif len(buffer) + 1 + len(word) <= limit:
                buffer += " " + word
            else:
                parts.append(buffer)
                buffer = word
        if buffer:
            parts.append(buffer)
        return parts
    # A punctuation-free CJK run has no semantic boundary available; this is
    # the final safety fallback required to uphold the hard character limit.
    return [text[offset : offset + limit] for offset in range(0, len(text), limit)]


def _split_cue(cue: PreprocessedCue, limit: int) -> list[_Piece]:
    units = _sentence_units(cue.cleaned_text)
    parts: list[str] = []
    buffer = ""
    for unit in units:
        for subunit in _split_oversized_unit(unit, limit):
            if not buffer:
                buffer = subunit
            elif len(buffer) + len(subunit) <= limit:
                buffer += subunit
            else:
                parts.append(buffer)
                buffer = subunit
    if buffer:
        parts.append(buffer)
    if not parts:
        return []
    duration = cue.end_ms - cue.start_ms
    return [
        _Piece(
            cue_id=cue.cue_id,
            start_ms=cue.start_ms + duration * index // len(parts),
            end_ms=cue.start_ms + duration * (index + 1) // len(parts),
            text=part,
            chapter_id=cue.chapter_id,
            start_index=cue.start_index,
            end_index=cue.end_index,
            source_cue_ids=cue.source_cue_ids,
        )
        for index, part in enumerate(parts)
    ]


def _keywords(text: str) -> set[str]:
    lowered = text.casefold()
    words = {word for word in _ENGLISH_WORD.findall(lowered) if word not in _STOP_WORDS}
    compact_cjk = re.sub(r"[^\u3400-\u9fff]", "", lowered)
    words.update(match.group(1) for match in _CJK_BIGRAM.finditer(compact_cjk))
    return words


def _topic_changed(current: list[_Piece], incoming: str) -> bool:
    lowered = incoming.casefold().lstrip()
    if any(lowered.startswith(marker) for marker in _TOPIC_MARKERS):
        return True
    left = _keywords(_join_text(piece.text for piece in current[-3:]))
    right = _keywords(incoming)
    if len(left) < 2 or len(right) < 2:
        return False
    similarity = len(left & right) / len(left | right)
    return similarity < 0.08


def _join_text(values: Sequence[str] | Any) -> str:
    return "\n".join(values)


def _group_chapter(group: list[_Piece], fallback: str | None) -> str | None:
    chapters = {piece.chapter_id for piece in group}
    if len(chapters) == 1:
        return next(iter(chapters))
    return fallback


def _chunk_id(**payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def chunk_transcript(
    cues: Sequence[object],
    *,
    course_id: str,
    video_id: str,
    chapter_id: str | None = None,
    target_chars: int = 1200,
    hard_limit_chars: int = 1800,
    min_chunk_chars: int | None = None,
    pause_ms: int = 1800,
    context_chars: int = 320,
) -> list[SubtitleChunk]:
    """Functional entry point for :class:`SemanticChunker`."""

    return SemanticChunker(
        target_chars=target_chars,
        hard_limit_chars=hard_limit_chars,
        min_chunk_chars=min_chunk_chars,
        pause_ms=pause_ms,
        context_chars=context_chars,
    ).chunk(cues, course_id=course_id, video_id=video_id, chapter_id=chapter_id)


semantic_chunk = chunk_transcript
semantic_chunk_cues = chunk_transcript
chunk_subtitle_cues = chunk_transcript
chunk_cues = chunk_transcript


__all__ = [
    "PreprocessedCue",
    "SemanticChunker",
    "chunk_cues",
    "chunk_subtitle_cues",
    "chunk_transcript",
    "clean_subtitle_text",
    "preprocess_cues",
    "semantic_chunk",
    "semantic_chunk_cues",
]
