from __future__ import annotations

import io
import json
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from sjtu_learning_assistant import media_features
from sjtu_learning_assistant.media_features import (
    MediaFeatureError,
    MediaFeatureService,
    PreviewLimits,
    UnsafeArchiveError,
    aggregate_course_media,
    detect_file_type,
    merge_pdfs,
    structured_preview,
    video_screenshots_pdf,
)


def make_zip(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


class FileTypeTests(unittest.TestCase):
    def test_detects_signature_container_and_media_metadata(self) -> None:
        docx = make_zip({"word/document.xml": b"<w:document xmlns:w='w'/>"})
        self.assertEqual("docx", detect_file_type("无扩展名", data=docx)["kind"])
        self.assertEqual(
            "pdf", detect_file_type("错误.txt", data=b"%PDF-1.7\n")["kind"]
        )
        self.assertEqual("video", detect_file_type("课程 视频.MP4")["kind"])
        self.assertEqual("audio", detect_file_type("录音", "audio/mpeg")["kind"])
        self.assertEqual("subtitle", detect_file_type("课程 视频.zh-CN.srt")["kind"])

    def test_macro_formats_are_never_previewable(self) -> None:
        detected = detect_file_type("作业.xlsm")
        self.assertEqual("office_macro", detected["kind"])
        self.assertTrue(detected["active_content"])
        self.assertFalse(detected["previewable"])


class ArchivePreviewTests(unittest.TestCase):
    def test_zip_preview_supports_chinese_and_spaces(self) -> None:
        payload = make_zip({"课程 资料/第一 周.txt": "你好".encode()})
        result = structured_preview(payload, "资料 包.zip")
        self.assertEqual("资料 包.zip", result["name"])
        self.assertEqual("课程 资料/第一 周.txt", result["entries"][0]["name"])

    def test_path_traversal_and_windows_paths_are_rejected(self) -> None:
        for name in (
            "../secret.txt",
            "/tmp/secret",
            "C:/secret",
            "folder\\secret.txt",
            "a//b.txt",
        ):
            with self.subTest(name=name), self.assertRaises(UnsafeArchiveError):
                structured_preview(make_zip({name: b"secret"}), "unsafe.zip")

    def test_nested_paths_are_bounded(self) -> None:
        limits = PreviewLimits(max_path_depth=2)
        with self.assertRaisesRegex(UnsafeArchiveError, "过深"):
            structured_preview(make_zip({"a/b/c.txt": b"x"}), "deep.zip", limits=limits)

    def test_zip_bomb_metadata_is_rejected_before_member_read(self) -> None:
        payload = make_zip({"large.txt": b"A" * (2 * 1024 * 1024)})
        limits = PreviewLimits(max_compression_ratio=10)
        with patch.object(
            zipfile.ZipFile, "open", side_effect=AssertionError("must not extract")
        ) as opened:
            with self.assertRaisesRegex(UnsafeArchiveError, "解压炸弹"):
                structured_preview(payload, "bomb.zip", limits=limits)
        opened.assert_not_called()

    def test_declared_total_and_entry_count_are_bounded(self) -> None:
        payload = make_zip({"a.txt": b"a", "b.txt": b"b"})
        with self.assertRaisesRegex(UnsafeArchiveError, "条目数量"):
            structured_preview(
                payload, "many.zip", limits=PreviewLimits(max_archive_entries=1)
            )
        with self.assertRaisesRegex(UnsafeArchiveError, "解压总大小"):
            structured_preview(
                payload, "large.zip", limits=PreviewLimits(max_uncompressed_size=1)
            )

    def test_docx_pptx_and_xlsx_structured_preview(self) -> None:
        docx = make_zip(
            {
                "word/document.xml": (
                    b"<w:document xmlns:w='urn:w'><w:body><w:p><w:r><w:t>\xe8\xaf\xbe\xe7\xa8\x8b \xe8\xb5\x84\xe6\x96\x99</w:t>"
                    b"</w:r></w:p></w:body></w:document>"
                )
            }
        )
        self.assertEqual(
            ["课程 资料"], structured_preview(docx, "课程.docx")["paragraphs"]
        )

        pptx = make_zip(
            {
                "ppt/presentation.xml": b"<p:presentation xmlns:p='urn:p'/>",
                "ppt/slides/slide2.xml": "<p:sld xmlns:p='urn:p' xmlns:a='urn:a'><a:t>第二页</a:t></p:sld>".encode(),
                "ppt/slides/slide1.xml": "<p:sld xmlns:p='urn:p' xmlns:a='urn:a'><a:t>第一页</a:t></p:sld>".encode(),
            }
        )
        self.assertEqual(
            ["第一页", "第二页"],
            [
                slide["text"]
                for slide in structured_preview(pptx, "课件.pptx")["slides"]
            ],
        )

        xlsx = make_zip(
            {
                "xl/workbook.xml": "<workbook xmlns='urn:x'><sheets><sheet name='成绩 单'/></sheets></workbook>".encode(),
                "xl/sharedStrings.xml": "<sst xmlns='urn:x'><si><t>姓名</t></si><si><t>张 三</t></si></sst>".encode(),
                "xl/worksheets/sheet1.xml": b"<worksheet xmlns='urn:x'><sheetData><row r='1'><c r='A1' t='s'><v>0</v></c></row><row r='2'><c r='A2' t='s'><v>1</v></c><c r='B2'><v>100</v></c></row></sheetData></worksheet>",
            }
        )
        sheet = structured_preview(xlsx, "成绩.xlsx")["sheets"][0]
        self.assertEqual("成绩 单", sheet["name"])
        self.assertEqual(["张 三", "100"], sheet["rows"][1])

    def test_active_content_in_office_container_is_rejected(self) -> None:
        payload = make_zip(
            {
                "word/document.xml": b"<w:document xmlns:w='w'/>",
                "word/vbaProject.bin": b"macro",
            }
        )
        with self.assertRaisesRegex(MediaFeatureError, "宏"):
            structured_preview(payload, "renamed.docx")


class NotebookTests(unittest.TestCase):
    def test_ipynb_is_cleaned_to_plain_text_without_rich_or_script_output(self) -> None:
        payload = json.dumps(
            {
                "nbformat": 4,
                "metadata": {"trusted": True},
                "cells": [
                    {
                        "cell_type": "markdown",
                        "source": ["# 标题\n", "<script>alert(1)</script>"],
                        "metadata": {"trusted": True},
                    },
                    {
                        "cell_type": "code",
                        "source": "print('运行我')",
                        "execution_count": 99,
                        "outputs": [
                            {
                                "output_type": "display_data",
                                "data": {
                                    "text/html": "<img src=x onerror=alert(1)>",
                                    "application/javascript": "alert(2)",
                                    "text/plain": "<safe text>",
                                    "image/png": "AAAA",
                                },
                            }
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ).encode()
        result = structured_preview(payload, "实验 笔记.ipynb")
        self.assertFalse(result["trusted"])
        self.assertNotIn("metadata", result["cells"][0])
        self.assertEqual(
            "# 标题\n&lt;script&gt;alert(1)&lt;/script&gt;",
            result["cells"][0]["source"],
        )
        code = result["cells"][1]
        self.assertNotIn("execution_count", code)
        self.assertEqual("&lt;safe text&gt;", code["outputs"][0]["text"])
        self.assertNotIn("text/html", json.dumps(result))
        self.assertNotIn("application/javascript", json.dumps(result))

    def test_notebook_cell_limit_is_applied(self) -> None:
        payload = json.dumps(
            {"cells": [{"cell_type": "raw", "source": "x"}] * 3}
        ).encode()
        result = structured_preview(
            payload, "x.ipynb", limits=PreviewLimits(max_notebook_cells=2)
        )
        self.assertEqual(2, len(result["cells"]))
        self.assertTrue(result["truncated"])


class MediaAggregationTests(unittest.TestCase):
    def test_course_files_and_raw_metadata_are_filtered_aggregated_and_matched(
        self,
    ) -> None:
        records = [
            SimpleNamespace(
                source_id="video-1",
                display_name="第 1 讲.mp4",
                filename=None,
                content_type="video/mp4",
                size=1024,
                local_path="课程/第 1 讲.mp4",
                cloud_path=None,
                url="https://secret.example/?token=must-not-leak",
                raw_data={},
                is_active=True,
                hidden=False,
            ),
            {
                "source_id": "sub-1",
                "display_name": "第 1 讲.zh-CN.srt",
                "content_type": "application/x-subrip",
                "size": 10,
                "url": "https://secret.example/subtitle?token=must-not-leak",
            },
            {
                "source_id": "audio-1",
                "raw_data": {
                    "display_name": "中文 录音.m4a",
                    "content-type": "audio/mp4",
                },
            },
            {
                "source_id": "document-1",
                "display_name": "讲义.pdf",
                "content_type": "application/pdf",
            },
            {"source_id": "hidden", "display_name": "隐藏.mp4", "hidden": True},
            {"source_id": "inactive", "display_name": "旧.mp4", "is_active": False},
        ]
        result = aggregate_course_media(records)
        self.assertEqual({"video": 1, "audio": 1, "subtitle": 1}, result["counts"])
        video = next(item for item in result["items"] if item["source_id"] == "video-1")
        self.assertEqual("sub-1", video["subtitles"][0]["source_id"])
        self.assertEqual("zh-CN", video["subtitles"][0]["language"])
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("secret.example", rendered)
        self.assertNotIn("local_path", rendered)
        self.assertEqual("dashboard_action", video["playback"]["transport"])

    def test_extra_raw_metadata_and_unmatched_subtitle(self) -> None:
        result = aggregate_course_media(
            [],
            [
                {"id": 12, "filename": "专题.webm", "type": "video/webm"},
                {"id": 13, "filename": "其他.vtt", "type": "text/vtt"},
            ],
        )
        self.assertEqual("12", result["items"][0]["source_id"])
        self.assertEqual("13", result["unmatched_subtitles"][0]["source_id"])

    def test_invalid_identifier_does_not_create_unsafe_action(self) -> None:
        with self.assertRaises(MediaFeatureError):
            MediaFeatureService.safe_download_descriptor(
                {"source_id": "bad\x00id", "filename": "a.mp4"}
            )


class OptionalCapabilityTests(unittest.TestCase):
    def test_pdf_merge_reports_unavailable_without_pypdf(self) -> None:
        with patch.object(
            media_features.importlib.util, "find_spec", return_value=None
        ):
            result = merge_pdfs([b"%PDF-1.4\n"])
        self.assertEqual("unavailable", result["status"])
        self.assertFalse(result["available"])
        self.assertIn("pypdf", result["reason"])
        self.assertNotIn("data", result)

    def test_video_screenshot_reports_all_missing_dependencies(self) -> None:
        with patch.object(
            media_features, "_ffmpeg_executable", return_value=None
        ), patch.object(media_features.importlib.util, "find_spec", return_value=None):
            result = video_screenshots_pdf(b"video")
        self.assertEqual("unavailable", result["status"])
        self.assertIn("ffmpeg", result["reason"])
        self.assertIn("Pillow", result["reason"])
        self.assertNotIn("data", result)

    def test_service_does_not_offer_output_path_parameters(self) -> None:
        with self.assertRaises(TypeError):
            MediaFeatureService.merge_pdfs([], output_path="/tmp/result.pdf")
        with self.assertRaises(TypeError):
            MediaFeatureService.video_screenshots_pdf(
                b"", output_path="/tmp/result.pdf"
            )


if __name__ == "__main__":
    unittest.main()
