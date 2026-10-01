"""受限、无执行能力的课程媒体与结构化文件预览后端。

本模块只接收内存中的输入并返回 DTO/字节，不接受调用方指定的输出路径。
归档只会按需读取受限条目，任何宏、公式、Notebook 代码或脚本都不会执行。
"""

from __future__ import annotations

import html
import importlib.util
import io
import json
import math
import mimetypes
import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import unicodedata
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


class MediaFeatureError(RuntimeError):
    """媒体输入不安全、无效或超过资源限制。"""


class UnsafeArchiveError(MediaFeatureError):
    """归档元数据或成员路径不安全。"""


@dataclass(frozen=True)
class PreviewLimits:
    max_file_size: int = 64 * 1024 * 1024
    max_archive_entries: int = 256
    max_uncompressed_size: int = 128 * 1024 * 1024
    max_entry_size: int = 32 * 1024 * 1024
    max_compression_ratio: int = 200
    max_path_depth: int = 12
    max_path_length: int = 1024
    max_xml_size: int = 8 * 1024 * 1024
    max_xml_elements: int = 100_000
    max_xml_depth: int = 128
    max_notebook_size: int = 16 * 1024 * 1024
    max_text_chars: int = 200_000
    max_notebook_cells: int = 500
    max_sheet_rows: int = 200
    max_sheet_columns: int = 50

    def __post_init__(self) -> None:
        for field in fields(self):
            if (
                type(getattr(self, field.name)) is not int
                or getattr(self, field.name) <= 0
            ):
                raise ValueError(f"{field.name} 必须为正整数")


DEFAULT_LIMITS = PreviewLimits()

_VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".m4v",
        ".mov",
        ".webm",
        ".mkv",
        ".avi",
        ".wmv",
        ".mpeg",
        ".mpg",
        ".ts",
        ".m3u8",
    }
)
_AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".weba"}
)
_SUBTITLE_EXTENSIONS = frozenset({".srt", ".vtt", ".ass", ".ssa", ".ttml", ".dfxp"})
_MACRO_EXTENSIONS = frozenset(
    {".docm", ".dotm", ".xlsm", ".xltm", ".xlam", ".pptm", ".potm", ".ppam", ".ppsm"}
)
_PREVIEWABLE = frozenset({"zip", "docx", "pptx", "xlsx", "ipynb"})
_MIME_BY_KIND = {
    "zip": "application/zip",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ipynb": "application/x-ipynb+json",
    "pdf": "application/pdf",
    "video": "video/*",
    "audio": "audio/*",
    "subtitle": "text/vtt",
    "text": "text/plain",
    "image": "image/*",
    "binary": "application/octet-stream",
    "office_macro": "application/octet-stream",
}
_ACTIVE_ARCHIVE_NAMES = re.compile(
    r"(?:^|/)(?:vbaproject\.bin|macros?/|embeddings?/|activeX/|customUI/)",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NATURAL_RE = re.compile(r"(\d+)")
_LANGUAGE_SUFFIX_RE = re.compile(
    r"(?:[._\- ](?:zh(?:[-_]?(?:cn|hans|tw|hant))?|en(?:[-_](?:us|gb))?|chs|cht|eng|中文|英文))$",
    re.IGNORECASE,
)


def _clean_name(value: object, default: str = "文件") -> str:
    if not isinstance(value, str):
        return default
    leaf = value.replace("\\", "/").split("/")[-1]
    leaf = _CONTROL_RE.sub("", leaf)
    leaf = " ".join(leaf.split()).strip(" .")
    return leaf[:255] or default


def _clean_text(value: object, limit: int) -> str:
    if isinstance(value, list):
        value = "".join(part for part in value if isinstance(part, str))
    if not isinstance(value, str):
        return ""
    return _CONTROL_RE.sub("", value)[:limit]


def _plain_preview_text(value: object, limit: int) -> str:
    # Escaping markup makes the contract safe even if a renderer accidentally uses innerHTML.
    return html.escape(_clean_text(value, limit), quote=False)


def _mime(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.split(";", 1)[0].strip().casefold()[:255]


def _as_bytes(data: bytes | bytearray | memoryview, limit: int) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise MediaFeatureError("文件内容必须是字节数据，不能传入文件路径。")
    payload = bytes(data)
    if len(payload) > limit:
        raise MediaFeatureError(f"文件超过 {limit} 字节限制。")
    return payload


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in _NATURAL_RE.split(value)
    )


def _preflight_zip(payload: bytes, limits: PreviewLimits) -> None:
    """在 zipfile 分配 ZipInfo 列表前先读取 EOCD 中的条目数量。"""
    offset = payload.rfind(b"PK\x05\x06", max(0, len(payload) - 65_557))
    if offset < 0 or offset + 22 > len(payload):
        raise MediaFeatureError("ZIP 文件缺少有效的中央目录。")
    try:
        (
            _,
            disk,
            directory_disk,
            disk_entries,
            total_entries,
            directory_size,
            directory_offset,
            comment_size,
        ) = struct.unpack_from("<4s4H2IH", payload, offset)
    except struct.error as exc:
        raise MediaFeatureError("ZIP 中央目录结构无效。") from exc
    if offset + 22 + comment_size != len(payload):
        raise MediaFeatureError("ZIP 尾部结构或注释长度无效。")
    if disk or directory_disk or disk_entries != total_entries:
        raise UnsafeArchiveError("不支持分卷 ZIP。")
    if (
        total_entries == 0xFFFF
        or directory_size == 0xFFFFFFFF
        or directory_offset == 0xFFFFFFFF
    ):
        raise UnsafeArchiveError("不支持 ZIP64 归档。")
    if total_entries > limits.max_archive_entries:
        raise UnsafeArchiveError("ZIP 条目数量超过限制。")
    if directory_offset + directory_size > offset:
        raise MediaFeatureError("ZIP 中央目录边界无效。")


def _zip_container_kind(payload: bytes, limits: PreviewLimits) -> str | None:
    try:
        _preflight_zip(payload, limits)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
    except (OSError, ValueError, zipfile.BadZipFile):
        return None
    if "word/document.xml" in names:
        return "docx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return "zip"


def _category(kind: str) -> str:
    if kind in {"video", "audio", "subtitle"}:
        return "media"
    if kind in {"docx", "pptx", "xlsx", "pdf", "ipynb", "office_macro"}:
        return "document"
    if kind == "zip":
        return "archive"
    return kind


def detect_file_type(
    filename: str,
    content_type: str | None = None,
    data: bytes | bytearray | memoryview | None = None,
    *,
    limits: PreviewLimits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """用签名、容器结构、扩展名和 MIME 探测文件类型。"""
    name = _clean_name(filename)
    extension = Path(name).suffix.casefold()
    declared = _mime(content_type)
    kind = "binary"
    confidence = "fallback"
    active_content = extension in _MACRO_EXTENSIONS
    payload = None if data is None else _as_bytes(data, limits.max_file_size)

    if active_content:
        kind, confidence = "office_macro", "extension"
    elif payload is not None and payload.startswith(b"%PDF-"):
        kind, confidence = "pdf", "signature"
    elif payload is not None and payload.startswith(b"PK\x03\x04"):
        kind, confidence = _zip_container_kind(payload, limits) or "zip", "container"
    elif payload is not None and payload.startswith(b"\x89PNG\r\n\x1a\n"):
        kind, confidence = "image", "signature"
    elif payload is not None and payload[:3] == b"\xff\xd8\xff":
        kind, confidence = "image", "signature"
    elif payload is not None and (
        payload.startswith(b"ID3")
        or payload[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}
    ):
        kind, confidence = "audio", "signature"
    elif payload is not None and len(payload) >= 12 and payload[4:8] == b"ftyp":
        if extension in _AUDIO_EXTENSIONS or declared.startswith("audio/"):
            kind = "audio"
        else:
            kind = "video"
        confidence = "signature"
    elif payload is not None and payload.startswith(b"\x1aE\xdf\xa3"):
        kind, confidence = "video", "signature"
    elif (
        extension in _VIDEO_EXTENSIONS
        or declared.startswith("video/")
        or declared in {"application/vnd.apple.mpegurl", "application/x-mpegurl"}
    ):
        kind, confidence = "video", "metadata"
    elif extension in _AUDIO_EXTENSIONS or declared.startswith("audio/"):
        kind, confidence = "audio", "metadata"
    elif extension in _SUBTITLE_EXTENSIONS or declared in {
        "text/vtt",
        "application/x-subrip",
        "application/ttml+xml",
    }:
        kind, confidence = "subtitle", "metadata"
    elif extension in {".docx", ".pptx", ".xlsx", ".ipynb", ".zip", ".pdf"}:
        kind, confidence = extension[1:], "extension"
    elif declared in set(_MIME_BY_KIND.values()):
        kind = next(
            candidate for candidate, mime in _MIME_BY_KIND.items() if mime == declared
        )
        confidence = "mime"
    elif declared.startswith("image/"):
        kind, confidence = "image", "mime"
    elif declared.startswith("text/") or extension in {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".py",
        ".log",
    }:
        kind, confidence = "text", "metadata"

    guessed = mimetypes.guess_type(name)[0]
    mime_type = _MIME_BY_KIND.get(kind, "application/octet-stream")
    if "*" in mime_type:
        mime_type = declared or guessed or mime_type
    return {
        "kind": kind,
        "category": _category(kind),
        "extension": extension,
        "mime_type": mime_type,
        "declared_mime_type": declared or None,
        "confidence": confidence,
        "previewable": kind in _PREVIEWABLE,
        "active_content": active_content,
    }


def _safe_member_name(name: str, limits: PreviewLimits) -> str:
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or len(name) > limits.max_path_length
    ):
        raise UnsafeArchiveError("ZIP 条目路径无效。")
    if name in {"/", "\\"}:
        raise UnsafeArchiveError("ZIP 条目路径无效。")
    if "\\" in name or name.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", name):
        raise UnsafeArchiveError("ZIP 条目包含绝对路径或路径穿越。")
    stripped = name[:-1] if name.endswith("/") else name
    parts = stripped.split("/")
    if not stripped or any(part in {"", ".", ".."} for part in parts):
        raise UnsafeArchiveError("ZIP 条目包含空路径段或路径穿越。")
    if len(parts) > limits.max_path_depth:
        raise UnsafeArchiveError("ZIP 条目嵌套路径过深。")
    if any(_CONTROL_RE.search(part) for part in parts):
        raise UnsafeArchiveError("ZIP 条目路径包含控制字符。")
    return "/".join(parts) + ("/" if name.endswith("/") else "")


def _validate_archive(
    archive: zipfile.ZipFile, limits: PreviewLimits
) -> tuple[list[zipfile.ZipInfo], bool]:
    infos = archive.infolist()
    if len(infos) > limits.max_archive_entries:
        raise UnsafeArchiveError("ZIP 条目数量超过限制。")
    total = 0
    seen: set[str] = set()
    active_content = False
    for info in infos:
        safe_name = _safe_member_name(info.filename, limits)
        folded = unicodedata.normalize("NFKC", safe_name).casefold()
        if folded in seen:
            raise UnsafeArchiveError("ZIP 包含重名条目。")
        seen.add(folded)
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise UnsafeArchiveError("ZIP 包含符号链接条目。")
        if info.flag_bits & 0x1:
            raise UnsafeArchiveError("不预览加密 ZIP 条目。")
        if (
            info.file_size < 0
            or info.compress_size < 0
            or info.file_size > limits.max_entry_size
        ):
            raise UnsafeArchiveError("ZIP 条目声明大小超过限制。")
        total += info.file_size
        if total > limits.max_uncompressed_size:
            raise UnsafeArchiveError("ZIP 声明的解压总大小超过限制。")
        if info.file_size > 1024 * 1024 and (
            info.compress_size == 0
            or info.file_size > info.compress_size * limits.max_compression_ratio
        ):
            raise UnsafeArchiveError("ZIP 条目压缩比异常，疑似解压炸弹。")
        active_content = active_content or bool(_ACTIVE_ARCHIVE_NAMES.search(safe_name))
    return infos, active_content


def _open_archive(
    payload: bytes, limits: PreviewLimits
) -> tuple[zipfile.ZipFile, list[zipfile.ZipInfo], bool]:
    archive: zipfile.ZipFile | None = None
    try:
        _preflight_zip(payload, limits)
        archive = zipfile.ZipFile(io.BytesIO(payload))
        infos, active = _validate_archive(archive, limits)
        return archive, infos, active
    except MediaFeatureError:
        if archive is not None:
            archive.close()
        raise
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        if archive is not None:
            archive.close()
        raise MediaFeatureError("ZIP/Office 文件损坏或格式无效。") from exc


def _read_entry(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise MediaFeatureError(f"归档缺少必要条目：{name}") from exc
    if info.file_size > limit:
        raise MediaFeatureError(f"归档条目 {name} 超过读取限制。")
    try:
        with archive.open(info, "r") as stream:
            content = stream.read(limit + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise MediaFeatureError(f"无法安全读取归档条目：{name}") from exc
    if len(content) > limit or len(content) != info.file_size:
        raise MediaFeatureError(f"归档条目 {name} 实际大小不一致。")
    return content


def _xml_root(content: bytes, limits: PreviewLimits) -> ElementTree.Element:
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise MediaFeatureError("XML 包含 DTD/实体声明，已拒绝解析。")
    count = 0
    depth = 0
    root: ElementTree.Element | None = None
    try:
        for event, element in ElementTree.iterparse(
            io.BytesIO(content), events=("start", "end")
        ):
            if event == "start":
                count += 1
                depth += 1
                if root is None:
                    root = element
                if count > limits.max_xml_elements or depth > limits.max_xml_depth:
                    raise MediaFeatureError("Office XML 元素过多或嵌套过深。")
            else:
                depth -= 1
    except ElementTree.ParseError as exc:
        raise MediaFeatureError("Office XML 结构无效。") from exc
    if root is None:
        raise MediaFeatureError("Office XML 内容为空。")
    return root


def _local_tag(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _docx_preview(
    archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo], limits: PreviewLimits
) -> dict[str, Any]:
    names = {info.filename for info in infos}
    candidates = ["word/document.xml"] + sorted(
        (
            name
            for name in names
            if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
        ),
        key=_natural_key,
    )
    paragraphs: list[str] = []
    used = 0
    truncated = False
    for name in candidates:
        if name not in names:
            continue
        root = _xml_root(_read_entry(archive, name, limits.max_xml_size), limits)
        for paragraph in (node for node in root.iter() if _local_tag(node) == "p"):
            pieces: list[str] = []
            for node in paragraph.iter():
                tag = _local_tag(node)
                if tag == "t" and node.text:
                    pieces.append(node.text)
                elif tag == "tab":
                    pieces.append("\t")
                elif tag in {"br", "cr"}:
                    pieces.append("\n")
            text = _clean_text("".join(pieces), limits.max_text_chars - used)
            if text:
                paragraphs.append(text)
                used += len(text) + 1
            if used >= limits.max_text_chars:
                truncated = True
                break
        if truncated:
            break
    return {
        "kind": "docx",
        "paragraphs": paragraphs,
        "text": "\n".join(paragraphs),
        "truncated": truncated,
    }


def _pptx_preview(
    archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo], limits: PreviewLimits
) -> dict[str, Any]:
    slide_names = sorted(
        (
            info.filename
            for info in infos
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", info.filename)
        ),
        key=_natural_key,
    )
    slides: list[dict[str, Any]] = []
    used = 0
    truncated = False
    for index, name in enumerate(slide_names, 1):
        root = _xml_root(_read_entry(archive, name, limits.max_xml_size), limits)
        texts = [
            _clean_text(node.text, limits.max_text_chars - used)
            for node in root.iter()
            if _local_tag(node) == "t" and node.text
        ]
        texts = [value for value in texts if value]
        text = "\n".join(texts)
        slides.append({"number": index, "text": text})
        used += len(text)
        if used >= limits.max_text_chars:
            truncated = True
            break
    return {
        "kind": "pptx",
        "slides": slides,
        "slide_count": len(slide_names),
        "truncated": truncated,
    }


def _column_number(reference: str) -> int:
    match = re.match(r"([A-Za-z]+)", reference)
    if not match:
        return 0
    result = 0
    for character in match.group(1).upper():
        result = result * 26 + ord(character) - 64
    return result


def _shared_strings(
    archive: zipfile.ZipFile, names: set[str], limits: PreviewLimits
) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    root = _xml_root(
        _read_entry(archive, "xl/sharedStrings.xml", limits.max_xml_size), limits
    )
    result: list[str] = []
    used = 0
    for item in (node for node in root.iter() if _local_tag(node) == "si"):
        text = "".join(
            node.text or "" for node in item.iter() if _local_tag(node) == "t"
        )
        text = _clean_text(text, limits.max_text_chars - used)
        result.append(text)
        used += len(text)
        if used >= limits.max_text_chars:
            break
    return result


def _xlsx_preview(
    archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo], limits: PreviewLimits
) -> dict[str, Any]:
    names = {info.filename for info in infos}
    shared = _shared_strings(archive, names, limits)
    sheet_names: list[str] = []
    workbook = _xml_root(
        _read_entry(archive, "xl/workbook.xml", limits.max_xml_size), limits
    )
    for node in workbook.iter():
        if _local_tag(node) == "sheet":
            sheet_names.append(
                _clean_text(node.attrib.get("name"), 255)
                or f"Sheet {len(sheet_names) + 1}"
            )
    paths = sorted(
        (name for name in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)),
        key=_natural_key,
    )
    sheets: list[dict[str, Any]] = []
    used = 0
    truncated = False
    for sheet_index, path in enumerate(paths):
        root = _xml_root(_read_entry(archive, path, limits.max_xml_size), limits)
        rows: list[list[str | None]] = []
        row_numbers: list[int] = []
        for row in (node for node in root.iter() if _local_tag(node) == "row"):
            if len(rows) >= limits.max_sheet_rows:
                truncated = True
                break
            values: list[str | None] = []
            for cell in (node for node in row if _local_tag(node) == "c"):
                column = _column_number(cell.attrib.get("r", "")) or len(values) + 1
                if column > limits.max_sheet_columns:
                    truncated = True
                    continue
                while len(values) < column:
                    values.append(None)
                cell_type = cell.attrib.get("t", "")
                value_node = next(
                    (node for node in cell.iter() if _local_tag(node) == "v"), None
                )
                if cell_type == "inlineStr":
                    value = "".join(
                        node.text or ""
                        for node in cell.iter()
                        if _local_tag(node) == "t"
                    )
                else:
                    value = value_node.text if value_node is not None else ""
                    if cell_type == "s" and value:
                        try:
                            value = shared[int(value)]
                        except (IndexError, ValueError):
                            value = ""
                    elif cell_type == "b":
                        value = "TRUE" if value == "1" else "FALSE"
                value = _clean_text(value, max(0, limits.max_text_chars - used))
                values[column - 1] = value
                used += len(value)
                if used >= limits.max_text_chars:
                    truncated = True
                    break
            rows.append(values)
            try:
                row_numbers.append(int(row.attrib.get("r", len(rows))))
            except ValueError:
                row_numbers.append(len(rows))
            if truncated and used >= limits.max_text_chars:
                break
        sheets.append(
            {
                "name": (
                    sheet_names[sheet_index]
                    if sheet_index < len(sheet_names)
                    else f"Sheet {sheet_index + 1}"
                ),
                "rows": rows,
                "row_numbers": row_numbers,
            }
        )
        if used >= limits.max_text_chars:
            break
    return {
        "kind": "xlsx",
        "sheets": sheets,
        "sheet_count": len(paths),
        "truncated": truncated,
    }


def _json_shape(
    value: object, *, depth: int = 0, counter: list[int] | None = None
) -> None:
    if counter is None:
        counter = [0]
    counter[0] += 1
    if depth > 16 or counter[0] > 20_000:
        raise MediaFeatureError("Notebook JSON 结构过深或项目过多。")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 1024 or "\x00" in key:
                raise MediaFeatureError("Notebook JSON 键无效。")
            _json_shape(child, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        for child in value:
            _json_shape(child, depth=depth + 1, counter=counter)
    elif isinstance(value, float) and not math.isfinite(value):
        raise MediaFeatureError("Notebook 包含无效数字。")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise MediaFeatureError("Notebook 包含不支持的数据类型。")


def _ipynb_preview(payload: bytes, limits: PreviewLimits) -> dict[str, Any]:
    try:
        notebook = json.loads(
            payload.decode("utf-8-sig"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MediaFeatureError("IPYNB 不是有效的 UTF-8 JSON。") from exc
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        raise MediaFeatureError("IPYNB 缺少 cells 数组。")
    _json_shape(notebook)
    cells: list[dict[str, Any]] = []
    used = 0
    truncated = len(notebook["cells"]) > limits.max_notebook_cells
    for raw_cell in notebook["cells"][: limits.max_notebook_cells]:
        if not isinstance(raw_cell, dict):
            continue
        cell_type = raw_cell.get("cell_type")
        if cell_type not in {"markdown", "code", "raw"}:
            cell_type = "raw"
        source = _plain_preview_text(
            raw_cell.get("source", ""), max(0, limits.max_text_chars - used)
        )
        used += len(source)
        clean: dict[str, Any] = {
            "cell_type": cell_type,
            "source": source,
            "render_as": "plain_text",
        }
        if cell_type == "code":
            outputs: list[dict[str, str]] = []
            for output in (
                raw_cell.get("outputs", [])
                if isinstance(raw_cell.get("outputs"), list)
                else []
            ):
                if not isinstance(output, dict):
                    continue
                output_type = output.get("output_type")
                text: object = ""
                if output_type == "stream":
                    text = output.get("text", "")
                elif output_type in {"execute_result", "display_data"}:
                    data = output.get("data")
                    if isinstance(data, dict):
                        text = data.get("text/plain", "")
                elif output_type == "error":
                    text = f"{_clean_text(output.get('ename'), 200)}: {_clean_text(output.get('evalue'), 1000)}"
                rendered = _plain_preview_text(
                    text, max(0, limits.max_text_chars - used)
                )
                if rendered:
                    outputs.append({"output_type": "text", "text": rendered})
                    used += len(rendered)
                if used >= limits.max_text_chars:
                    truncated = True
                    break
            clean["outputs"] = outputs
        cells.append(clean)
        if used >= limits.max_text_chars:
            truncated = True
            break
    return {
        "kind": "ipynb",
        "nbformat": (
            notebook.get("nbformat") if type(notebook.get("nbformat")) is int else None
        ),
        "trusted": False,
        "cells": cells,
        "truncated": truncated,
    }


def structured_preview(
    data: bytes | bytearray | memoryview,
    filename: str,
    content_type: str | None = None,
    *,
    limits: PreviewLimits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """返回可 JSON 序列化的安全结构化预览；不提取文件到磁盘。"""
    payload = _as_bytes(data, limits.max_file_size)
    detected = detect_file_type(filename, content_type, payload, limits=limits)
    kind = detected["kind"]
    if kind == "office_macro" or detected["active_content"]:
        raise MediaFeatureError("含宏的 Office 文件不提供结构化预览。")
    if kind == "ipynb":
        if len(payload) > limits.max_notebook_size:
            raise MediaFeatureError("IPYNB 超过大小限制。")
        result = _ipynb_preview(payload, limits)
        result.update({"name": _clean_name(filename), "file_type": detected})
        return result
    if kind not in {"zip", "docx", "pptx", "xlsx"}:
        raise MediaFeatureError("该文件类型不支持结构化预览。")

    archive, infos, active_content = _open_archive(payload, limits)
    try:
        if active_content:
            raise MediaFeatureError("归档包含宏、嵌入对象或 ActiveX 内容，已拒绝预览。")
        if kind == "docx":
            result = _docx_preview(archive, infos, limits)
        elif kind == "pptx":
            result = _pptx_preview(archive, infos, limits)
        elif kind == "xlsx":
            result = _xlsx_preview(archive, infos, limits)
        else:
            result = {
                "kind": "zip",
                "entries": [
                    {
                        "name": info.filename,
                        "size": info.file_size,
                        "compressed_size": info.compress_size,
                        "directory": info.is_dir(),
                    }
                    for info in infos
                ],
                "entry_count": len(infos),
                "uncompressed_size": sum(info.file_size for info in infos),
            }
    finally:
        archive.close()
    result.update({"name": _clean_name(filename), "file_type": detected})
    return result


def _record_value(record: object, name: str, default: object = None) -> object:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _metadata_values(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        return {}
    aliases = {
        "name": ("display_name", "filename", "name", "title"),
        "content_type": ("content_type", "content-type", "mime_type", "type"),
    }
    result: dict[str, object] = {}
    queue: list[tuple[Mapping[object, object], int]] = [(raw, 0)]
    visited = 0
    while queue and visited < 128:
        current, depth = queue.pop(0)
        visited += 1
        for target, keys in aliases.items():
            if target not in result:
                for key in keys:
                    candidate = current.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        result[target] = candidate
                        break
        if depth < 3:
            for child in current.values():
                if isinstance(child, Mapping):
                    queue.append((child, depth + 1))
        if len(result) == len(aliases):
            break
    return result


def _safe_source_id(value: object) -> str | None:
    if type(value) is int and value > 0:
        value = str(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 255 or _CONTROL_RE.search(value):
        return None
    return value


def safe_download_descriptor(record: object) -> dict[str, Any]:
    """生成仅引用后端受控标识的下载动作，不暴露本地路径或任意 URL。"""
    source_id = _safe_source_id(
        _record_value(record, "source_id") or _record_value(record, "id")
    )
    if source_id is None:
        raise MediaFeatureError("媒体文件缺少安全的 source_id。")
    raw = _metadata_values(
        _record_value(record, "raw_data", record if isinstance(record, Mapping) else {})
    )
    name = _clean_name(
        _record_value(record, "display_name")
        or _record_value(record, "filename")
        or raw.get("name")
    )
    return {
        "available": True,
        "transport": "dashboard_action",
        "action": "download_material",
        "source_id": source_id,
        "download_name": name,
        "disposition": "attachment",
    }


def safe_playback_descriptor(record: object) -> dict[str, Any]:
    """生成受控播放动作；URL、认证查询参数和文件系统路径不会进入 DTO。"""
    source_id = _safe_source_id(
        _record_value(record, "source_id") or _record_value(record, "id")
    )
    if source_id is None:
        raise MediaFeatureError("媒体文件缺少安全的 source_id。")
    raw = _metadata_values(
        _record_value(record, "raw_data", record if isinstance(record, Mapping) else {})
    )
    name = _clean_name(
        _record_value(record, "display_name")
        or _record_value(record, "filename")
        or raw.get("name")
    )
    content_type = _mime(
        _record_value(record, "content_type") or raw.get("content_type")
    )
    detected = detect_file_type(name, content_type)
    if detected["kind"] not in {"video", "audio"}:
        raise MediaFeatureError("该记录不是可播放的音视频。")
    local = bool(_record_value(record, "local_path"))
    cloud = bool(_record_value(record, "cloud_path"))
    remote = bool(_record_value(record, "url"))
    return {
        "available": True,
        "status": "ready" if local or cloud or remote else "requires_download",
        "transport": "dashboard_action",
        "action": "play_course_media",
        "source_id": source_id,
        "media_kind": detected["kind"],
        "content_type": content_type or detected["mime_type"],
        "name": name,
    }


def _subtitle_match_key(name: str) -> tuple[str, str | None]:
    stem = Path(name).stem
    language: str | None = None
    match = _LANGUAGE_SUFFIX_RE.search(stem)
    if match:
        language = match.group(0).lstrip("._- ").replace("_", "-")[:32]
        stem = stem[: match.start()]
    normalized = unicodedata.normalize("NFKC", stem).casefold()
    normalized = re.sub(r"[\s._\-（）()\[\]]+", "", normalized)
    return normalized, language


def discover_subtitles(
    media_items: Sequence[dict[str, Any]], subtitles: Sequence[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按 Unicode 规范化后的文件名发现字幕，返回新列表和未匹配字幕。"""
    result = [
        dict(item, subtitles=list(item.get("subtitles", []))) for item in media_items
    ]
    keys = [_subtitle_match_key(str(item.get("name", "")))[0] for item in result]
    unmatched: list[dict[str, Any]] = []
    for subtitle in subtitles:
        key, language = _subtitle_match_key(str(subtitle.get("name", "")))
        matches = [
            index for index, candidate in enumerate(keys) if key and key == candidate
        ]
        descriptor = dict(subtitle)
        descriptor["language"] = language
        if len(matches) == 1:
            result[matches[0]]["subtitles"].append(descriptor)
        else:
            unmatched.append(descriptor)
    return result, unmatched


def aggregate_course_media(
    course_files: Iterable[object],
    raw_metadata: Iterable[Mapping[str, object]] | None = None,
    *,
    max_records: int = 5_000,
) -> dict[str, Any]:
    """从 CourseFile、字典或额外原始元数据中聚合视频、音频和字幕。"""
    if type(max_records) is not int or not 1 <= max_records <= 100_000:
        raise MediaFeatureError("媒体聚合数量限制不正确。")
    records: list[object] = []
    for source in (course_files, raw_metadata or ()):
        for record in source:
            if len(records) >= max_records:
                raise MediaFeatureError("课程媒体记录数量超过限制。")
            records.append(record)
    media: list[dict[str, Any]] = []
    subtitles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if (
            _record_value(record, "is_active", True) is False
            or _record_value(record, "hidden", False) is True
        ):
            continue
        source_id = _safe_source_id(
            _record_value(record, "source_id") or _record_value(record, "id")
        )
        if source_id is None or source_id in seen:
            continue
        raw = _metadata_values(
            _record_value(
                record, "raw_data", record if isinstance(record, Mapping) else {}
            )
        )
        name = _clean_name(
            _record_value(record, "display_name")
            or _record_value(record, "filename")
            or raw.get("name")
        )
        content_type = _mime(
            _record_value(record, "content_type") or raw.get("content_type")
        )
        detected = detect_file_type(name, content_type)
        kind = detected["kind"]
        if kind not in {"video", "audio", "subtitle"}:
            continue
        seen.add(source_id)
        base = {
            "source_id": source_id,
            "name": name,
            "media_kind": kind,
            "content_type": content_type or detected["mime_type"],
            "size": (
                _record_value(record, "size")
                if type(_record_value(record, "size")) is int
                else None
            ),
            "download": safe_download_descriptor(record),
        }
        if kind == "subtitle":
            subtitles.append(base)
        else:
            base["playback"] = safe_playback_descriptor(record)
            base["subtitles"] = []
            media.append(base)
    media, unmatched = discover_subtitles(media, subtitles)
    return {
        "items": media,
        "subtitles": subtitles,
        "unmatched_subtitles": unmatched,
        "counts": {
            "video": sum(item["media_kind"] == "video" for item in media),
            "audio": sum(item["media_kind"] == "audio" for item in media),
            "subtitle": len(subtitles),
        },
    }


def _capability(
    name: str, available: bool, reason: str | None = None
) -> dict[str, Any]:
    return {
        "capability": name,
        "available": available,
        "status": "available" if available else "unavailable",
        "reason": reason,
    }


def pdf_merge_capability() -> dict[str, Any]:
    available = importlib.util.find_spec("pypdf") is not None
    return _capability(
        "pdf_merge",
        available,
        None if available else "缺少可选依赖 pypdf，PDF 合并不可用。",
    )


def merge_pdfs(
    documents: Sequence[bytes | bytearray | memoryview],
    *,
    max_documents: int = 32,
    max_total_size: int = 128 * 1024 * 1024,
    max_pages: int = 1000,
) -> dict[str, Any]:
    """在内存中合并 PDF；不可用时返回明确状态，绝不创建空白成功结果。"""
    capability = pdf_merge_capability()
    if not capability["available"]:
        return capability
    if (
        not isinstance(documents, Sequence)
        or not documents
        or len(documents) > max_documents
    ):
        raise MediaFeatureError("PDF 文件数量不正确或超过限制。")
    payloads: list[bytes] = []
    total = 0
    for document in documents:
        payload = _as_bytes(document, max_total_size)
        if not payload.startswith(b"%PDF-"):
            raise MediaFeatureError("输入包含非 PDF 文件。")
        total += len(payload)
        if total > max_total_size:
            raise MediaFeatureError("PDF 输入总大小超过限制。")
        payloads.append(payload)
    try:
        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        page_count = 0
        for payload in payloads:
            reader = PdfReader(io.BytesIO(payload), strict=False)
            if reader.is_encrypted:
                raise MediaFeatureError("不合并加密 PDF。")
            if page_count + len(reader.pages) > max_pages:
                raise MediaFeatureError("PDF 总页数超过限制。")
            for page in reader.pages:
                writer.add_page(page)
                page_count += 1
        output = io.BytesIO()
        writer.write(output)
        merged = output.getvalue()
        if not merged.startswith(b"%PDF-") or not merged:
            raise MediaFeatureError("PDF 合并未产生有效结果。")
        return {
            "capability": "pdf_merge",
            "available": True,
            "status": "succeeded",
            "data": merged,
            "size": len(merged),
            "page_count": page_count,
        }
    except MediaFeatureError:
        raise
    except Exception as exc:
        raise MediaFeatureError("PDF 合并失败。") from exc


def _ffmpeg_executable() -> str | None:
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError):
        return None
    path = Path(bundled)
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None


def video_screenshot_pdf_capability() -> dict[str, Any]:
    missing: list[str] = []
    if _ffmpeg_executable() is None:
        missing.append("ffmpeg")
    if importlib.util.find_spec("PIL") is None:
        missing.append("Pillow")
    reason = (
        f"缺少可选依赖：{', '.join(missing)}，视频截图 PDF 不可用。"
        if missing
        else None
    )
    return _capability("video_screenshot_pdf", not missing, reason)


def video_screenshots_pdf(
    video: bytes | bytearray | memoryview,
    *,
    interval_seconds: int = 60,
    max_frames: int = 60,
    max_video_size: int = 256 * 1024 * 1024,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """在私有临时目录中调用 ffmpeg 截图并由 Pillow 生成 PDF。"""
    capability = video_screenshot_pdf_capability()
    if not capability["available"]:
        return capability
    if type(interval_seconds) is not int or not 1 <= interval_seconds <= 3600:
        raise MediaFeatureError("截图间隔必须在 1 到 3600 秒之间。")
    if type(max_frames) is not int or not 1 <= max_frames <= 200:
        raise MediaFeatureError("截图数量限制不正确。")
    payload = _as_bytes(video, max_video_size)
    if not payload:
        raise MediaFeatureError("视频内容为空。")
    from PIL import Image, UnidentifiedImageError

    with tempfile.TemporaryDirectory(prefix="sjtu-media-") as directory:
        root = Path(directory)
        pattern = root / "frame-%04d.jpg"
        command = [
            _ffmpeg_executable() or "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-max_alloc",
            str(64 * 1024 * 1024),
            "-protocol_whitelist",
            "pipe",
            "-i",
            "pipe:0",
            "-vf",
            f"fps=1/{interval_seconds},scale=1920:-2:force_original_aspect_ratio=decrease",
            "-frames:v",
            str(max_frames),
            "-threads",
            "1",
            os.fspath(pattern),
        ]
        try:
            completed = subprocess.run(
                command,
                input=payload,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MediaFeatureError("ffmpeg 截图失败或超时。") from exc
        if completed.returncode != 0:
            raise MediaFeatureError("ffmpeg 无法解析视频，截图 PDF 未生成。")
        frame_paths = sorted(
            root.glob("frame-*.jpg"), key=lambda path: _natural_key(path.name)
        )
        if not frame_paths or len(frame_paths) > max_frames:
            raise MediaFeatureError("视频没有可用截图或截图数量异常。")
        images: list[Any] = []
        try:
            for path in frame_paths:
                if path.stat().st_size > 16 * 1024 * 1024:
                    raise MediaFeatureError("单张视频截图超过大小限制。")
                image = Image.open(path)
                if (
                    image.width <= 0
                    or image.height <= 0
                    or image.width * image.height > 40_000_000
                ):
                    image.close()
                    raise MediaFeatureError("视频截图尺寸无效或过大。")
                image.load()
                images.append(image.convert("RGB"))
                image.close()
            output = io.BytesIO()
            images[0].save(
                output, "PDF", save_all=True, append_images=images[1:], resolution=100.0
            )
            pdf = output.getvalue()
        except (OSError, UnidentifiedImageError) as exc:
            raise MediaFeatureError("Pillow 无法安全读取截图。") from exc
        finally:
            for image in images:
                image.close()
        if not pdf.startswith(b"%PDF-") or len(pdf) > 256 * 1024 * 1024:
            raise MediaFeatureError("截图 PDF 结果无效或过大。")
        return {
            "capability": "video_screenshot_pdf",
            "available": True,
            "status": "succeeded",
            "data": pdf,
            "size": len(pdf),
            "frame_count": len(frame_paths),
        }


class MediaFeatureService:
    """供 DashboardService 组合调用的无状态外观。"""

    def __init__(self, limits: PreviewLimits = DEFAULT_LIMITS) -> None:
        self.limits = limits

    def detect_file_type(
        self,
        filename: str,
        content_type: str | None = None,
        data: bytes | bytearray | memoryview | None = None,
    ) -> dict[str, Any]:
        return detect_file_type(filename, content_type, data, limits=self.limits)

    def preview(
        self,
        data: bytes | bytearray | memoryview,
        filename: str,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        return structured_preview(data, filename, content_type, limits=self.limits)

    def aggregate_course_media(
        self,
        course_files: Iterable[object],
        raw_metadata: Iterable[Mapping[str, object]] | None = None,
        *,
        max_records: int = 5_000,
    ) -> dict[str, Any]:
        return aggregate_course_media(
            course_files, raw_metadata, max_records=max_records
        )

    @staticmethod
    def safe_playback_descriptor(record: object) -> dict[str, Any]:
        return safe_playback_descriptor(record)

    @staticmethod
    def safe_download_descriptor(record: object) -> dict[str, Any]:
        return safe_download_descriptor(record)

    @staticmethod
    def capabilities() -> dict[str, dict[str, Any]]:
        return {
            "pdf_merge": pdf_merge_capability(),
            "video_screenshot_pdf": video_screenshot_pdf_capability(),
        }

    @staticmethod
    def merge_pdfs(
        documents: Sequence[bytes | bytearray | memoryview], **limits: int
    ) -> dict[str, Any]:
        return merge_pdfs(documents, **limits)

    @staticmethod
    def video_screenshots_pdf(
        video: bytes | bytearray | memoryview, **options: int
    ) -> dict[str, Any]:
        return video_screenshots_pdf(video, **options)


__all__ = [
    "DEFAULT_LIMITS",
    "MediaFeatureError",
    "MediaFeatureService",
    "PreviewLimits",
    "UnsafeArchiveError",
    "aggregate_course_media",
    "detect_file_type",
    "discover_subtitles",
    "merge_pdfs",
    "pdf_merge_capability",
    "safe_download_descriptor",
    "safe_playback_descriptor",
    "structured_preview",
    "video_screenshot_pdf_capability",
    "video_screenshots_pdf",
]
