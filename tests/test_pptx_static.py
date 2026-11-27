"""Offline, temporary UTF-8 fixtures. Never uses real user files or providers."""
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from office_fixtures import build_pptx, build_pdf, knowledge_response, _rels
from sjtu_learning_assistant.document_text import DocumentTextError
from sjtu_learning_assistant.knowledge_documents import DocumentCompiler, digest
from sjtu_learning_assistant.pptx_static import extract_static_pptx
from sjtu_learning_assistant.segment_recovery import AuthorizedSegmentRecovery

class StaticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source, self.target = self.root / 'source', self.root / 'target'
        self.source.mkdir()
        self.target.mkdir()
        self.path, self.calls = self.source / '静态.pptx', list()
        self.fixture()
    def fixture(self, extras=None, blank=False):
        build_pptx(self.path)
        with zipfile.ZipFile(self.path) as archive:
            members = dict((n, archive.read(n)) for n in archive.namelist())
        members.update(dict.fromkeys(('ppt/slides/_rels/slide1.xml.rels',), _rels(
            ('rId5', 'image', '../media/image1.png'), ('rId6', 'notesSlide', '../notesSlides/notesSlide1.xml'),
            ('chart', 'chart', '../charts/chart1.xml'), ('ole', 'oleObject', '../embeddings/object.bin'),
            ('external', 'image', 'https://unopened.invalid/remote.png'), external=('external',))))
        members.update(dict.fromkeys(('ppt/embeddings/object.bin',), b'NEVER OPEN OLE'))
        members.update(dict.fromkeys(('ppt/charts/chart1.xml',), '<chart><numRef><f>FORMULA_NOT_EVIDENCE</f><numCache><pt idx="0"><v>17.5</v></pt></numCache></numRef></chart>'))
        if blank:
            members.update(dict.fromkeys(('ppt/slides/slide2.xml',), '<slide show="0"/>'))
        members.update(extras or dict())
        with zipfile.ZipFile(self.path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, body in members.items():
                archive.writestr(name, body)
    def runner(self, model, prompt, content):
        self.assertIsInstance(content, str)
        self.assertNotIn('FORMULA_NOT_EVIDENCE', content)
        self.assertNotIn('https://unopened.invalid', content)
        self.calls.append((model, prompt, content))
        return knowledge_response(model, prompt, content)
    def test_all_pages_notes_tables_cached_chart_no_payload_reads(self):
        before, original_open = self.path.read_bytes(), zipfile.ZipFile.open
        def spy(archive, name, mode='r', pwd=None, force_zip64=False):
            self.assertNotIn('embeddings/', getattr(name, 'filename', name))
            return original_open(archive, name, mode, pwd, force_zip64=force_zip64)
        with patch.object(zipfile.ZipFile, 'open', spy), patch('subprocess.run', side_effect=AssertionError('no execution')):
            static = extract_static_pptx(self.path)
        self.assertTrue(static.complete)
        self.assertEqual(list((1, 2)), static.inventory.get('covered_pages'))
        self.assertTrue(static.inventory.get('pages').__getitem__(0).get('hidden'))
        evidence = static.pages.__getitem__(1).get('source_text')
        for expected in ('弹性', '讲解重点', '17.5', 'external_assets_unopened', 'embedded_objects_unopened'):
            self.assertIn(expected, evidence)
        self.assertNotIn('FORMULA_NOT_EVIDENCE', evidence)
        self.assertTrue(static.previews)
        self.assertEqual(before, self.path.read_bytes())
    def test_static_summary_review_no_renderer_http_and_resume_zero_calls(self):
        before = digest(self.path.read_bytes())
        with patch('sjtu_learning_assistant.knowledge_documents.rendered_pdf', side_effect=AssertionError('no renderer')), patch('httpx.Client', side_effect=AssertionError('no HTTP')):
            note = DocumentCompiler(self.runner, threading.Event(), static_pptx=True).compile(self.source, self.path, self.target)
            count = len(self.calls)
            same = DocumentCompiler(self.runner, threading.Event(), static_pptx=True).compile(self.source, self.path, self.target)
        self.assertEqual(note, same)
        self.assertEqual(count, len(self.calls))
        self.assertTrue(any('来源审校员' in prompt for _, prompt, _ in self.calls))
        body = note.read_text(encoding='utf-8')
        self.assertIn('quality_status: static-source-reviewed', body)
        self.assertIn('不是原件整页视觉复核', body)
        self.assertLessEqual(len(body.split('---\n\n', 1).pop()), 5000)
        report = json.loads((note.parent / 'review.json').read_text(encoding='utf-8'))
        self.assertEqual(list((1, 2)), report.get('covered_pages'))
        self.assertFalse(report.get('static_inventory').get('original_rendered'))
        self.assertEqual(before, digest(self.path.read_bytes()))
    def test_incomplete_static_checkpoint_existing_png_resumes_without_decoding_or_requests(self):
        from sjtu_learning_assistant.knowledge_documents import CompilationDeferred
        source_before = self.path.read_bytes()
        with patch('httpx.Client', side_effect=AssertionError('no HTTP')), patch(
                'sjtu_learning_assistant.knowledge_documents.rendered_pdf', side_effect=AssertionError('no renderer')):
            with patch('sjtu_learning_assistant.knowledge_documents.SummaryCompiler.compile',
                       side_effect=CompilationDeferred('saved evidence; no request')):
                with self.assertRaises(CompilationDeferred):
                    DocumentCompiler(self.runner, threading.Event(), static_pptx=True).compile(self.source, self.path, self.target)
            manifest_path = next(self.target.rglob('manifest.json'))
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            self.assertNotEqual('completed', manifest.get('status'))
            previews = [name for name in manifest['outputs'] if name.startswith('static-previews/')]
            self.assertTrue(previews)
            before = {name: (manifest_path.parent / name).read_bytes() for name in manifest['outputs']}
            self.assertTrue(any(payload.startswith(b'\x89PNG') for payload in before.values()))
            result = ('# Static Summary\n\nSource-backed definitions and conditions remain tied to the two physical source pages.\n',
                      dict(abstract='Static source', tags=[], synonyms=[]),
                      dict(reviews_sampled_out=0, covered_pages=[1, 2], checks=[dict(approved=True, issues=[])]))
            with patch('sjtu_learning_assistant.knowledge_documents.SummaryCompiler.compile', return_value=result) as compile_summary:
                note = DocumentCompiler(self.runner, threading.Event(), static_pptx=True).compile(self.source, self.path, self.target)
            compile_summary.assert_called_once()
        self.assertTrue(note.is_file())
        self.assertEqual([], self.calls)
        self.assertEqual(source_before, self.path.read_bytes())
        for name, payload in before.items():
            self.assertEqual(payload, (manifest_path.parent / name).read_bytes())
        self.assertEqual('completed', json.loads(manifest_path.read_text(encoding='utf-8'))['status'])

    def test_rejected_review_not_approved(self):
        def reject(model, prompt, content):
            if '来源审校员' in prompt:
                return json.dumps(dict(approved=False, issues=list(('静态来源不支持结论',))))
            return self.runner(model, prompt, content)
        with self.assertRaises(DocumentTextError):
            DocumentCompiler(reject, threading.Event(), static_pptx=True).compile(self.source, self.path, self.target)
        self.assertFalse(list(self.target.rglob('document.md')))
        self.assertTrue(list(self.target.rglob('source-basis.md')))
    def test_blank_hidden_page_not_omitted(self):
        self.fixture(blank=True)
        static = extract_static_pptx(self.path)
        self.assertEqual(2, len(static.pages))
        self.assertIn('no_static_text', static.pages.__getitem__(0).get('gaps'))
    def test_dtd_zipbomb_traversal_missing_slide_rejected(self):
        dtd = '<!DOCTYPE x ' + chr(91) + '<!ENTITY e "bad">' + chr(93) + '><slide>&e;</slide>'
        cases = (dict.fromkeys(('ppt/slides/slide2.xml',), dtd), dict.fromkeys(('../escape.xml',), 'bad'),
            dict.fromkeys(('ppt/embeddings/bomb.bin',), bytes(2097152)), dict.fromkeys(('ppt/presentation.xml',), '<presentation/>'),
            dict.fromkeys(('ppt/slides/_rels/slide1.xml.rels',), _rels(('bad', 'image', '../../../escape.png'))))
        for extras in cases:
            with self.subTest(case=next(iter(extras))):
                self.fixture(extras)
                with self.assertRaises(DocumentTextError):
                    extract_static_pptx(self.path)
    def test_macro_unknown_source_only_no_requests(self):
        for extras in (dict.fromkeys(('ppt/vbaProject.bin',), b'NO OPEN'), dict.fromkeys(('ppt/slides/slide2.xml',), '<slide><AlternateContent/></slide>')):
            self.fixture(extras)
            with patch('sjtu_learning_assistant.knowledge_documents.rendered_pdf', side_effect=AssertionError('no renderer')):
                note = DocumentCompiler(self.runner, threading.Event(), static_pptx=True).compile(self.source, self.path, self.target)
            self.assertIn('quality_status: source-only', note.read_text(encoding='utf-8'))
        self.assertEqual(list(), self.calls)
    def test_basis_and_old_revision_protected(self):
        old = DocumentCompiler(self.runner, threading.Event()).compile(self.source, self.path, self.target)
        old_bytes = old.read_bytes()
        note = DocumentCompiler(self.runner, threading.Event(), static_pptx=True).compile(self.source, self.path, self.target)
        self.assertNotEqual(old.parent, note.parent)
        self.assertEqual(old_bytes, old.read_bytes())
        basis = note.parent / 'source-basis.md'
        basis.write_text('人工编辑', encoding='utf-8')
        count = len(self.calls)
        with self.assertRaises(DocumentTextError):
            DocumentCompiler(self.runner, threading.Event(), static_pptx=True).compile(self.source, self.path, self.target)
        self.assertEqual('人工编辑', basis.read_text(encoding='utf-8'))
        self.assertEqual(count, len(self.calls))

    def test_optional_recovery_requires_transport_and_binds_stable_source(self):
        from types import SimpleNamespace
        with self.assertRaisesRegex(ValueError, 'single-request'):
            DocumentCompiler(self.runner, threading.Event(), authorized_recovery=SimpleNamespace(runner=None))
        self.assertEqual(list(), self.calls)
        path = build_pdf(self.source / 'ordinary.pdf', ('Definitions and conditions for local source evidence.', 'More source evidence with clear explanatory conditions.'))
        raw_calls = list()
        def transport(model, prompt, content):
            raw_calls.append(prompt)
            return knowledge_response(model, prompt, content)
        with self.assertRaises(DocumentTextError):
            DocumentCompiler(transport, threading.Event(), text_first=True, max_calls=1).compile(self.source, path, self.target)
        sid = digest(path.relative_to(self.source).as_posix().encode()).__getitem__(slice(16))
        ledger = self.root / 'ledger.json'
        ledger.write_text(json.dumps(dict(requests=40, limit=64, results=list())), encoding='utf-8')
        recovery = AuthorizedSegmentRecovery(ledger, scope='stable-operation', max_calls=10,
            registrations=dict.fromkeys((sid,), list(('part-0',))), allow_ai=True, runner=transport)
        with patch.object(DocumentCompiler, '_request', side_effect=AssertionError('hidden request fallback')):
            compiler = DocumentCompiler(lambda: None, threading.Event(), authorized_recovery=recovery)
            note = compiler.compile(self.source, path, self.target)
        self.assertIn('source-reviewed', note.read_text(encoding='utf-8'))
        self.assertLessEqual(json.loads(ledger.read_text(encoding='utf-8')).get('requests') - 40, 10)
        self.assertEqual(0, compiler.calls)
        self.assertEqual(list(), self.calls)

class PresentationSectionCompatibilityTests(unittest.TestCase):
    def test_section_catalog_ids_are_not_physical_slide_entries(self):
        import xml.etree.ElementTree as ET
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "sectioned.pptx"
            build_pptx(path)
            with zipfile.ZipFile(path) as archive:
                members = dict((name, archive.read(name)) for name in archive.namelist())
            members.update(dict.fromkeys(("ppt/slides/_rels/slide1.xml.rels",), _rels(
                ("rId5", "image", "../media/image1.png"),
                ("rId6", "notesSlide", "../notesSlides/notesSlide1.xml"))))
            presentation = ET.fromstring(members.get("ppt/presentation.xml"))
            section = ET.SubElement(presentation, "sections")
            ET.SubElement(section, "sldId", dict(id="17"))
            members.update(dict.fromkeys(("ppt/presentation.xml",), ET.tostring(presentation)))
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, body in members.items():
                    archive.writestr(name, body)
            result = extract_static_pptx(path)
            self.assertTrue(result.complete)
            self.assertEqual(len(result.pages), 2)
