"""Repair only explicitly selected failed summary nodes; offline is the default."""
from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.segment_recovery import (
    AuthorizedSegmentRecovery, RecoveryBlocked, logical_node,
)
from sjtu_learning_assistant.knowledge_documents import (
    DocumentCompiler, CONTRACT, VISION_MODEL, digest, safe_destination, target_lock,
)
from sjtu_learning_assistant.knowledge_compiler import _atomic_write
from sjtu_learning_assistant.course_markdown_sync import CourseMarkdownSync


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--file', action='append', required=True,
                        help='Exact file path relative to the configured archive; repeat for selected files.')
    result.add_argument('--scope', required=True, help='Stable operation ID/max_calls (1..10), unchanged on restart.')
    result.add_argument('--ledger', type=Path, required=True, help='Existing linked requests.json; never reset or refund it.')
    mode = result.add_mutually_exclusive_group()
    mode.add_argument('--allow-ai', action='store_true', help='Explicitly authorize bounded live requests.')
    mode.add_argument('--offline', action='store_true', help='Saved responses only; never load a key or request AI.')
    result.add_argument('--retry-unknown', type=int, default=0,
                        help='Explicit bounded opt-in for outcome-unknown retries; consumes the SAME total/node allowance.')
    result.add_argument('--local-candidates', type=Path,
                        help='Explicit local JSON source-ID/node mapping of {input_hash,candidate}; still requires real source review.')
    result.add_argument('--diagnose', action='store_true', help='Read-only local diagnosis; no AI or recovery writes.')
    return result


def parse_scope(value):
    try:
        scope, calls = value.rsplit('/', 1)
        calls = int(calls)
    except (ValueError, AttributeError):
        raise ValueError('--scope must be a stable ID/max_calls') from None
    if not scope or not 1 <= calls <= 10:
        raise ValueError('--scope maximum must be 1..10')
    return scope, calls


def selected_checkpoints(source, target, state, files, *, frozen_registrations=None):
    """No archive-wide rebuild, guessed IDs, filename aliases or missing page generation."""
    selected, seen = [], set()
    for relative in files:
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or path.as_posix() != relative or relative in seen:
            raise RecoveryBlocked('--file requires unique, exact archive-relative paths.')
        seen.add(relative)
        original = safe_destination(source, source / path)
        item = state['items'].get(relative)
        source_id = digest(relative.encode())[:16]
        if (isinstance(item, dict) and item.get('status') == 'completed'
                and frozen_registrations is not None and source_id in frozen_registrations):
            # A partially completed operation can resume with its original --file list.
            # Already-published/manual-edited completed notes are never reopened or rebuilt.
            selected.append({'relative': relative, 'source_id': source_id,
                             'nodes': frozen_registrations[source_id], 'outputs': [],
                             'already_completed': True})
            continue
        if not original.is_file() or original.suffix.casefold() not in ('.pptx', '.pdf', '.ppt'):
            raise RecoveryBlocked('Selected source is not a supported, registered local document.')
        if not isinstance(item, dict) or item.get('status') not in ('failed', 'pending'):
            raise RecoveryBlocked('Only explicitly registered failed/pending sources can be recovered.')
        source_hash = digest(original.read_bytes())
        if item.get('source_hash') not in (None, source_hash):
            raise RecoveryBlocked('Source no longer matches the registered failure; original retained.')
        source_id = digest(relative.encode())[:16]
        revision = digest((source_hash + CONTRACT + VISION_MODEL).encode())[:16]
        directory = safe_destination(target, target / '90_Inbox' / 'Documents' / source_id / revision)
        manifest_path = safe_destination(target, directory / 'manifest.json')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (manifest.get('source') != relative or manifest.get('source_hash') != source_hash
                or manifest.get('contract') != CONTRACT or manifest.get('model') != VISION_MODEL
                or manifest.get('pending') is not None):
            raise RecoveryBlocked('Source checkpoint differs or has an unfinished transaction; inspect it first.')
        outputs, pages = manifest.get('outputs'), manifest.get('pages')
        if not isinstance(outputs, dict) or not isinstance(pages, dict) or not pages:
            raise RecoveryBlocked('Complete saved source page evidence is required.')
        for name, expected in outputs.items():
            if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts:
                raise RecoveryBlocked('Invalid registered output path.')
            candidate = safe_destination(target, directory / name)
            if not candidate.is_file() or digest(candidate.read_bytes()) != expected:
                raise RecoveryBlocked('Registered output was edited/missing; no overwrite or reset.')
        numbers = sorted(int(n) for n in pages)
        if numbers != list(range(1, len(numbers) + 1)) or any(
                pages[str(n)] != f'pages/page-{n:04d}.md' or pages[str(n)] not in outputs for n in numbers):
            raise RecoveryBlocked('Saved page evidence is incomplete; no page regeneration authorized.')
        from sjtu_learning_assistant.document_text import extract_document
        document = extract_document(original, original.name)
        if document.truncated or document.unit_count != len(numbers):
            raise RecoveryBlocked('Saved page count differs from physical source pages.')
        mirror = safe_destination(target, target / 'Courses' / Path(relative + '.md'))
        if not mirror.is_file() or digest(mirror.read_bytes()) != item.get('mirror_hash'):
            raise RecoveryBlocked('Current registered Markdown mirror was edited/missing; retained unchanged.')
        # Only exhausted actual summary stages qualify; repair/review errors do not
        # authorize new sources or new logical node identities.
        nodes = set()
        for name in outputs:
            if not name.startswith('summary/') or '-resume-v1-' not in name:
                continue
            saved = json.loads((directory / name).read_text(encoding='utf-8'))
            if not saved.get('error') or not saved.get('draft') or saved.get('status') == 'validated':
                continue
            node_name = name.split('/')[-1]
            if '-review' in node_name:
                continue
            nodes.add(logical_node(node_name))
        if frozen_registrations is not None and source_id in frozen_registrations:
            # Reuse the original authorization even if error text/checkpoint state changed.
            nodes = set(frozen_registrations[source_id])
        if not nodes:
            raise RecoveryBlocked('No retained failed segmented node found; diagnosis does not grant a budget.')
        selected.append({'relative': relative, 'original': original, 'source_id': source_id,
                         'source_hash': source_hash, 'directory': directory, 'mirror': mirror,
                         'mirror_hash': digest(mirror.read_bytes()), 'nodes': sorted(nodes),
                         'outputs': list(outputs), 'page_count': len(numbers)})
    return selected


def live_transport(settings, cache_path):
    """Called only after explicit allow-ai and protection/lock checks; one HTTP call.

    Existing OpenAIClassificationClient retains shared provider/user-key 10 RPM /
    300000 TPM scheduling. Exact CompilationResponseCache lookup precedes reservation.
    No retries/fallbacks exist in this transport; complete raw envelopes are retained
    by the authorization context before checking finish reason or JSON validity.
    """
    from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient
    from sjtu_learning_assistant.ai_keychain import get_ai_api_key
    from sjtu_learning_assistant.compilation_cache import CompilationResponseCache
    import httpx
    key = get_ai_api_key()
    if not key:
        raise RecoveryBlocked('No saved provider key; no reservation or model request made.')
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    cache = CompilationResponseCache(cache_path)
    clients = {}
    def max_tokens(prompt):
        return 1024 if '审校员' in prompt else 7500
    def identity(model, prompt, content):
        return cache.identity((model, settings.ai_base_url, key_hash), prompt, content, max_tokens(prompt))
    def lookup(model, prompt, content):
        return cache.get(identity(model, prompt, content))
    class RawCapturedClient(OpenAIClassificationClient):
        def _post_completion(self, body, *, request_check=None):
            payload = super()._post_completion(body, request_check=request_check)
            sink = getattr(self._capture_scope, 'sink', None)
            if sink is None:
                raise RecoveryBlocked('Provider request has no durable raw-response reservation sink.')
            sink(payload)  # BEFORE choices/finish reason/content parsing or stripping.
            return payload
    def request(model, prompt, content, *, raw_response=None):
        if raw_response is None:
            raise RecoveryBlocked('Live transport cannot run outside a durable reservation.')
        if model not in clients:
            clients[model] = RawCapturedClient(api_key=key, base_url=settings.ai_base_url, model=model,
                timeout=httpx.Timeout(connect=15, read=300, write=60, pool=15))
            clients[model]._capture_scope = threading.local()
        client = clients[model]
        client._capture_scope.sink = raw_response
        try:
            return client.chat_completion(
                [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': content}],
                max_tokens=max_tokens(prompt), temperature=0.1, system_prompt=False,
                knowledge_compilation=True, response_format={'type': 'json_object'})
        finally:
            client._capture_scope.sink = None
    request.supports_raw_capture = True
    request.cache_store = lambda model, prompt, content, raw: cache.store(identity(model, prompt, content), raw)
    def close():
        for client in clients.values():
            client.close()
    return request, lookup, close


def run(args, *, settings_loader=None, compiler_class=DocumentCompiler, transport_factory=live_transport):
    if settings_loader is None:
        from sjtu_learning_assistant.local_settings import SettingsStore
        settings_loader = SettingsStore().load
    settings = settings_loader()
    source = Path(settings.archive_root).resolve()
    target = safe_destination(source, source / 'Knowledge Markdown')
    marker = safe_destination(target, target / '99_System' / 'Course-Sync.json')
    scope, max_calls = parse_scope(args.scope)
    def paused():
        current = settings_loader()
        if current.ai_enabled or Path(current.archive_root).resolve() != source:
            raise RecoveryBlocked('Pause and drain background AI before recovery writes; archive must remain unchanged.')
    if args.diagnose:
        state = json.loads(marker.read_text(encoding='utf-8'))
        selected = selected_checkpoints(source, target, state, args.file)
        return {'status': 'diagnosed', 'offline': True, 'files': [
            {'file': s['relative'], 'failed_nodes': s['nodes'], 'saved_pages': s['page_count']} for s in selected]}
    paused()  # No lock, ledger or checkpoint writes before background AI is paused.
    if 'authorized_recovery' not in inspect.signature(compiler_class).parameters:
        raise RecoveryBlocked('DocumentCompiler integration missing: add optional authorized_recovery=None and pass it to SummaryCompiler.')
    request = lookup = None
    close = lambda: None
    with target_lock(target):
        paused()
        state = json.loads(marker.read_text(encoding='utf-8'))
        if state.get('version') != 1 or state.get('source_root') != str(source) or not isinstance(state.get('items'), dict):
            raise RecoveryBlocked('Course sync registration is invalid; retained unchanged.')
        ledger_history = json.loads(args.ledger.read_text(encoding='utf-8'))
        saved_scope = ledger_history.get('authorized_scopes', {}).get(scope)
        frozen_registrations = saved_scope.get('registrations') if saved_scope is not None else None
        selected = selected_checkpoints(source, target, state, args.file,
                                        frozen_registrations=frozen_registrations)
        local_candidates = None
        candidate_path = getattr(args, 'local_candidates', None)
        if candidate_path is not None:
            from sjtu_learning_assistant.segment_recovery import _safe_path
            candidate_path = _safe_path(candidate_path)
            if not candidate_path.is_file() or candidate_path.stat().st_size > 2_000_000:
                raise RecoveryBlocked('Explicit local candidate JSON is missing or exceeds the bounded size.')
            local_candidates = json.loads(candidate_path.read_text(encoding='utf-8'))
            if not isinstance(local_candidates, dict):
                raise RecoveryBlocked('Local candidates must be a source-ID/logical-node mapping.')
        marker_hash = digest(marker.read_bytes())
        def persist():
            nonlocal marker_hash
            paused()
            if digest(marker.read_bytes()) != marker_hash:
                raise RecoveryBlocked('Sync registration changed concurrently; no overwrite.')
            _atomic_write(marker, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
            marker_hash = digest(marker.read_bytes())
        if args.allow_ai and not args.offline and any(not s.get('already_completed') for s in selected):
            from sjtu_learning_assistant.database import APP_SUPPORT_DIR
            request, lookup, close = transport_factory(settings, APP_SUPPORT_DIR / 'compilation-responses.sqlite3')
        def forbidden(*unused):
            raise RecoveryBlocked('Offline mode forbids model requests and credential access.')
        try:
            root = AuthorizedSegmentRecovery(args.ledger, scope=scope, max_calls=max_calls,
                registrations={s['source_id']: s['nodes'] for s in selected},
                checkpoint_keys={s['source_id']: s['outputs'] for s in selected},
                allow_ai=args.allow_ai, offline=args.offline or not args.allow_ai,
                retry_unknown=args.retry_unknown, runner=request or forbidden, cache_lookup=lookup,
                local_candidates=local_candidates)
            results = []
            for selected_file in selected:
                paused()
                if selected_file.get('already_completed'):
                    results.append({'file': selected_file['relative'], 'status': 'completed', 'reused': True})
                    continue
                original, mirror = selected_file['original'], selected_file['mirror']
                item = state['items'][selected_file['relative']]
                if digest(original.read_bytes()) != selected_file['source_hash'] or digest(mirror.read_bytes()) != selected_file['mirror_hash']:
                    raise RecoveryBlocked('Original/mirror changed since authorization; no overwrite.')
                original_item = copy.deepcopy(item)
                try:
                    context = root.for_source(selected_file['source_id'])
                    note = compiler_class(request or forbidden, threading.Event(), text_first=True,
                        review_percent=20, parallel_batches=1, auto_recovery=False,
                        content_recovery=False, authorized_recovery=context).compile(source, original, target)
                    if digest(original.read_bytes()) != selected_file['source_hash']:
                        raise RecoveryBlocked('Source changed during compilation; mirror retained.')
                    def publish(row, destination, body):
                        paused()
                        if not destination.is_file() or digest(destination.read_bytes()) != selected_file['mirror_hash']:
                            raise RecoveryBlocked('Manual Markdown change during compilation; retained unchanged.')
                        old = destination.read_bytes()
                        backup = safe_destination(target, target / '90_Inbox' / 'Previous'
                            / destination.relative_to(target / 'Courses') / (digest(old) + '.md'))
                        if backup.exists() and (not backup.is_file() or backup.read_bytes() != old):
                            raise RecoveryBlocked('Immutable previous mirror backup conflict.')
                        if not backup.exists():
                            _atomic_write(backup, old.decode('utf-8'))
                        row['pending_hash'] = digest(body.encode())
                        persist()
                        _atomic_write(safe_destination(target, destination), body)
                        row['mirror_hash'] = digest(body.encode())
                        row.pop('pending_hash', None)
                    # Compiler uses its protected transaction; mirror publication uses the
                    # existing course sync path, never direct manifest/document corruption.
                    CourseMarkdownSync._publish_compiled_note(note, mirror, target, item, publish)
                    for key in ('auto_paused', 'error', 'retry_after'):
                        item.pop(key, None)  # Only a successful protected publication unpauses.
                    persist()
                    results.append({'file': selected_file['relative'], 'status': 'completed'})
                except Exception as exc:
                    # Never clear old stage counters/pauses/errors to dodge a remaining failure.
                    if digest(mirror.read_bytes()) == selected_file['mirror_hash']:
                        state['items'][selected_file['relative']] = original_item
                    results.append({'file': selected_file['relative'], 'status': 'preserved',
                                    'error_type': type(exc).__name__, 'error': str(exc)})
            return {'status': 'completed' if all(r['status'] == 'completed' for r in results) else 'preserved',
                    'files': results, 'authorization': root.status()}
        finally:
            close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        result = {'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('completed', 'diagnosed') else 2


if __name__ == '__main__':
    raise SystemExit(main())
