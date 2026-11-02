"""有界、只读地把常见课程文档（PPTX/DOCX/XLSX/PDF）转换为分页正文与内嵌图片。

- 不执行宏、不解压到磁盘，只读取 OOXML 部件中的 XML 与被幻灯片/正文引用的图片；
- ZIP 结构、条目路径、XML DTD/实体与元素规模复用 ``media_features`` 的安全校验；
- 正文按“页/幻灯片/工作表/分段”切分为 ``units``，便于按需分页读取；
- 图片只作为多模态模型输入的候选，调用方负责数量、像素与体积上限。
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree

from sjtu_learning_assistant.media_features import (
    MediaFeatureError,
    PreviewLimits,
    _clean_text,
    _local_tag,
    _natural_key,
    _read_entry,
    _safe_member_name,
    _validate_archive,
    _xlsx_preview,
    _xml_root,
)

OOXML_EXTENSIONS = frozenset({".pptx", ".docx", ".xlsx"})
DOCUMENT_EXTENSIONS = OOXML_EXTENSIONS | {".pdf"}
MAX_DOCUMENT_FILE_SIZE = 512 * 1024**2
MAX_DOCUMENT_CHARS = 400_000
MAX_XML_BYTES_TOTAL = 96 * 1024**2
MAX_PDF_PAGES = 500
MAX_IMAGE_ENTRY_BYTES = 24 * 1024**2
MIN_IMAGE_ENTRY_BYTES = 6 * 1024
DOCX_UNIT_CHARS = 3_000
VISION_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})

# 课件动辄上百页且常嵌入视频：条目数量和单条目声明大小放宽，但 XML 读取仍单独限额。
DOCUMENT_LIMITS = PreviewLimits(
    max_file_size=MAX_DOCUMENT_FILE_SIZE,
    max_archive_entries=8_192,
    max_uncompressed_size=4 * 1024**3,
    max_entry_size=MAX_DOCUMENT_FILE_SIZE,
    max_xml_size=16 * 1024**2,
    max_xml_elements=400_000,
    max_text_chars=MAX_DOCUMENT_CHARS,
    max_sheet_rows=2_000,
    max_sheet_columns=60,
)

_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_ID = f"{{{_REL_NS}}}id"
_REL_EMBED = f"{{{_REL_NS}}}embed"
_MACRO_PART = re.compile(r"(?:^|/)vbaproject\.bin$", re.IGNORECASE)
_SPACE_RUN = re.compile(r"[ \t　]+")
# PowerPoint/Word 默认把插入图片的文件名写进替代文本，这类说明没有信息量。
_FILENAME_ALT = re.compile(
    r"(?i)^[^\n]{0,120}\.(?:png|jpe?g|gif|bmp|webp|tiff?|emf|wmf|svg|heic)$"
)
# 出现在这么多页以上的图片视为模板装饰（校徽、页眉条），不作为多模态候选。
REPEATED_IMAGE_SLIDES = 3


class DocumentTextError(RuntimeError):
    """文档损坏、不安全或超出上限；消息可直接展示且不含路径。"""


class DocumentUnsupportedError(DocumentTextError):
    """该格式（或加密文档）不提供正文提取。"""


@dataclass(frozen=True)
class DocumentImage:
    unit: int
    member: str
    size: int


@dataclass(frozen=True)
class DocumentText:
    kind: str
    extractor: str
    unit_label: str
    units: tuple[str, ...]
    unit_count: int
    truncated: bool
    images: tuple[DocumentImage, ...] = field(default_factory=tuple)
    image_reference_count: int = 0

    @property
    def text(self) -> str:
        return "\n\n".join(unit for unit in self.units if unit)


class _Budget:
    def __init__(self, chars: int) -> None:
        self.remaining = chars
        self.xml_bytes = 0
        self.truncated = False
        self.taken = False

    def take(self, text: str) -> str:
        # DocumentText.text 用空行连接各单元，分隔符也计入预算。
        separator = 2 if self.taken else 0
        available = self.remaining - separator
        if available <= 0:
            self.remaining = 0
            self.truncated = True
            return ""
        if len(text) > available:
            self.truncated = True
            text = text[:available]
        self.remaining -= len(text) + separator
        self.taken = True
        return text


def _line(value: str | None, limit: int = 20_000) -> str:
    text = _clean_text(value, limit)
    return _SPACE_RUN.sub(" ", text).strip()


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        tag = _local_tag(node)
        if tag == "t" and node.text:
            pieces.append(node.text)
        elif tag == "tab":
            pieces.append("\t")
        elif tag in {"br", "cr"}:
            pieces.append("\n")
    return "\n".join(_line(part) for part in "".join(pieces).split("\n") if _line(part))


def _drawing_blocks(root: ElementTree.Element) -> list[str]:
    """按文档顺序输出 DrawingML 段落与表格；表格单元格用“ | ”连接。"""
    blocks: list[str] = []

    def visit(node: ElementTree.Element) -> None:
        tag = _local_tag(node)
        if tag == "tbl":
            rows: list[str] = []
            for row in (item for item in node.iter() if _local_tag(item) == "tr"):
                cells = [
                    " ".join(
                        _paragraph_text(paragraph)
                        for paragraph in cell.iter()
                        if _local_tag(paragraph) == "p"
                    ).strip()
                    for cell in row
                    if _local_tag(cell) == "tc"
                ]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append("[表格]\n" + "\n".join(rows))
            return
        if tag == "p":
            text = _paragraph_text(node)
            if text:
                blocks.append(text)
            return
        for child in node:
            visit(child)

    visit(root)
    return blocks


def _alt_texts(root: ElementTree.Element) -> list[str]:
    result: list[str] = []
    for node in root.iter():
        if _local_tag(node) != "cNvPr":
            continue
        description = _line(node.attrib.get("descr"), 600)
        if _FILENAME_ALT.match(description):
            continue
        if description and description not in result:
            result.append(description)
    return result


def _open_ooxml(path: Path) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    try:
        size = path.stat().st_size
    except OSError:
        raise DocumentTextError("无法读取文档。") from None
    if size > MAX_DOCUMENT_FILE_SIZE:
        raise DocumentTextError("文档超过 512 MiB，暂不提取正文。")
    archive: zipfile.ZipFile | None = None
    try:
        archive = zipfile.ZipFile(path)
        infos, _active = _validate_archive(archive, DOCUMENT_LIMITS)
        names = {info.filename: info for info in infos}
        if any(_MACRO_PART.search(name) for name in names):
            raise DocumentUnsupportedError("文档包含宏，已拒绝提取正文。")
        return archive, names
    except DocumentTextError:
        if archive is not None:
            archive.close()
        raise
    except MediaFeatureError as exc:
        if archive is not None:
            archive.close()
        raise DocumentTextError(str(exc) or "文档结构不安全。") from None
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError):
        if archive is not None:
            archive.close()
        raise DocumentTextError("Office 文档损坏或格式无效。") from None


def _xml(archive: zipfile.ZipFile, name: str, budget: _Budget) -> ElementTree.Element:
    payload = _read_entry(archive, name, DOCUMENT_LIMITS.max_xml_size)
    budget.xml_bytes += len(payload)
    if budget.xml_bytes > MAX_XML_BYTES_TOTAL:
        raise DocumentTextError("文档 XML 总量超过限制。")
    return _xml_root(payload, DOCUMENT_LIMITS)


def _rels_name(part: str) -> str:
    directory, leaf = posixpath.split(part)
    return posixpath.join(directory, "_rels", leaf + ".rels")


def _relationships(
    archive: zipfile.ZipFile, names: dict[str, zipfile.ZipInfo], part: str, budget: _Budget
) -> dict[str, tuple[str, str]]:
    """返回 {rId: (类型后缀, 包内规范路径)}；外部链接与越界目标被忽略。"""
    rels = _rels_name(part)
    if rels not in names:
        return {}
    result: dict[str, tuple[str, str]] = {}
    for node in _xml(archive, rels, budget).iter():
        if _local_tag(node) != "Relationship":
            continue
        if node.attrib.get("TargetMode", "").casefold() == "external":
            continue
        rel_id = node.attrib.get("Id", "")
        target = node.attrib.get("Target", "")
        kind = node.attrib.get("Type", "").rsplit("/", 1)[-1]
        if not rel_id or not target or "\\" in target or "\x00" in target:
            continue
        if target.startswith("/"):
            resolved = posixpath.normpath(target.lstrip("/"))
        else:
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
        if resolved.startswith("../") or resolved in {"..", "."}:
            continue
        try:
            resolved = _safe_member_name(resolved, DOCUMENT_LIMITS)
        except MediaFeatureError:
            continue
        if resolved in names:
            result[rel_id] = (kind, resolved)
    return result


def _image_member(name: str, names: dict[str, zipfile.ZipInfo]) -> bool:
    info = names.get(name)
    return bool(
        info is not None
        and Path(name).suffix.casefold() in VISION_IMAGE_SUFFIXES
        and MIN_IMAGE_ENTRY_BYTES <= info.file_size <= MAX_IMAGE_ENTRY_BYTES
    )


def _slide_order(
    archive: zipfile.ZipFile, names: dict[str, zipfile.ZipInfo], budget: _Budget
) -> list[str]:
    """按 presentation.xml 的 sldIdLst 取放映顺序；缺失时回退到文件名自然序。"""
    fallback = sorted(
        (name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=_natural_key,
    )
    if "ppt/presentation.xml" not in names:
        return fallback
    rels = _relationships(archive, names, "ppt/presentation.xml", budget)
    ordered: list[str] = []
    for node in _xml(archive, "ppt/presentation.xml", budget).iter():
        if _local_tag(node) != "sldId":
            continue
        target = rels.get(node.attrib.get(_REL_ID, ""))
        if target and target[0] == "slide" and target[1] not in ordered:
            ordered.append(target[1])
    return ordered or fallback


def _notes_text(root: ElementTree.Element) -> str:
    paragraphs: list[str] = []
    for shape in (node for node in root.iter() if _local_tag(node) == "sp"):
        placeholder = next((node for node in shape.iter() if _local_tag(node) == "ph"), None)
        if placeholder is None or placeholder.attrib.get("type") != "body":
            continue
        paragraphs.extend(
            text
            for text in (
                _paragraph_text(node) for node in shape.iter() if _local_tag(node) == "p"
            )
            if text
        )
    return "\n".join(paragraphs)


def _pptx(path: Path, max_chars: int) -> DocumentText:
    archive, names = _open_ooxml(path)
    budget = _Budget(max_chars)
    try:
        if "ppt/presentation.xml" not in names:
            raise DocumentTextError("PPTX 缺少 presentation.xml。")
        slides = _slide_order(archive, names, budget)
        total = len(slides)
        units: list[str] = []
        candidates: dict[str, DocumentImage] = {}
        image_slides: dict[str, set[int]] = {}
        references = 0
        for index, slide in enumerate(slides, 1):
            if budget.remaining <= 0:
                budget.truncated = True
                break
            root = _xml(archive, slide, budget)
            rels = _relationships(archive, names, slide, budget)
            hidden = root.attrib.get("show") == "0"
            header = f"【第 {index} 页 / 共 {total} 页{'，隐藏页' if hidden else ''}】"
            lines = _drawing_blocks(root)
            for description in _alt_texts(root):
                lines.append(f"[图片说明] {description}")
            for embed in (node.attrib.get(_REL_EMBED) for node in root.iter()):
                if not embed or embed not in rels or rels[embed][0] != "image":
                    continue
                references += 1
                member = rels[embed][1]
                image_slides.setdefault(member, set()).add(index)
                if member not in candidates and _image_member(member, names):
                    candidates[member] = DocumentImage(index, member, names[member].file_size)
            notes = next((target for kind, target in rels.values() if kind == "notesSlide"), None)
            if notes:
                note_text = _notes_text(_xml(archive, notes, budget))
                if note_text:
                    lines.append("[演讲者备注]\n" + note_text)
            body = "\n".join(lines) if lines else "（本页没有可提取文字）"
            taken = budget.take(header + "\n" + body)
            if not taken:
                break
            units.append(taken)
        images = tuple(
            image
            for member, image in candidates.items()
            if len(image_slides.get(member, ())) < REPEATED_IMAGE_SLIDES
        )
        return DocumentText(
            "pptx", "ooxml-pptx", "页", tuple(units), total, budget.truncated,
            images, references,
        )
    except MediaFeatureError as exc:
        raise DocumentTextError(str(exc) or "PPTX 结构无效。") from None
    finally:
        archive.close()


def _chunks(paragraphs: list[str], size: int) -> Iterator[str]:
    current: list[str] = []
    used = 0
    for paragraph in paragraphs:
        if current and used + len(paragraph) > size:
            yield "\n".join(current)
            current, used = [], 0
        current.append(paragraph)
        used += len(paragraph) + 1
    if current:
        yield "\n".join(current)


def _docx(path: Path, max_chars: int) -> DocumentText:
    archive, names = _open_ooxml(path)
    budget = _Budget(max_chars)
    try:
        if "word/document.xml" not in names:
            raise DocumentTextError("DOCX 缺少 document.xml。")
        root = _xml(archive, "word/document.xml", budget)
        blocks: list[str] = []
        body = next((node for node in root if _local_tag(node) == "body"), root)
        for node in body:
            tag = _local_tag(node)
            if tag == "tbl":
                rows = []
                for row in (item for item in node.iter() if _local_tag(item) == "tr"):
                    cells = [
                        " ".join(
                            _paragraph_text(paragraph)
                            for paragraph in cell.iter()
                            if _local_tag(paragraph) == "p"
                        ).strip()
                        for cell in row
                        if _local_tag(cell) == "tc"
                    ]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    blocks.append("[表格]\n" + "\n".join(rows))
            else:
                blocks.extend(
                    text
                    for text in (
                        _paragraph_text(item)
                        for item in ([node] if tag == "p" else node.iter())
                        if _local_tag(item) == "p"
                    )
                    if text
                )
        blocks.extend(f"[图片说明] {text}" for text in _alt_texts(root))
        rels = _relationships(archive, names, "word/document.xml", budget)
        images: list[DocumentImage] = []
        references = 0
        for embed in (node.attrib.get(_REL_EMBED) for node in root.iter()):
            if not embed or embed not in rels or rels[embed][0] != "image":
                continue
            references += 1
            member = rels[embed][1]
            if _image_member(member, names) and all(item.member != member for item in images):
                images.append(DocumentImage(1, member, names[member].file_size))
        chunks = list(_chunks(blocks, DOCX_UNIT_CHARS)) or ["（文档没有可提取文字）"]
        total = len(chunks)
        units = [
            budget.take(f"【第 {index} 段 / 共 {total} 段】\n{chunk}")
            for index, chunk in enumerate(chunks, 1)
        ]
        return DocumentText(
            "docx", "ooxml-docx", "段", tuple(unit for unit in units if unit), total,
            budget.truncated, tuple(images), references,
        )
    except MediaFeatureError as exc:
        raise DocumentTextError(str(exc) or "DOCX 结构无效。") from None
    finally:
        archive.close()


def _xlsx(path: Path, max_chars: int) -> DocumentText:
    archive, names = _open_ooxml(path)
    try:
        if "xl/workbook.xml" not in names:
            raise DocumentTextError("XLSX 缺少 workbook.xml。")
        limits = replace(DOCUMENT_LIMITS, max_text_chars=max(1, max_chars))
        preview = _xlsx_preview(archive, list(names.values()), limits)
    except MediaFeatureError as exc:
        raise DocumentTextError(str(exc) or "XLSX 结构无效。") from None
    finally:
        archive.close()
    sheets = preview.get("sheets", [])
    total = int(preview.get("sheet_count") or len(sheets))
    budget = _Budget(max_chars)
    units: list[str] = []
    for index, sheet in enumerate(sheets, 1):
        rows = [
            " | ".join(value or "" for value in row).rstrip(" |")
            for row in sheet.get("rows", [])
        ]
        body = "\n".join(row for row in rows if row) or "（工作表为空）"
        units.append(budget.take(f"【工作表 {index} / 共 {total}：{sheet.get('name')}】\n{body}"))
    return DocumentText(
        "xlsx", "ooxml-xlsx", "工作表", tuple(unit for unit in units if unit), total,
        budget.truncated or bool(preview.get("truncated")),
    )


def _pdf(path: Path, max_chars: int) -> DocumentText:
    try:
        size = path.stat().st_size
    except OSError:
        raise DocumentTextError("无法读取文档。") from None
    if size > MAX_DOCUMENT_FILE_SIZE:
        raise DocumentTextError("文档超过 512 MiB，暂不提取正文。")
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        raise DocumentUnsupportedError("缺少 pypdf，PDF 正文不可用。") from None
    budget = _Budget(max_chars)
    units: list[str] = []
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise DocumentUnsupportedError("PDF 已加密，无法提取正文。")
            except DocumentUnsupportedError:
                raise
            except Exception:
                raise DocumentUnsupportedError("PDF 已加密，无法提取正文。") from None
        total = len(reader.pages)
        for index in range(min(total, MAX_PDF_PAGES)):
            if budget.remaining <= 0:
                budget.truncated = True
                break
            try:
                raw = reader.pages[index].extract_text() or ""
            except Exception:
                raw = ""
            lines = [_line(line) for line in raw.splitlines()]
            body = "\n".join(line for line in lines if line) or "（本页没有可提取文字，可能是扫描图片）"
            taken = budget.take(f"【第 {index + 1} 页 / 共 {total} 页】\n{body}")
            if not taken:
                break
            units.append(taken)
        truncated = budget.truncated or total > MAX_PDF_PAGES
    except DocumentTextError:
        raise
    except (PdfReadError, OSError, ValueError, KeyError, TypeError, RecursionError):
        raise DocumentTextError("PDF 损坏或格式无效。") from None
    except Exception:
        raise DocumentTextError("PDF 解析失败。") from None
    return DocumentText("pdf", "pypdf", "页", tuple(units), total, truncated)


def extract_document(path: Path, name: str, *, max_chars: int = MAX_DOCUMENT_CHARS) -> DocumentText:
    """提取受支持文档的分页正文；不支持的扩展名抛出 DocumentUnsupportedError。"""
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError("max_chars 必须为正整数")
    suffix = Path(name).suffix.casefold()
    if suffix == ".pptx":
        return _pptx(path, max_chars)
    if suffix == ".docx":
        return _docx(path, max_chars)
    if suffix == ".xlsx":
        return _xlsx(path, max_chars)
    if suffix == ".pdf":
        return _pdf(path, max_chars)
    raise DocumentUnsupportedError("该文档格式暂不支持提取正文。")


def read_document_image(path: Path, image: DocumentImage) -> bytes:
    """读取 extract_document 返回的单张内嵌图片原始字节（有界）。"""
    archive, names = _open_ooxml(path)
    try:
        if image.member not in names or not _image_member(image.member, names):
            raise DocumentTextError("文档内嵌图片不可用。")
        return _read_entry(archive, image.member, MAX_IMAGE_ENTRY_BYTES)
    except MediaFeatureError:
        raise DocumentTextError("文档内嵌图片不可用。") from None
    finally:
        archive.close()


def strip_unit_headers(text: str) -> str:
    """生成摘要/标签时去掉“【第 n 页 / 共 m 页】”等分页标记。"""
    return re.sub(r"(?m)^【[^】\n]{1,60}】$\n?", "", text)


__all__ = [
    "DOCUMENT_EXTENSIONS",
    "DocumentImage",
    "DocumentText",
    "DocumentTextError",
    "DocumentUnsupportedError",
    "extract_document",
    "read_document_image",
    "strip_unit_headers",
]
