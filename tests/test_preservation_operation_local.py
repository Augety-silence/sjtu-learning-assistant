"""Byte-backed temporary protection fixtures: no credentials, models or production writes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/preservation_operation_local.py'
spec = importlib.util.spec_from_file_location('preservation_fixture', SCRIPT)
protection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protection)


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.archive = self.root / 'archive'
        self.support = self.root / 'support'
        self.archive.mkdir()
        self.support.mkdir()
        self.operations = self.support / 'recovery-backups/protection-operations'
        self.original = self.write(self.archive / 'course/original.pdf', b'original PDF bytes')
        self.manual = self.write(self.archive / 'Knowledge Markdown/manual-unregistered.md', b'manual manuscript')
        self.paid = self.write(self.archive / 'Knowledge Markdown/90_Inbox/paid-response.json', b'{"content":"original response"}')
        self.raw = self.write(self.support / 'transcripts/video/raw.vtt', b'original subtitles')
        self.snapshot = self.root / 'old-baseline.json'
        self.snapshot.write_text(json.dumps(dict(protected={str(self.original): protection.file_hash(self.original)})), encoding="utf-8")
        self.old = self.snapshot.read_bytes()

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def capture(self, **kwargs):
        return protection.capture(self.archive, self.support, self.operations, baselines=[self.snapshot], **kwargs)

    def test_windows_capture_keeps_file_flushes_hashes_and_readonly_manifests(self):
        actual_os = protection.os
        fake_os = SimpleNamespace()
        fake_os.__dict__.update(vars(actual_os))
        fake_os.name = 'nt'
        def open_file(path, flags, *args, **kwargs):
            if Path(path).is_dir():
                raise AssertionError('Windows CRT directory open forbidden')
            return actual_os.open(path, flags, *args, **kwargs)
        fake_os.open = open_file
        fake_os.fsync = Mock(wraps=actual_os.fsync)
        with patch.object(protection, 'os', fake_os):
            manifest = self.capture()
            result = protection.verify(manifest)
        self.assertTrue(result.get('verified'))
        self.assertGreater(fake_os.fsync.call_count, 2)
        self.assertEqual(self.old, self.snapshot.read_bytes())
        import stat
        self.assertFalse(manifest.stat().st_mode & stat.S_IWRITE)
        self.assertFalse((manifest.parent / 'manifest.sha256').stat().st_mode & stat.S_IWRITE)

    def test_required_file_fsync_failure_never_returns_successful_capture(self):
        with patch.object(protection.os, 'fsync', side_effect=OSError('synthetic fsync failure')):
            with self.assertRaisesRegex(OSError, 'synthetic fsync failure'):
                self.capture()
        self.assertEqual(self.old, self.snapshot.read_bytes())

    def test_union_captures_original_manual_unregistered_and_paid_bytes_unique_operations(self):
        first = self.capture()
        first_bytes = first.read_bytes()
        second = self.capture()
        self.assertNotEqual(first, second)
        self.assertEqual(first_bytes, first.read_bytes())
        self.assertEqual(self.old, self.snapshot.read_bytes())
        value = json.loads(first.read_text(encoding="utf-8"))
        for path in (self.original, self.manual, self.paid, self.raw):
            record = value['files'][str(path)]
            backup = first.parent / record['backup']
            self.assertEqual(path.read_bytes(), backup.read_bytes())
            self.assertEqual([], record['mutable_fields'])
            self.assertNotEqual(path.stat().st_ino, backup.stat().st_ino)
        self.assertTrue(protection.verify(first)['verified'])

    def test_no_settings_database_keychain_or_credentials_backup(self):
        for name in ('settings.json', 'secrets.json', '.env', 'credentials/provider.json', 'private.key', 'cache.sqlite3'):
            self.write(self.archive / name, b'NEVER BACK UP')
        self.write(self.support / 'settings.json', b'NEVER BACK UP')
        self.write(self.support / 'recovery-backups/databases/app.db', b'NEVER BACK UP')
        manifest = self.capture()
        value = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertFalse(any(b'NEVER BACK UP' in (manifest.parent / record['backup']).read_bytes() for record in value['files'].values()))

    def test_immutable_change_delete_and_backup_tamper_report_all_no_repair(self):
        manifest = self.capture()
        value = json.loads(manifest.read_text(encoding="utf-8"))
        self.manual.write_text('new manual edit', encoding="utf-8")
        self.original.unlink()
        (manifest.parent / value['files'][str(self.paid)]['backup']).write_text('bad backup', encoding="utf-8")
        baseline = manifest.read_bytes()
        result = protection.verify(manifest)
        self.assertFalse(result['verified'])
        self.assertEqual(3, len(result['failures']))
        self.assertEqual('new manual edit', self.manual.read_text(encoding="utf-8"))
        self.assertEqual(baseline, manifest.read_bytes())

    def test_only_explicit_state_fields_mutable_counters_monotonic_and_limit_immutable(self):
        state = self.write(self.archive / 'Knowledge Markdown/99_System/Course-Sync.json',
                           json.dumps(dict(items=dict(a=dict(status='failed', attempts=3)), quota=dict(used=40))).encode())
        ledger = self.write(self.support / 'recovery-backups/requests.json', json.dumps(dict(requests=40, limit=64, results=[])).encode())
        fields = {str(state): ['/items/a/status', '/items/a/attempts'], str(ledger): ['/requests']}
        manifest = self.capture(mutable_fields=fields)
        state.write_text(json.dumps(dict(items=dict(a=dict(status='completed', attempts=4)), quota=dict(used=40))), encoding="utf-8")
        ledger.write_text(json.dumps(dict(requests=41, limit=64, results=[])), encoding="utf-8")
        self.assertTrue(protection.verify(manifest)['verified'])
        ledger.write_text(json.dumps(dict(requests=39, limit=64, results=[])), encoding="utf-8")
        self.assertIn('counter_decreased_or_removed', str(protection.verify(manifest)['failures']))
        ledger.write_text(json.dumps(dict(requests=41, limit=100, results=[])), encoding="utf-8")
        self.assertIn('unapproved_state_field_changed', str(protection.verify(manifest)['failures']))

    def test_whole_ledger_evidence_budget_or_wildcard_allowlist_rejected(self):
        ledger = self.write(self.support / 'recovery-backups/requests.json', b'{"requests":40,"limit":64,"results":[]}')
        for pointer in ('/results', '/limit', '/*', '/'):
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                self.capture(mutable_fields={str(ledger): [pointer]})
        self.assertFalse(self.operations.exists())

    def test_changed_old_baseline_does_not_create_new_baseline(self):
        self.original.write_text('changed original', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, 'old_baseline_changed'):
            self.capture()
        self.assertFalse(self.operations.exists())
        self.assertEqual(self.old, self.snapshot.read_bytes())

    def test_old_snapshot_itself_protected_and_readonly_verification(self):
        manifest = self.capture()
        self.snapshot.write_text('changed baseline', encoding="utf-8")
        before = manifest.read_bytes()
        self.assertFalse(protection.verify(manifest)['verified'])
        self.assertEqual(before, manifest.read_bytes())
        self.assertEqual('changed baseline', self.snapshot.read_text(encoding="utf-8"))

    def test_symlinks_and_scope_escape_fail_before_backup(self):
        (self.archive / 'alias.md').symlink_to(self.manual)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.capture()
        self.assertFalse(self.operations.exists())
        (self.archive / 'alias.md').unlink()
        outside = self.write(self.root / 'outside.md', b'outside artifact')
        self.snapshot.write_text(json.dumps(dict(protected={str(outside): protection.file_hash(outside)})), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, 'outside_artifact_scope'):
            self.capture()

    def test_file_limit_fails_closed_and_no_partial_baseline(self):
        with self.assertRaisesRegex(ValueError, 'file_limit'):
            self.capture(max_files=1)
        self.assertFalse(self.operations.exists())

    def test_capture_source_race_retains_incomplete_operation_not_valid_manifest(self):
        copy_bytes = protection.shutil.copyfileobj
        def race(source, destination, **kwargs):
            copy_bytes(source, destination, **kwargs)
            self.original.write_bytes(b'racing source change')
        with patch.object(protection.shutil, 'copyfileobj', side_effect=race):
            with self.assertRaisesRegex(ValueError, 'artifact_changed'):
                self.capture()
        self.assertFalse(list(self.operations.rglob('manifest.json')))
        self.assertEqual(self.old, self.snapshot.read_bytes())

    def test_parent_traversal_old_manifest_rejected(self):
        outside = self.write(self.root / 'outside.md', b'outside artifact')
        alias = self.archive / '..' / 'outside.md'
        self.snapshot.write_text(json.dumps(dict(protected=dict.fromkeys((str(alias),), protection.file_hash(outside)))), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, 'parent_traversal'):
            self.capture()
        self.assertFalse(self.operations.exists())

    def test_paid_candidates_never_declared_mutable_state(self):
        with self.assertRaisesRegex(ValueError, 'mutable_file_not_protected_json'):
            self.capture(mutable_fields=dict.fromkeys((str(self.paid),), list(('/content',))))
        self.assertFalse(self.operations.exists())

    def test_next_unique_operation_accepts_prior_allowlisted_monotonic_state(self):
        ledger = self.write(self.support / 'recovery-backups/requests.json', b'{"requests":40,"limit":64,"results":[]}')
        first = self.capture(mutable_fields=dict.fromkeys((str(ledger),), list(('/requests',))))
        first_bytes = first.read_bytes()
        ledger.write_text(json.dumps(dict(requests=41, limit=64, results=list())), encoding="utf-8")
        second = protection.capture(self.archive, self.support, self.operations, baselines=list((first,)))
        self.assertTrue(protection.verify(first).get('verified'))
        self.assertTrue(protection.verify(second).get('verified'))
        self.assertEqual(first_bytes, first.read_bytes())

    def test_legacy_settings_remain_hash_only_never_byte_backed(self):
        settings = self.write(self.archive / 'imported/.claude/settings.local.json', b'UNBACKED CREDENTIAL FIXTURE')
        self.snapshot.write_text(json.dumps(dict(protected=dict.fromkeys((str(settings),), protection.file_hash(settings)))), encoding="utf-8")
        manifest = self.capture()
        value = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertNotIn(str(settings), value.get('files'))
        self.assertEqual(1, len(value.get('old_baselines').__getitem__(0).get('hash_only')))
        self.assertFalse(any(b'UNBACKED CREDENTIAL FIXTURE' in (manifest.parent / record.get('backup')).read_bytes() for record in value.get('files').values()))
        self.assertTrue(protection.verify(manifest).get('verified'))
        settings.write_text('changed credential fixture', encoding="utf-8")
        self.assertFalse(protection.verify(manifest).get('verified'))
