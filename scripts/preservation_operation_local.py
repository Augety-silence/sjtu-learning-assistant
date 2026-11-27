"""Unique, byte-backed local protection operations. No models, credentials or repair.

capture scans the archive and known learning-artifact directories, plus every old
protection manifest. Existing completed/manual/unregistered drafts are immutable
by default. Explicit JSON-pointer allowlists permit only selected state fields.
verify is read-only: it never refreshes baselines, repairs files or refunds budgets.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

VERSION = 1
DENIED_PARTS = frozenset(('.git', '.venv', '.ssh', '.aws', '.gnupg', 'keychain', 'credentials',
                          'mail-attachments', 'webview', 'node_modules'))
DENIED_SUFFIXES = frozenset(('.db', '.sqlite', '.sqlite3', '.pem', '.key', '.p12', '.pfx'))
MISSING = object()


def safe_path(path):
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('parent_traversal_not_allowed')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink_not_allowed')
    return path


def credential_path(path):
    for part in path.parts:
        lower = part.casefold()
        if (lower in DENIED_PARTS or lower.startswith('.env') or lower in ('.netrc', '.npmrc')
                or any(word in lower for word in ('keychain', 'credential', 'api-key', 'api_key'))):
            return True
    name = path.name.casefold()
    return (path.suffix.casefold() in DENIED_SUFFIXES or name.startswith(('settings', 'secrets', 'access_token', 'refresh_token'))
            or name.endswith(('-wal', '-shm')))


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def roots_for(archive, support):
    archive, support = safe_path(archive), safe_path(support)
    if not archive.is_dir() or not support.is_dir():
        raise ValueError('artifact_roots_missing')
    return (archive, support / 'transcripts', support / 'recording-asr', support / 'recovery-backups')


def allowed_artifact(path, roots):
    path = safe_path(path)
    if credential_path(path) or not any(path.is_relative_to(root) for root in roots):
        raise ValueError('outside_artifact_scope_or_credentials')
    return path


def old_manifest(path, roots):
    path = safe_path(path)
    if credential_path(path) or not path.is_file():
        raise ValueError('invalid_old_baseline')
    value = json.loads(path.read_text(encoding='utf-8'))
    records = value.get('protected')
    previous_operation = records is None and value.get('version') == VERSION
    if previous_operation:
        if not verify(path)['verified']:
            raise ValueError('previous_operation_verification_failed')
        records = dict((name, item['sha256']) for name, item in value['files'].items())
    if not isinstance(records, dict):
        raise ValueError('unrecognized_old_baseline')
    entries, failures, hash_only = dict(), [], []
    if previous_operation:
        for ancestor in value.get('old_baselines', list()):
            hash_only.extend(copy.deepcopy(ancestor.get('hash_only', list())))
    for name, expected in records.items():
        artifact = safe_path(name)
        if not any(artifact.is_relative_to(root) for root in roots):
            raise ValueError('outside_artifact_scope_or_credentials')
        if isinstance(expected, dict):
            expected = expected.get('sha256')
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError('invalid_old_baseline_digest')
        try:
            good = artifact.is_file() and (file_hash(artifact) == expected or previous_operation
                                           and bool(value['files'][name].get('mutable_fields')))
        except OSError:
            good = False
        if not good:
            failures.append(dict(path=str(artifact), error='old_baseline_changed_or_missing'))
        if credential_path(artifact):
            # Legacy settings are hash-only: never copy credential bytes.
            hash_only.append(dict(path=str(artifact), sha256=expected))
        else:
            entries[str(artifact)] = artifact
    return dict(path=str(path), sha256=file_hash(path), files=len(records), hash_only=hash_only), entries, failures


def scan(roots, excluded, max_files):
    files = dict()
    for root in roots:
        if not root.exists():
            continue
        safe_path(root)
        for directory, dirs, names in os.walk(root, followlinks=False):
            directory = Path(directory)
            kept = []
            for name in dirs:
                p = directory / name
                if any(p == parent or p.is_relative_to(parent) for parent in excluded) or credential_path(p):
                    continue
                safe_path(p)
                kept.append(name)
            dirs[:] = kept
            for name in names:
                p = directory / name
                if credential_path(p):
                    continue
                safe_path(p)
                if not p.is_file():
                    raise ValueError('nonregular_artifact')
                files[str(p)] = p
                if len(files) > max_files:
                    raise ValueError('file_limit_exceeded_no_partial_baseline')
    return files


def pointer_parts(pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/') or pointer == '/' or '*' in pointer:
        raise ValueError('exact_nonroot_json_pointer_required')
    parts = pointer[1:].split('/')
    for part in parts:
        if '~' in part.replace('~0', '').replace('~1', ''):
            raise ValueError('invalid_json_pointer_escape')
    return tuple(part.replace('~1', '/').replace('~0', '~') for part in parts)


def at(value, parts):
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part, MISSING)
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            return MISSING
    return value


def scrub(value, parts):
    parent = at(value, parts[:-1])
    if parent is MISSING:
        return
    if isinstance(parent, dict):
        parent.pop(parts[-1], None)
    else:
        # Array insertion/removal can shift sibling evidence; allow individual dict keys only.
        raise ValueError('mutable_array_element_not_supported')


def validate_allowlist(fields, files):
    result = dict()
    for name, pointers in (fields or {}).items():
        path = safe_path(name)
        if (str(path) not in files or path.name not in ('Course-Sync.json', 'manifest.json', 'requests.json', 'state.json', 'ledger.json')
                or credential_path(path)):
            raise ValueError('mutable_file_not_protected_json')
        if not isinstance(pointers, list) or not pointers:
            raise ValueError('mutable_fields_require_explicit_list')
        decoded = tuple(pointer_parts(pointer) for pointer in pointers)
        for a in decoded:
            for b in decoded:
                if a != b and b[:len(a)] == a:
                    raise ValueError('overlapping_mutable_fields')
        state = json.loads(files[str(path)].read_text(encoding='utf-8'))
        for parts in decoded:
            # Do not allow an operator to replace a whole item, request list, or response body.
            if (parts[-1] in ('items', 'outputs', 'pages', 'results', 'authorized_scopes', 'nodes', 'limit', 'max_calls')
                    or isinstance(at(state, parts), (dict, list))):
                raise ValueError('whole_evidence_or_budget_field_cannot_be_mutable')
        result[str(path)] = list(pointers)
    return result


def capture(archive, support, operations_dir, *, baselines=(), mutable_fields=None, max_files=100000):
    roots = roots_for(archive, support)
    parent = safe_path(operations_dir)
    files = scan(roots, (parent,), max_files)
    old, failures = [], []
    for baseline in baselines:
        record, additional, changed = old_manifest(baseline, roots)
        old.append(record)
        files.update(additional)
        failures.extend(changed)
    if failures:
        raise ValueError('old_baseline_changed_or_missing_no_capture')
    if len(files) > max_files:
        raise ValueError('file_limit_exceeded_no_partial_baseline')
    mutable = validate_allowlist(mutable_fields, files)
    for name in mutable:
        value = json.loads(files[name].read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('mutable_state_must_be_json_object')
    initial_hashes = dict((name, file_hash(path)) for name, path in files.items())
    total = sum(p.stat().st_size for p in files.values()) + sum(Path(item['path']).stat().st_size for item in old)
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(parent).free < total + 16 * 1024 * 1024:
        raise ValueError('insufficient_backup_space')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    operation = Path(tempfile.mkdtemp(prefix=stamp + '-', dir=parent))
    os.chmod(operation, 0o700)
    (operation / 'bytes').mkdir(mode=0o700)
    records = dict()
    for index, (name, path) in enumerate(sorted(files.items())):
        backup = 'bytes/%08d' % index
        before = initial_hashes.get(name)
        if file_hash(path) != before:
            raise ValueError('artifact_changed_during_capture_incomplete_operation_retained')
        with path.open('rb') as source, (operation / backup).open('xb') as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(operation / backup, 0o600)
        if file_hash(operation / backup) != before or file_hash(path) != before:
            raise ValueError('artifact_changed_during_capture_incomplete_operation_retained')
        records[name] = dict(sha256=before, size=path.stat().st_size, backup=backup,
                             mutable_fields=mutable.get(name, []))
    for index, item in enumerate(old):
        name = 'old-baseline-%04d.json' % index
        with Path(item['path']).open('rb') as source, (operation / name).open('xb') as destination:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(operation / name, 0o600)
        if file_hash(operation / name) != item['sha256']:
            raise ValueError('old_baseline_changed_during_capture')
        item['backup'] = name
    # Seal only a coherent snapshot; background changes never become a partial baseline.
    for name, record in records.items():
        if file_hash(Path(name)) != record['sha256']:
            raise ValueError('artifact_changed_before_seal_incomplete_operation_retained')
    for baseline in baselines:
        _, _, changed = old_manifest(baseline, roots)
        if changed:
            raise ValueError('old_baseline_changed_before_seal')
    manifest = dict(version=VERSION, observed_at=datetime.now(timezone.utc).isoformat(),
                    roots=list(map(str, roots)), excluded_credentials=True, files=records, old_baselines=old)
    path = operation / 'manifest.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o400)
    with (operation / 'manifest.sha256').open('x', encoding='ascii') as stream:
        stream.write(file_hash(path) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(operation / 'manifest.sha256', 0o400)
    for directory in (operation / 'bytes', operation, parent):
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return path


def state_changes(before, after, pointers):
    old, new = copy.deepcopy(before), copy.deepcopy(after)
    for pointer in pointers:
        parts = pointer_parts(pointer)
        a, b = at(before, parts), at(after, parts)
        # Reservations, attempts and steps are never refunded; missing new values are not allowed.
        if parts[-1] in ('requests', 'attempts', 'step', 'used', 'unknown_retries_used', 'diagnostic_checks'):
            if a is not MISSING and (type(a) is not int or type(b) is not int or b < a):
                raise ValueError('counter_decreased_or_removed')
        scrub(old, parts)
        scrub(new, parts)
    if old != new:
        raise ValueError('unapproved_state_field_changed')


def verify(manifest_path):
    path = safe_path(manifest_path)
    operation = path.parent
    expected = safe_path(operation / 'manifest.sha256').read_text(encoding='ascii').strip()
    if file_hash(path) != expected:
        raise ValueError('protection_manifest_changed')
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('version') != VERSION:
        raise ValueError('unsupported_protection_version')
    failures = []
    for name, record in value['files'].items():
        try:
            artifact = safe_path(name)
            backup_name = record['backup']
            if Path(backup_name).is_absolute() or '..' in Path(backup_name).parts:
                raise ValueError('unsafe_backup_path')
            backup = safe_path(operation / backup_name)
            if file_hash(backup) != record['sha256']:
                raise ValueError('original_byte_backup_changed')
            if not artifact.is_file():
                raise ValueError('protected_file_missing')
            if file_hash(artifact) != record['sha256']:
                pointers = record.get('mutable_fields')
                if not pointers:
                    raise ValueError('immutable_file_changed')
                state_changes(json.loads(backup.read_text()), json.loads(artifact.read_text()), pointers)
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            code = str(exc) if type(exc) is ValueError and str(exc).replace('_', '').isalnum() else type(exc).__name__
            failures.append(dict(path=name, error=code))
    for old in value.get('old_baselines', []):
        for excluded in old.get('hash_only', []):
            try:
                if file_hash(safe_path(excluded.get('path'))) != excluded.get('sha256'):
                    raise ValueError('old_hash_only_credential_file_changed')
            except (OSError, ValueError) as exc:
                failures.append(dict(path=excluded.get('path'), error=type(exc).__name__))
        try:
            if file_hash(safe_path(old['path'])) != old['sha256'] or file_hash(safe_path(operation / old['backup'])) != old['sha256']:
                raise ValueError('old_baseline_manifest_changed')
        except (OSError, ValueError) as exc:
            failures.append(dict(path=old['path'], error=type(exc).__name__))
    return dict(verified=not failures, protected_files=len(value['files']), failures=failures)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    before = sub.add_parser('capture')
    before.add_argument('--archive', type=Path, required=True)
    before.add_argument('--support', type=Path, required=True)
    before.add_argument('--operations-dir', type=Path, required=True)
    before.add_argument('--old-baseline', type=Path, action='append', required=True)
    before.add_argument('--mutable-fields', type=Path)
    after = sub.add_parser('verify')
    after.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == 'capture':
        fields = json.loads(safe_path(args.mutable_fields).read_text()) if args.mutable_fields else None
        path = capture(args.archive, args.support, args.operations_dir, baselines=args.old_baseline, mutable_fields=fields)
        print(json.dumps(dict(manifest=str(path), models_called=0)))
        return 0
    result = verify(args.manifest)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
