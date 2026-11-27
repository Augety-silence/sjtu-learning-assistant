"""Synthetic archive/CLI protection tests, with no real home/keychain/model calls."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from office_fixtures import build_pdf
from sjtu_learning_assistant.knowledge_documents import CONTRACT, VISION_MODEL, digest
from sjtu_learning_assistant.segment_recovery import RecoveryBlocked

spec = importlib.util.spec_from_file_location('selected_repair_cli', Path(__file__).resolve().parents[1] / 'scripts/repair_selected_segments_local.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class SelectedSegmentsCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'archive'
        self.source.mkdir()
        self.original = build_pdf(self.source / 'lecture.pdf', ('source page one',))
        self.target = self.source / 'Knowledge Markdown'
        self.relative = 'lecture.pdf'
        self.source_hash = digest(self.original.read_bytes())
        self.source_id = digest(self.relative.encode())[:16]
        revision = digest((self.source_hash + CONTRACT + VISION_MODEL).encode())[:16]
        self.directory = self.target / '90_Inbox/Documents' / self.source_id / revision
        self.directory.mkdir(parents=True)
        self.mirror = self.target / 'Courses/lecture.pdf.md'
        self.mirror.parent.mkdir(parents=True)
        self.mirror.write_text('original registered mirror', encoding="utf-8")
        self.marker = self.target / '99_System/Course-Sync.json'
        self.marker.parent.mkdir(parents=True)
        self.item = {'status': 'failed', 'auto_paused': True, 'attempts': 4,
                     'automatic_attempts': 2, 'content_attempts': 2,
                     'error': 'original failure', 'mirror_hash': digest(self.mirror.read_bytes())}
        self.state = {'version': 1, 'source_root': str(self.source), 'items': {self.relative: self.item},
                      'quota': {'day': 'past-day', 'used': 123}}
        self.marker.write_text(json.dumps(self.state), encoding="utf-8")
        page = self.directory / 'pages/page-0001.md'
        page.parent.mkdir()
        page.write_text('complete saved page', encoding="utf-8")
        failed = self.directory / 'summary/prefix/part-0-node.json-resume-v1-identity.json'
        failed.parent.mkdir(parents=True)
        failed.write_text(json.dumps({'error': '精炼总结缺少有效来源引用。', 'draft': '{"markdown":"old"}', 'step': 4}), encoding="utf-8")
        self.note = self.directory / 'document.md'
        self.note.write_text('# accepted new summary\n\nsource-backed summary [课件 p.1]\n', encoding="utf-8")
        outputs = {p.relative_to(self.directory).as_posix(): digest(p.read_bytes()) for p in (page, failed, self.note)}
        manifest = {'version': 1, 'contract': CONTRACT, 'model': VISION_MODEL,
                    'source': self.relative, 'source_hash': self.source_hash,
                    'pages': {'1': 'pages/page-0001.md'}, 'outputs': outputs}
        self.manifest_path = self.directory / 'manifest.json'
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.ledger = self.root / 'requests.json'
        self.ledger.write_text(json.dumps({'requests': 40, 'limit': 64, 'results': []}), encoding="utf-8")
        self.settings = SimpleNamespace(ai_enabled=False, archive_root=str(self.source))
        self.args = argparse.Namespace(file=[self.relative], scope='stable-user-operation/10', ledger=self.ledger,
            allow_ai=False, offline=True, retry_unknown=0, diagnose=False)
        self.invocations = []
        owner = self
        class FakeCompiler:
            def __init__(self, runner, cancel, *, authorized_recovery=None, **kwargs):
                owner.invocations.append((authorized_recovery, kwargs))
            def compile(self, source, original, target):
                return owner.note
        self.compiler = FakeCompiler

    def run_cli(self, **kwargs):
        return cli.run(self.args, settings_loader=lambda: self.settings, compiler_class=self.compiler,
                       transport_factory=lambda *args: (_ for _ in ()).throw(AssertionError('keychain/model forbidden')), **kwargs)

    def test_fixture_and_offline_recovery_do_not_depend_on_default_codepage(self):
        read, write = Path.read_text, Path.write_text
        def cp1252_read(path, *args, **kwargs):
            if not args:
                kwargs.setdefault('encoding', 'cp1252')
            return read(path, *args, **kwargs)
        def cp1252_write(path, text, *args, **kwargs):
            if not args:
                kwargs.setdefault('encoding', 'cp1252')
            return write(path, text, *args, **kwargs)
        case = SelectedSegmentsCliTests('test_success_publishes_existing_sync_path_preserves_counts_and_backup')
        try:
            with patch.object(Path, 'read_text', cp1252_read), patch.object(Path, 'write_text', cp1252_write):
                case.setUp()
                case.test_success_publishes_existing_sync_path_preserves_counts_and_backup()
        finally:
            case.doCleanups()

    def test_offline_is_default_and_allow_ai_is_exclusive(self):
        args = cli.parser().parse_args(['--file', 'a.pptx', '--scope', 'id/10', '--ledger', str(self.ledger)])
        self.assertFalse(args.allow_ai)
        with self.assertRaises(SystemExit):
            cli.parser().parse_args(['--file', 'a.pptx', '--scope', 'id/10', '--ledger', str(self.ledger), '--offline', '--allow-ai'])

    def test_background_pause_required_before_any_write(self):
        self.settings.ai_enabled = True
        before = self.marker.read_bytes(), self.ledger.read_bytes(), self.mirror.read_bytes()
        with self.assertRaisesRegex(RecoveryBlocked, 'Pause and drain'):
            self.run_cli()
        self.assertEqual(before, (self.marker.read_bytes(), self.ledger.read_bytes(), self.mirror.read_bytes()))
        self.assertFalse((self.target / '99_System/Document-Compiler.lock').exists())

    def test_diagnosis_readonly_and_no_authorization_or_provider_calls(self):
        self.args.diagnose = True
        before = self.marker.read_bytes(), self.ledger.read_bytes(), self.mirror.read_bytes()
        result = self.run_cli()
        self.assertEqual('diagnosed', result['status'])
        self.assertEqual(before, (self.marker.read_bytes(), self.ledger.read_bytes(), self.mirror.read_bytes()))
        self.assertEqual([], self.invocations)
        self.assertFalse((self.target / '99_System/Document-Compiler.lock').exists())

    def test_manual_mirror_or_registered_page_changes_stop_before_compiler(self):
        for path in (self.mirror, self.directory / 'pages/page-0001.md'):
            original = path.read_bytes()
            path.write_text('manual edits', encoding="utf-8")
            with self.assertRaises(RecoveryBlocked):
                self.run_cli()
            self.assertEqual('manual edits', path.read_text(encoding="utf-8"))
            self.assertEqual([], self.invocations)
            path.write_bytes(original)

    def test_success_publishes_existing_sync_path_preserves_counts_and_backup(self):
        with patch.object(cli.CourseMarkdownSync, '_publish_compiled_note', wraps=cli.CourseMarkdownSync._publish_compiled_note) as publish:
            result = self.run_cli()
            self.assertEqual('completed', result['status'])
            publish.assert_called_once()
        state = json.loads(self.marker.read_text(encoding="utf-8"))
        item = state['items'][self.relative]
        self.assertEqual((4, 2, 2), (item['attempts'], item['automatic_attempts'], item['content_attempts']))
        self.assertEqual(self.state['quota'], state['quota'])
        self.assertNotIn('auto_paused', item)
        self.assertEqual('completed', item['status'])
        self.assertEqual(self.source_hash, digest(self.original.read_bytes()))
        self.assertEqual(0, result['authorization']['requests'])
        backups = [p for p in (self.target / '90_Inbox/Previous').rglob('*.md') if p.is_file()]
        self.assertEqual(['original registered mirror'], [p.read_text(encoding="utf-8") for p in backups])
        self.assertIsNotNone(self.invocations[0][0])

    def test_restart_same_operation_skips_completed_note_without_reset_or_overwrite(self):
        first = self.run_cli()
        self.assertEqual('completed', first['status'])
        self.mirror.write_text('manual edit to completed note', encoding="utf-8")
        before = self.marker.read_bytes(), self.ledger.read_bytes(), self.note.read_bytes()
        restarted = self.run_cli()
        self.assertEqual('completed', restarted['status'])
        self.assertTrue(restarted['files'][0]['reused'])
        self.assertEqual(1, len(self.invocations))
        self.assertEqual('manual edit to completed note', self.mirror.read_text(encoding="utf-8"))
        self.assertEqual(before, (self.marker.read_bytes(), self.ledger.read_bytes(), self.note.read_bytes()))
        self.assertEqual(0, restarted['authorization']['requests'])

    def test_manual_change_during_compile_keeps_pause_counts_and_no_publish(self):
        owner = self
        class ChangingCompiler:
            def __init__(self, runner, cancel, *, authorized_recovery=None, **kwargs):
                pass
            def compile(self, *args):
                owner.mirror.write_text('new manual edit during compilation', encoding="utf-8")
                return owner.note
        self.compiler = ChangingCompiler
        before = self.marker.read_bytes()
        result = self.run_cli()
        self.assertEqual('preserved', result['status'])
        self.assertEqual('new manual edit during compilation', self.mirror.read_text(encoding="utf-8"))
        self.assertEqual(before, self.marker.read_bytes())
        self.assertTrue(json.loads(self.marker.read_text(encoding="utf-8"))['items'][self.relative]['auto_paused'])

    def test_parent_absolute_unregistered_and_changed_source_are_not_authorized(self):
        for relative in ('../outside.pdf', str(self.original), 'unregistered.pdf'):
            self.args.file = [relative]
            with self.assertRaises(RecoveryBlocked):
                self.run_cli()
        self.args.file = [self.relative]
        self.original.write_bytes(b'changed source')
        with self.assertRaises(Exception):
            self.run_cli()
        self.assertEqual([], self.invocations)


if __name__ == '__main__':
    unittest.main()
