import unittest
from unittest.mock import patch
from types import SimpleNamespace
from sjtu_learning_assistant.pdf_preview_search import pdf_preview_search


class PdfPreviewSearchTests(unittest.TestCase):
    def test_retains_real_page_numbers_and_does_not_break_malformed_preview(self):
        pages = (SimpleNamespace(extract_text=lambda: ""), SimpleNamespace(extract_text=lambda: "中文对偶"))
        with patch("pypdf.PdfReader", return_value=SimpleNamespace(is_encrypted=False, pages=pages)):
            result = pdf_preview_search(b"validated preview")
        self.assertEqual(result["search_pages"], [{"page": 2, "text": "中文对偶"}])
        self.assertEqual(pdf_preview_search(b"not a pdf")["search_pages"], [])

    def test_text_budget_and_encrypted_documents_are_explicit(self):
        pages = (SimpleNamespace(extract_text=lambda: "a" * 400_001),)
        with patch("pypdf.PdfReader", return_value=SimpleNamespace(is_encrypted=False, pages=pages)):
            result = pdf_preview_search(b"validated preview")
        self.assertEqual(len(result["search_pages"][0]["text"]), 400_000)
        self.assertIn("40 万字", result["search_note"])
        with patch("pypdf.PdfReader", return_value=SimpleNamespace(is_encrypted=True, decrypt=lambda _: 0)):
            result = pdf_preview_search(b"encrypted")
        self.assertEqual(result["search_pages"], [])
        self.assertIn("加密", result["search_note"])
