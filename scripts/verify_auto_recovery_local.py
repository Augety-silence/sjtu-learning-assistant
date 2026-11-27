"""本地安装前后验收；旧基线只读，新增快照 exclusive-create，不调用模型。"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote

DEFAULT_SNAPSHOT = None


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_file(root, path):
    path = Path(path).absolute()
    root = Path(root).absolute()
    if not path.is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('unsafe_registered_path')
    if not path.is_file():
        raise ValueError('registered_file_missing')
    return path


def check_registered_course(root, relative, item):
    """Use the registered revision, not today's visual/static policy to guess one."""
    source = safe_file(root.parent, root.parent / relative)
    source_hash = file_hash(source)
    source_id = hashlib.sha256(relative.encode()).hexdigest()[:16]
    registered = item.get('document')
    if not isinstance(registered, str) or Path(registered).is_absolute() or '..' in Path(registered).parts:
        raise ValueError('invalid_document_registration')
    document = safe_file(root, root / registered)
    expected_parent = root / '90_Inbox/Documents' / source_id
    if document.name != 'document.md' or document.parent.parent != expected_parent:
        raise ValueError('document_source_identity_mismatch')
    directory = document.parent
    manifest = json.loads(safe_file(root, directory / 'manifest.json').read_text(encoding='utf-8'))
    report = json.loads(safe_file(root, directory / 'review.json').read_text(encoding='utf-8'))
    if (manifest.get('version') != 1 or manifest.get('source') != relative
            or manifest.get('source_hash') != source_hash or manifest.get('status') != 'completed'
            or manifest.get('pending') is not None or manifest.get('contract') != 'course-lecture-v2'
            or manifest.get('study_contract') != 'course-summary-v1'
            or report.get('contract') != 'course-summary-v1'):
        raise ValueError('completed_source_contract_mismatch')
    outputs = manifest.get('outputs')
    if not isinstance(outputs, dict):
        raise ValueError('invalid_output_registration')
    for name in ('document.md', 'review.json'):
        if outputs.get(name) != file_hash(directory / name):
            raise ValueError('registered_output_hash_mismatch')
    mirror = safe_file(root, root / 'Courses' / (relative + '.md'))
    if file_hash(mirror) != item.get('mirror_hash'):
        raise ValueError('registered_mirror_hash_mismatch')
    document_text = document.read_text(encoding='utf-8')
    pieces = document_text.split('---', 2)
    body = pieces[2].lstrip('\n') if document_text.startswith('---') and len(pieces) == 3 else document_text
    if not 1 <= len(body) <= 5000:
        raise ValueError('completed_reading_body_length_invalid')
    pages = report.get('covered_pages')
    count = manifest.get('page_count')
    if (type(count) is not int or not 1 <= count <= 500 or type(pages) is not list
            or pages != list(range(1, count + 1))):
        raise ValueError('review_page_coverage_mismatch')
    chars = report.get('summary_chars')
    checks = report.get('checks')
    if (type(chars) is not int or not 1 <= chars <= 5000 or type(checks) is not list or not checks
            or any(type(check) is not dict or check.get('approved') is not True
                   or check.get('issues') != [] for check in checks)):
        raise ValueError('review_rejected_or_invalid')
    static = bool(manifest.get('static_policy'))
    if static:
        if (report.get('evidence_mode') != 'static-extraction-only'
                or not str(manifest.get('quality_status', '')).startswith('static-source-')):
            raise ValueError('static_evidence_mode_mismatch')
        policy = manifest['static_policy']
        expected_revision = hashlib.sha256((source_hash + policy).encode()).hexdigest()[:16]
        inventory = json.loads(safe_file(root, directory / 'static-inventory.json').read_text(encoding='utf-8'))
        if (outputs.get('static-inventory.json') != file_hash(directory / 'static-inventory.json')
                or inventory.get('source_sha256') != source_hash or inventory.get('source_id') != source_id
                or inventory.get('covered_pages') != pages or inventory.get('original_rendered') is not False
                or inventory.get('external_assets_opened') is not False
                or inventory.get('embedded_payloads_opened') is not False):
            raise ValueError('static_inventory_identity_mismatch')
    else:
        model = manifest.get('model')
        if not isinstance(model, str) or report.get('evidence_mode') == 'static-extraction-only':
            raise ValueError('visual_evidence_mode_mismatch')
        expected_revision = hashlib.sha256((source_hash + manifest['contract'] + model).encode()).hexdigest()[:16]
    if directory.name != expected_revision:
        raise ValueError('registered_revision_identity_mismatch')
    return dict(pages=count, summary_chars=chars, evidence_mode='static-extraction-only' if static else 'visual-page-evidence',
                sampled_checks=sum(check.get('mode') == 'sampled-out' for check in checks),
                reviewed_checks=sum(check.get('mode') != 'sampled-out' for check in checks))


def check_new_courses(root, current, protected):
    checked, failures = [], []
    for relative, item in current.items():
        mirror = root / 'Courses' / (relative + '.md')
        if item.get('status') != 'completed' or str(mirror) in protected:
            continue
        source_id = hashlib.sha256(relative.encode()).hexdigest()[:16]
        try:
            checked.append(dict(source_id=source_id, **check_registered_course(root, relative, item)))
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            # Never echo source payloads/exception paths; aggregate all actual failures.
            code = str(exc) if type(exc) is ValueError and str(exc).replace('_', '').isalnum() else type(exc).__name__
            failures.append(dict(source_id=source_id, error=code))
    return checked, failures


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('before', 'after'))
    parser.add_argument('--snapshot', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == 'before' and args.snapshot == DEFAULT_SNAPSHOT:
        parser.error('旧基线禁止重建；请用 --snapshot 指定新的唯一文件，或使用 preservation_operation_local.py。')
    support = Path.home() / 'Library/Application Support/SJTU Learning Assistant'
    root = Path.home() / 'Documents/SJTU Study/Knowledge Markdown'
    items = json.loads((root / '99_System/Course-Sync.json').read_text())['items']
    with sqlite3.connect('file:' + quote(str(support / 'data/app.db')) + '?mode=ro', uri=True) as db:
        active = set(row[0] for row in db.execute("SELECT local_path FROM course_files WHERE is_active=1 AND download_status='downloaded' AND local_path IS NOT NULL"))
    current = {relative: item for relative, item in items.items() if str(root.parent / relative) in active}
    notes, protected = [], {}
    def protect(path):
        if path.is_file() and not path.is_symlink():
            protected[str(path)] = file_hash(path)
    for relative, item in current.items():
        protect(root.parent / relative)
        if item.get('status') == 'completed':
            protect(root / 'Courses' / (relative + '.md'))
    for directory in (support / 'transcripts/v1/videos').iterdir():
        f = directory / 'manifest.json'
        if not f.is_file() or directory.is_symlink() or f.is_symlink():
            continue
        data = json.loads(f.read_text())
        notes.append(data.get('lesson_notes', {}))
        protect(directory / 'raw.vtt')
        if data.get('lesson_notes', {}).get('status') == 'completed':
            for name in ('lesson.md', 'lecture-summary.md'):
                protect(directory / name)
    result = dict(observed_at=time.time(), courses=dict(collections.Counter(i.get('status') for i in current.values())),
                  transcripts=dict(collections.Counter(i.get('status') for i in notes)),
                  auto_paused_courses=sum(bool(i.get('auto_paused')) for i in current.values()),
                  auto_paused_transcripts=sum(bool(i.get('auto_paused')) for i in notes))
    if args.action == 'before':
        with args.snapshot.open('x', encoding='utf-8') as stream:
            json.dump(dict(protected=protected, baseline=result), stream)
        result['protected_files'] = len(protected)
    else:
        saved = json.loads(args.snapshot.read_text())
        changed = 0
        for path, expected in saved['protected'].items():
            try:
                f = Path(path)
                changed += f.is_symlink() or not f.is_file() or file_hash(f) != expected
            except OSError:
                changed += 1
        checked, failures = check_new_courses(root, current, saved['protected'])
        result.update(protected_files=len(saved['protected']), protected_changed=changed,
                      elapsed_seconds=round(result['observed_at'] - saved['baseline']['observed_at'], 1),
                      new_courses=result['courses'].get('completed', 0) - saved['baseline']['courses'].get('completed', 0),
                      new_transcripts=result['transcripts'].get('completed', 0) - saved['baseline']['transcripts'].get('completed', 0),
                      new_course_checks=checked, verification_failures=failures, verified=changed == 0 and not failures)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if args.action == 'before' or result['verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
