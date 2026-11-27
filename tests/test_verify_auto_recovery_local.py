"""Offline verifier fixtures; never touches production databases or providers."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify_auto_recovery_local.py'
spec = importlib.util.spec_from_file_location('verify_auto_recovery_fixture', SCRIPT)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class RegisteredVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name).resolve() / 'archive'
        self.root = self.archive / 'Knowledge Markdown'
        self.root.mkdir(parents=True)

    def fixture(self, relative='course/Group Project.pptx', static=True, model='old-recorded-model'):
        source = self.archive / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'original local source')
        sha = verifier.file_hash(source)
        sid = hashlib.sha256(relative.encode()).hexdigest()[:16]
        policy = 'static-source-text-20261125-v1'
        revision = hashlib.sha256((sha + (policy if static else 'course-lecture-v2' + model)).encode()).hexdigest()[:16]
        directory = self.root / '90_Inbox/Documents' / sid / revision
        directory.mkdir(parents=True)
        document = directory / 'document.md'
        document.write_text('---\ncontract: course-summary-v1\n---\n\n# Summary\n\n' + 'Source-backed content. ' * 4)
        mirror = self.root / 'Courses' / (relative + '.md')
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_bytes(document.read_bytes())
        report = dict(contract='course-summary-v1', covered_pages=[1, 2], summary_chars=100,
                      checks=[dict(approved=True, issues=[], mode='sampled-out' if static else 'source-review')])
        manifest = dict(version=1, source=relative, source_hash=sha, status='completed',
                        contract='course-lecture-v2', study_contract='course-summary-v1', model=model, page_count=2)
        outputs = dict()
        if static:
            inventory = dict(source_sha256=sha, source_id=sid, covered_pages=[1, 2], original_rendered=False,
                             external_assets_opened=False, embedded_payloads_opened=False)
            (directory / 'static-inventory.json').write_text(json.dumps(inventory))
            outputs['static-inventory.json'] = verifier.file_hash(directory / 'static-inventory.json')
            report.update(evidence_mode='static-extraction-only')
            manifest.update(static_policy=policy, quality_status='static-source-sampled')
        (directory / 'review.json').write_text(json.dumps(report))
        for name in ('document.md', 'review.json'):
            outputs[name] = verifier.file_hash(directory / name)
        manifest['outputs'] = outputs
        (directory / 'manifest.json').write_text(json.dumps(manifest))
        item = dict(status='completed', document=document.relative_to(self.root).as_posix(), mirror_hash=verifier.file_hash(mirror))
        return relative, item, directory

    def test_registered_static_revision_not_guessed_visual_path_and_not_claimed_reviewed(self):
        relative, item, directory = self.fixture()
        before = {str(p): p.read_bytes() for p in self.archive.rglob('*') if p.is_file()}
        result = verifier.check_registered_course(self.root, relative, item)
        self.assertEqual('static-extraction-only', result['evidence_mode'])
        self.assertEqual((1, 0), (result['sampled_checks'], result['reviewed_checks']))
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.archive.rglob('*') if p.is_file()})

    def test_visual_uses_actual_registered_model_identity(self):
        relative, item, directory = self.fixture(static=False)
        self.assertEqual('visual-page-evidence', verifier.check_registered_course(self.root, relative, item)['evidence_mode'])

    def test_all_missing_and_corrupt_reports_aggregate_without_fabrication(self):
        current = dict()
        for number in range(3):
            relative, item, directory = self.fixture('course/%s.pptx' % number)
            current[relative] = item
            if number < 2:
                (directory / 'review.json').unlink()
            else:
                (directory / 'review.json').write_text('{bad')
        before = sorted(str(p) for p in self.archive.rglob('*'))
        checked, failures = verifier.check_new_courses(self.root, current, {})
        self.assertEqual([], checked)
        self.assertEqual(3, len(failures))
        self.assertEqual(before, sorted(str(p) for p in self.archive.rglob('*')))
        self.assertNotIn('payload', json.dumps(failures))

    def test_tampered_registered_output_and_wrong_source_fail(self):
        relative, item, directory = self.fixture()
        (directory / 'document.md').write_text('manual edit')
        with self.assertRaisesRegex(ValueError, 'hash_mismatch'):
            verifier.check_registered_course(self.root, relative, item)
        relative, item, directory = self.fixture('course/other.pptx')
        path = directory / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['source_hash'] = '0' * 64
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'source_contract'):
            verifier.check_registered_course(self.root, relative, item)

    def test_existing_baseline_protected_course_skipped(self):
        relative, item, directory = self.fixture()
        mirror = self.root / 'Courses' / (relative + '.md')
        (directory / 'review.json').unlink()
        self.assertEqual(([], []), verifier.check_new_courses(self.root, {relative: item}, {str(mirror): 'old'}))

    def test_before_never_recreates_old_verification_baseline(self):
        with patch.object(Path, 'home', side_effect=AssertionError('must reject before reading home')):
            with self.assertRaises(SystemExit) as raised:
                verifier.main(['before'])
        self.assertEqual(2, raised.exception.code)

    def test_symlink_document_rejected_without_following(self):
        relative, item, directory = self.fixture()
        target = self.archive / 'manual.md'
        target.write_text('manual')
        (directory / 'document.md').unlink()
        (directory / 'document.md').symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            verifier.check_registered_course(self.root, relative, item)
