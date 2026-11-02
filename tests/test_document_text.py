from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from office_fixtures import build_docx, build_pdf, build_pptx, build_xlsx, noisy_png

from sjtu_learning_assistant.document_text import (
    DocumentTextError,
    DocumentUnsupportedError,
    extract_document,
    read_document_image,
    strip_unit_headers,
)


class DocumentTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_pptx_uses_presentation_order_and_keeps_tables_alt_text_and_notes(self) -> None:
        path = build_pptx(self.root / "课件.pptx")

        document = extract_document(path, "课件.pptx")

        self.assertEqual(("pptx", "页", 2, 2), (document.kind, document.unit_label, document.unit_count, len(document.units)))
        # sldIdLst 把 slide2.xml 排在第一页；按文件名排序会把内容页错放到前面。
        self.assertTrue(document.units[0].startswith("【第 1 页 / 共 2 页，隐藏页】\n封面"))
        second = document.units[1]
        self.assertTrue(second.startswith("【第 2 页 / 共 2 页】\n第一章 导论\n需求与供给"))
        self.assertIn("[表格]\n概念 | 定义\n弹性 | 价格变动的敏感度", second)
        self.assertIn("[图片说明] 供需曲线示意图", second)
        self.assertNotIn("logo.png", second)
        self.assertIn("[演讲者备注]\n讲解重点：需求弹性", second)
        self.assertFalse(document.truncated)
        # 只有足够大的本地图片成为多模态候选；越界、外链与图标被忽略。
        self.assertEqual([(2, "ppt/media/image1.png")], [(image.unit, image.member) for image in document.images])
        self.assertEqual(noisy_png(), read_document_image(path, document.images[0]))
        self.assertNotIn("【", strip_unit_headers(document.text).splitlines()[0])

    def test_template_image_repeated_on_many_slides_is_not_a_vision_candidate(self) -> None:
        slides = {}
        rels = []
        ids = []
        for number in range(1, 4):
            slides[f"ppt/slides/slide{number}.xml"] = (
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<p:cSld><p:spTree><p:pic><p:blipFill><a:blip r:embed="rIdLogo"/></p:blipFill></p:pic>'
                f"<p:sp><p:txBody><a:p><a:r><a:t>第{number}页</a:t></a:r></a:p></p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:sld>"
            )
            slides[f"ppt/slides/_rels/slide{number}.xml.rels"] = (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdLogo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                'Target="../media/logo.png"/></Relationships>'
            )
            rels.append(
                f'<Relationship Id="rId{number}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
                f'Target="slides/slide{number}.xml"/>'
            )
            ids.append(f'<p:sldId id="{255 + number}" r:id="rId{number}"/>')
        path = self.root / "template.pptx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "ppt/presentation.xml",
                '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f"<p:sldIdLst>{''.join(ids)}</p:sldIdLst></p:presentation>",
            )
            archive.writestr(
                "ppt/_rels/presentation.xml.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(rels)
                + "</Relationships>",
            )
            for name, payload in slides.items():
                archive.writestr(name, payload)
            archive.writestr("ppt/media/logo.png", noisy_png())

        document = extract_document(path, "template.pptx")

        self.assertEqual(3, document.image_reference_count)
        self.assertEqual((), document.images)

    def test_dtd_macro_and_unknown_formats_are_rejected(self) -> None:
        dtd = build_pptx(
            self.root / "dtd.pptx",
            extra={"ppt/slides/slide2.xml": '<!DOCTYPE x [<!ENTITY a "b">]><p:sld xmlns:p="p"/>'},
        )
        with self.assertRaises(DocumentTextError):
            extract_document(dtd, "dtd.pptx")
        macro = build_pptx(self.root / "macro.pptx", extra={"ppt/vbaProject.bin": b"macro"})
        with self.assertRaises(DocumentUnsupportedError):
            extract_document(macro, "macro.pptx")
        broken = self.root / "broken.pptx"
        broken.write_bytes(b"not a zip")
        with self.assertRaises(DocumentTextError):
            extract_document(broken, "broken.pptx")
        with self.assertRaises(DocumentUnsupportedError):
            extract_document(broken, "legacy.ppt")

    def test_character_budget_marks_truncation(self) -> None:
        path = build_pptx(self.root / "budget.pptx")
        document = extract_document(path, "budget.pptx", max_chars=30)
        self.assertTrue(document.truncated)
        self.assertLessEqual(len(document.text), 30)

    def test_docx_xlsx_and_pdf_are_paginated(self) -> None:
        docx = extract_document(build_docx(self.root / "要求.docx"), "要求.docx")
        self.assertEqual("段", docx.unit_label)
        self.assertIn("实验报告要求\n[表格]\n项目 | 分值\n提交截止：第八周", docx.text)

        xlsx = extract_document(build_xlsx(self.root / "权重.xlsx"), "权重.xlsx")
        self.assertEqual(("工作表", 1), (xlsx.unit_label, xlsx.unit_count))
        self.assertIn("【工作表 1 / 共 1：成绩】\n章节 | 权重\n期中 | 30", xlsx.text)

        pdf = extract_document(build_pdf(self.root / "讲义.pdf"), "讲义.pdf")
        self.assertEqual(("pdf", 2), (pdf.kind, pdf.unit_count))
        self.assertIn("【第 1 页 / 共 2 页】\nLecture One", pdf.units[0])
        self.assertIn("【第 2 页 / 共 2 页】\nLecture Two", pdf.units[1])

        broken = self.root / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4\nbroken")
        with self.assertRaises(DocumentTextError):
            extract_document(broken, "broken.pdf")


if __name__ == "__main__":
    unittest.main()
