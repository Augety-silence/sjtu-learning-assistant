"""在内存中构造最小 OOXML/PDF 测试样本；不依赖真实课程数据。"""

from __future__ import annotations

import io
import random
import zipfile
from pathlib import Path

from PIL import Image

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def noisy_png(width: int = 72, height: int = 72, seed: int = 7) -> bytes:
    """随机噪声 PNG 不可压缩，保证超过“图标”体积下限。"""
    generator = random.Random(seed)
    image = Image.frombytes(
        "RGB", (width, height), bytes(generator.randrange(256) for _ in range(width * height * 3))
    )
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _rels(*items: tuple[str, str, str], external: tuple[str, ...] = ()) -> str:
    body = "".join(
        f'<Relationship Id="{rid}" Type="{REL}/{kind}" Target="{target}"'
        + (' TargetMode="External"' if rid in external else "")
        + "/>"
        for rid, kind, target in items
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG}">{body}</Relationships>'


def _slide(body: str, *, hidden: bool = False) -> str:
    show = ' show="0"' if hidden else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"{show}>'
        f"<p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>"
    )


def _text_shape(*paragraphs: str, placeholder: str | None = None) -> str:
    ph = f'<p:nvSpPr><p:nvPr><p:ph type="{placeholder}"/></p:nvPr></p:nvSpPr>' if placeholder else ""
    runs = "".join(f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p>" for text in paragraphs)
    return f"<p:sp>{ph}<p:txBody>{runs}</p:txBody></p:sp>"


def _picture(rid: str, description: str) -> str:
    return (
        f'<p:pic><p:nvPicPr><p:cNvPr id="4" name="Picture" descr="{description}"/></p:nvPicPr>'
        f'<p:blipFill><a:blip r:embed="{rid}"/></p:blipFill></p:pic>'
    )


def build_pptx(path: Path, *, extra: dict[str, bytes | str] | None = None) -> Path:
    """两页课件：文件名顺序与放映顺序相反；第 2 页含表格、插图、备注。"""
    content_slide = _slide(
        _text_shape("第一章 导论", "需求与供给")
        + '<p:graphicFrame><a:graphic><a:graphicData><a:tbl>'
        "<a:tr><a:tc><a:txBody><a:p><a:r><a:t>概念</a:t></a:r></a:p></a:txBody></a:tc>"
        "<a:tc><a:txBody><a:p><a:r><a:t>定义</a:t></a:r></a:p></a:txBody></a:tc></a:tr>"
        "<a:tr><a:tc><a:txBody><a:p><a:r><a:t>弹性</a:t></a:r></a:p></a:txBody></a:tc>"
        "<a:tc><a:txBody><a:p><a:r><a:t>价格变动的敏感度</a:t></a:r></a:p></a:txBody></a:tc></a:tr>"
        "</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
        + _picture("rId5", "供需曲线示意图")
        + _picture("rId7", "logo.png")
    )
    notes = (
        f'<?xml version="1.0" encoding="UTF-8"?><p:notes xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
        + _text_shape("讲解重点：需求弹性", placeholder="body")
        + _text_shape("2", placeholder="sldNum")
        + "</p:spTree></p:cSld></p:notes>"
    )
    members: dict[str, bytes | str] = {
        "[Content_Types].xml": "<Types/>",
        "ppt/presentation.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:p="{P}" xmlns:r="{R}">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId3"/><p:sldId id="257" r:id="rId2"/></p:sldIdLst>'
            "</p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": _rels(
            ("rId2", "slide", "slides/slide1.xml"), ("rId3", "slide", "slides/slide2.xml")
        ),
        "ppt/slides/slide1.xml": content_slide,
        "ppt/slides/_rels/slide1.xml.rels": _rels(
            ("rId5", "image", "../media/image1.png"),
            ("rId6", "notesSlide", "../notesSlides/notesSlide1.xml"),
            ("rId7", "image", "../media/tiny.png"),
            ("rId8", "image", "../../../outside.png"),
            ("rId9", "hyperlink", "https://example.invalid/"),
            external=("rId9",),
        ),
        "ppt/slides/slide2.xml": _slide(_text_shape("封面", "课程介绍"), hidden=True),
        "ppt/notesSlides/notesSlide1.xml": notes,
        "ppt/media/image1.png": noisy_png(),
        "ppt/media/tiny.png": noisy_png(4, 4),
    }
    members.update(extra or {})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def build_docx(path: Path) -> Path:
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}"><w:body>'
        "<w:p><w:r><w:t>实验报告要求</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>分值</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "<w:p><w:r><w:t>提交截止：第八周</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
    return path


def build_xlsx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{S}"><sheets><sheet name="成绩" sheetId="1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{S}"><si><t>章节</t></si><si><t>权重</t></si></sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{S}"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>期中</t></is></c><c r="B2"><v>30</v></c></row>'
            "</sheetData></worksheet>",
        )
    return path


def build_pdf(path: Path, pages: tuple[str, ...] = ("Lecture One", "Lecture Two")) -> Path:
    """手写带正确 xref 的最小 PDF（Helvetica ASCII 文本）。"""
    objects: list[bytes] = []
    count = len(pages)
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(count))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode())
    font_id = 3 + count * 2
    for index, text in enumerate(pages):
        content_id = 4 + index * 2
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents {content_id} 0 R "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode()
        )
        stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(output))
    return path
