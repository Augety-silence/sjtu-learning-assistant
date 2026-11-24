from types import SimpleNamespace
import sys

from sjtu_learning_assistant import knowledge_documents as kd


def _fake_pdfium(pages):
    class FakeDoc:
        def __len__(self):
            return pages

    return SimpleNamespace(PdfDocument=lambda value: FakeDoc())


def test_non_courseware_large_pdf_is_skipped(tmp_path, monkeypatch):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "pypdfium2", _fake_pdfium(201))
    assert "oversize_reference_over_pages" in kd.skipped_active_content(path)


def test_courseware_large_pdf_is_not_skipped(tmp_path, monkeypatch):
    folder = tmp_path / "课件"
    folder.mkdir()
    path = folder / "slides.pdf"
    path.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "pypdfium2", _fake_pdfium(201))
    assert kd.skipped_active_content(path) == []


def test_oversize_bytes_for_non_courseware(tmp_path, monkeypatch):
    monkeypatch.setattr(kd, "MAX_REFERENCE_BYTES", 10)
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 20)
    assert "oversize_reference_over_50MiB" in kd._oversize_reference_reasons(path)


def test_courseware_byte_limit_exempt(tmp_path, monkeypatch):
    monkeypatch.setattr(kd, "MAX_REFERENCE_BYTES", 10)
    folder = tmp_path / "课件"
    folder.mkdir()
    path = folder / "big.bin"
    path.write_bytes(b"x" * 20)
    assert kd._oversize_reference_reasons(path) == []
