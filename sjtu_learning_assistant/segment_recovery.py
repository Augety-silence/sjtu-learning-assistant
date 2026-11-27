"""Explicit, durable, source/node-scoped summary recovery (no implicit AI allowance)."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path

from .knowledge_compiler import KnowledgeResponseError
from .knowledge_study import _json, validate_review


class RecoveryBlocked(ValueError):
    """A preserved checkpoint requires an explicit operator decision."""


class RecoveryBudgetExhausted(RecoveryBlocked):
    pass


class RecoveryOutcomeUnknown(RecoveryBlocked):
    pass


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def logical_node(name):
    """Prompt/version/error suffixes never grant another node allowance."""
    match = re.match(r'^(part-\d+|merge-\d+-\d+)(?:-|\.|$)', name)
    if not match:
        raise RecoveryBlocked('Unknown segmented summary node; no authorization granted.')
    return match.group(1)


def _safe_path(path):
    path = Path(path).absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise RecoveryBlocked('Recovery storage cannot contain symbolic links.')
    return path


def _write(path, text, *, immutable=False):
    """fsync both file and parent; immutable response files are never replaced."""
    path = _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable:
        if path.exists():
            if path.read_text(encoding='utf-8') != text:
                raise RecoveryBlocked('Immutable recovery response conflict; retained unchanged.')
            return
        with path.open('x', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    else:
        import tempfile
        fd, temporary = tempfile.mkstemp(prefix='.recovery-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


RECOVERY_GUIDANCE = '''\nExplicit authorized segmented recovery: read ALL supplied pages or ALL input_summaries.
Repair the actual failed content, not just metadata. Preserve correct concepts, formulas,
conditions and source-backed citations; do not invent evidence or enlarge the page bounds.
For a merge use only the accepted input summaries, retaining every substantive topic and
its relationships. Restructure meaningfully to the source_budget (at most 4300 markdown
characters; hard limit 4850); never concatenate and truncate. Defer later-segment algorithms
rather than attributing them to earlier pages. Cite physical source pages, not guessed offsets.
Return the complete summary JSON. A separate real source review is required for changed text.
'''


def remove_strong_emphasis(markdown):
    """Remove paired strong markers only; conservatively protect all code/math spans."""
    protected = []
    index = 0
    while index < len(markdown):
        start = index
        line_start = markdown.rfind('\n', 0, index) + 1
        indent = markdown[line_start:index]
        if len(indent) <= 3 and not indent.strip() and markdown[index] in '`~':
            fence = re.match(r'(`{3,}|~{3,})[^\n]*(?:\n|$)', markdown[index:])
            if fence:
                marker = fence.group(1)
                close = re.search(r'(?m)^[ \t]{0,3}' + re.escape(marker[0]) + '{' + str(len(marker)) + r',}[ \t]*(?:\n|$)', markdown[index + fence.end():])
                index = index + fence.end() + close.end() if close else len(markdown)
                protected.append((start, index))
                continue
        escaped = len(markdown[:index]) - len(markdown[:index].rstrip('\\'))
        if escaped % 2:
            index += 1
            continue
        marker = None
        if markdown[index] == '`':
            marker = re.match(r'`+', markdown[index:]).group()
            close = re.search(r'(?<!`)' + re.escape(marker) + r'(?!`)', markdown[index + len(marker):])
        elif markdown.startswith(('\\(', '\\['), index):
            marker = markdown[index:index + 2]
            close = re.search(re.escape('\\)' if marker == '\\(' else '\\]'), markdown[index + 2:])
        elif markdown[index] == '$':
            marker = '$$' if markdown.startswith('$$', index) else '$'
            close = re.search(r'(?<![\\$])' + re.escape(marker) + r'(?!\$)', markdown[index + len(marker):])
        if marker:
            index = index + len(marker) + close.end() if close else len(markdown)
            protected.append((start, index))
        else:
            index += 1
    def strip(text):
        pattern = r'(?<!\\)\*\*(?=\S)(.+?)(?<=\S)(?<!\\)\*\*|(?<![\\A-Za-z0-9])__(?=\S)(.+?)(?<=\S)(?<!\\)__(?![A-Za-z0-9])'
        return re.sub(pattern, lambda m: m.group(1) if m.group(1) is not None else m.group(2), text, flags=re.S)
    result, last = [], 0
    for first, end in protected:
        result.extend((strip(markdown[last:first]), markdown[first:end]))
        last = end
    result.append(strip(markdown[last:]))
    return ''.join(result)


class AuthorizedSegmentRecovery:
    """One operation <=10 calls, linked to an existing non-resettable request ledger.

    ``registrations`` maps stable source IDs to failed logical nodes (part-0/merge-0-0).
    ``runner`` must make ONE provider request, without retries/fallbacks. If supplied it
    replaces the compiler's potentially retrying request function. ``cache_lookup`` is
    an optional exact provider/user-key response lookup, performed before reservation.
    No credentials, provider calls, or home-directory storage are accessed here.
    """
    def __init__(self, ledger_path, *, scope, max_calls, registrations,
                 allow_ai=False, offline=False, retry_unknown=0, runner=None,
                 cache_lookup=None, source_id=None, checkpoint_keys=None, local_candidates=None):
        if not isinstance(scope, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', scope):
            raise ValueError('A stable explicit authorization scope is required.')
        if type(max_calls) is not int or not 1 <= max_calls <= 10:
            raise ValueError('One selected-files operation allows at most 10 additional AI calls.')
        if type(retry_unknown) is not int or not 0 <= retry_unknown <= 10:
            raise ValueError('retry_unknown must be an explicit bounded integer.')
        self.ledger_path = _safe_path(ledger_path)
        if not self.ledger_path.is_file():
            raise RecoveryBlocked('The linked existing recovery ledger is required; not creating/resetting it.')
        self.scope, self.max_calls = scope, max_calls
        self.allow_ai, self.offline, self.retry_unknown = allow_ai, offline, retry_unknown
        self.runner, self.cache_lookup, self.source_id = runner, cache_lookup, source_id
        self.checkpoint_keys = checkpoint_keys or {}
        self.registrations = {str(source): sorted({logical_node(node) for node in nodes})
                              for source, nodes in registrations.items()}
        if (not self.registrations or any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', source)
                or not nodes for source, nodes in self.registrations.items())):
            raise ValueError('Explicit failed source/node registrations are required.')
        if local_candidates is not None and not isinstance(local_candidates, dict):
            raise RecoveryBlocked('Local candidates must be a source-ID/logical-node mapping.')
        self.local_candidates = copy.deepcopy(local_candidates or {})
        for source, nodes in self.local_candidates.items():
            if source not in self.registrations or not isinstance(nodes, dict):
                raise RecoveryBlocked('Local candidate source is not explicitly registered.')
            for node, entry in nodes.items():
                if (node not in self.registrations[source] or logical_node(node) != node
                        or not isinstance(entry, dict) or set(entry) != {'input_hash', 'candidate'}
                        or not isinstance(entry['input_hash'], str)
                        or not re.fullmatch(r'[a-f0-9]{64}', entry['input_hash'])
                        or not isinstance(entry['candidate'], dict)
                        or set(entry['candidate']) != {'title', 'abstract', 'tags', 'synonyms', 'markdown',
                                                     'covered_pages', 'covered_topics', 'topics'}):
                    raise RecoveryBlocked('Local candidates need registered logical nodes, exact input hashes and complete candidate JSON; approval flags forbidden.')
        self._mutex, self._local = threading.RLock(), threading.local()
        with self._ledger() as history:
            scopes = history.setdefault('authorized_scopes', {})
            record = scopes.get(scope)
            if record is None:
                scopes[scope] = {'max_calls': max_calls, 'registrations': self.registrations,
                                 'requests': 0, 'attempts': [], 'nodes': {}, 'unknown_retries_used': 0}
            elif record.get('max_calls') != max_calls or record.get('registrations') != self.registrations:
                raise RecoveryBlocked('Scope already exists with different bounds/sources; never reset its allowance.')

    @contextmanager
    def _ledger(self):
        import fcntl
        with self._mutex:
            lock = _safe_path(self.ledger_path.with_name(self.ledger_path.name + '.lock'))
            with lock.open('a+b') as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    history = json.loads(self.ledger_path.read_text(encoding='utf-8'))
                    if (type(history.get('requests')) is not int or type(history.get('limit')) is not int
                            or not 0 <= history['requests'] <= history['limit']
                            or type(history.get('results')) is not list):
                        raise RecoveryBlocked('Linked recovery ledger is invalid; not resetting counters.')
                    before = _wire(history)
                    yield history
                    if _wire(history) != before:
                        _write(self.ledger_path, json.dumps(history, ensure_ascii=False, indent=2) + '\n')
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _scope(self, history):
        record = history['authorized_scopes'][self.scope]
        attempts = record.get('attempts')
        if (type(attempts) is not list or type(record.get('nodes')) is not dict
                or type(record.get('requests')) is not int or record['requests'] != len(attempts)
                or not 0 <= record['requests'] <= self.max_calls
                or record.get('max_calls') != self.max_calls
                or record.get('registrations') != self.registrations
                or type(record.get('unknown_retries_used')) is not int):
            raise RecoveryBlocked('Authorization ledger is inconsistent; retained without new allowance.')
        return record

    def for_source(self, source_id):
        if source_id not in self.registrations:
            raise RecoveryBlocked('Source was not explicitly registered as failed.')
        bound = copy.copy(self)
        bound.source_id = source_id
        bound._local = threading.local()
        return bound

    def registered(self, name):
        return self.source_id in self.registrations and logical_node(name) in self.registrations[self.source_id]

    @contextmanager
    def node_scope(self, name):
        previous = getattr(self._local, 'node', None)
        self._local.node = logical_node(name)
        try:
            yield
        finally:
            self._local.node = previous

    def wrap_request(self, compiler, request):
        # Explicit transport avoids DocumentCompiler._request's hidden fallback billing.
        raw_request = self.runner if self.runner is not None else request
        def bounded(model, prompt, content):
            return self._request(model, prompt, content, raw_request)
        return bounded

    def _response_path(self, attempt):
        return _safe_path(self.ledger_path.parent / ('authorized-' + _sha(self.scope)[:16])
                          / ('response-' + str(attempt['number']) + '.json'))

    def _read_response(self, attempt):
        path = self._response_path(attempt)
        if not path.exists():
            return None
        text = path.read_text(encoding='utf-8')
        if attempt.get('response_sha256') and _sha(text) != attempt['response_sha256']:
            raise RecoveryBlocked('Saved paid response changed; no retry or overwrite.')
        envelope = json.loads(text)
        if envelope.get('request_hash') != attempt['request_hash']:
            raise RecoveryBlocked('Response does not match its durable reservation.')
        return self._extract(envelope)

    @staticmethod
    def _extract(envelope):
        raw = envelope['raw']
        if envelope.get('error'):
            raise KnowledgeResponseError(envelope['error'], content=raw if isinstance(raw, str) else '')
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict) and 'provider_response' in raw:
            payload = raw['provider_response']
            try:
                choice = payload['choices'][0]
                content = choice['message']['content']
                if not isinstance(content, str):
                    raise ValueError('non-text content')
                if choice.get('finish_reason') == 'length':
                    raise KnowledgeResponseError('知识库响应被截断；完整原响应已保存，未发布。', content=content)
                return content
            except (KeyError, IndexError, TypeError, ValueError):
                raise KnowledgeResponseError('知识库响应格式无效；完整原响应已保存。', content=_wire(payload)) from None
        if isinstance(raw, dict):
            content = raw.get('content')
            if raw.get('finish_reason') == 'length' or (raw.get('response_metadata') or {}).get('finish_reason') == 'length':
                raise KnowledgeResponseError('知识库响应被截断；原响应已保存，未发布。', content=content or '')
            if isinstance(content, str):
                return content
        raise RecoveryBlocked('Provider response has no text; immutable raw response retained.')

    def _request(self, model, prompt, content, request):
        node = getattr(self._local, 'node', None)
        if self.source_id not in self.registrations or not node:
            raise RecoveryBlocked('Request has no registered source/node scope; no implicit billing.')
        kind = 'review' if '来源审校员' in prompt or '审校员' in prompt else 'generation'
        identity = _sha(_wire([model, prompt, content]))
        with self._ledger() as history:
            scope = self._scope(history)
            previous = [a for a in scope['attempts'] if a['source'] == self.source_id
                        and a['node'] == node and a['request_hash'] == identity]
        # Prefer an exact durable response even if an earlier same-input attempt is unknown.
        for attempt in reversed(previous):
            response = self._read_response(attempt)
            if response is not None:
                return response
        cached = self.cache_lookup(model, prompt, content) if self.cache_lookup is not None else None
        if cached is not None:
            if previous:
                self._store_response(previous[-1], cached)
            return self._extract({'raw': cached})
        if not self.allow_ai or self.offline:
            if previous:
                raise RecoveryOutcomeUnknown('Reserved attempt has no saved response (outcome unknown); offline cannot resend.')
            raise RecoveryBlocked('Offline/unauthorized recovery needs a new response; no AI request made.')
        with self._ledger() as history:
            scope = self._scope(history)
            # Pending matching requests from concurrent processes cannot race into a duplicate call.
            matches = [a for a in scope['attempts'] if a['source'] == self.source_id
                       and a['node'] == node and a['request_hash'] == identity]
            if matches and (not previous or matches[-1]['number'] != previous[-1]['number']):
                raise RecoveryOutcomeUnknown('Another process reserved this request; do not automatically resend.')
            answered_retries = {a['number'] for a in scope['attempts'] if self._response_path(a).is_file()}
            unknown = [a for a in scope['attempts'] if a['source'] == self.source_id
                       and a['node'] == node and not self._response_path(a).is_file()
                       and a.get('retried_by') not in answered_retries]
            if unknown and scope['unknown_retries_used'] >= self.retry_unknown:
                raise RecoveryOutcomeUnknown('Reserved attempt has no response: outcome unknown; explicit bounded retry opt-in required.')
            if scope['requests'] >= self.max_calls or history['requests'] >= history['limit']:
                raise RecoveryBudgetExhausted('Durable total AI request budget exhausted; old counters retained.')
            generations = sum(a['source'] == self.source_id and a['node'] == node and a['kind'] == 'generation'
                              for a in scope['attempts'])
            if kind == 'generation' and generations >= 2:
                raise RecoveryBudgetExhausted('Failed node has used its two generation reservations; reviews share the total budget.')
            history['requests'] += 1
            scope['requests'] += 1
            if unknown:
                scope['unknown_retries_used'] += 1
            attempt = {'number': history['requests'], 'source': self.source_id, 'node': node,
                       'kind': kind, 'model': model, 'request_hash': identity, 'status': 'reserved'}
            scope['attempts'].append(attempt)
            for unresolved in unknown:
                unresolved['retried_by'] = attempt['number']
        # Reservation is fsynced BEFORE touching the provider, including timeouts/cancellation.
        captured = False
        def capture(payload):
            nonlocal captured
            self._store_response(attempt, {'provider_response': payload})
            captured = True
        try:
            if getattr(request, 'supports_raw_capture', False):
                raw = request(model, prompt, content, raw_response=capture)
            else:
                raw = request(model, prompt, content)
        except KnowledgeResponseError as exc:
            if not captured:
                self._store_response(attempt, exc.content, error=str(exc))
            raise
        except BaseException:
            if captured:
                # The full provider response is authoritative, even when the client's
                # post-response parser rejects it. Validate the durable raw data first.
                return self._read_response(attempt)
            with self._ledger() as history:
                row = next(a for a in self._scope(history)['attempts'] if a['number'] == attempt['number'])
                row['status'] = 'outcome-unknown'
            raise
        if not captured:
            self._store_response(attempt, raw)
        text = self._read_response(attempt)
        store = getattr(request, 'cache_store', None)
        if store is not None:
            store(model, prompt, content, text)
        return text

    def _store_response(self, attempt, raw, *, error=None):
        envelope = {'request_hash': attempt['request_hash'], 'raw': raw, 'error': error}
        text = json.dumps(envelope, ensure_ascii=False, indent=2) + '\n'
        _write(self._response_path(attempt), text, immutable=True)
        with self._ledger() as history:
            row = next(a for a in self._scope(history)['attempts'] if a['number'] == attempt['number'])
            row.update(status='response-saved', response_sha256=_sha(text))

    def _key(self, compiler, name):
        return compiler.prefix + 'authorized-recovery-v1/' + _sha(self.scope)[:16] + '/' + self.source_id + '/' + logical_node(name)

    def _freeze(self, compiler, key, value):
        raw = compiler.cached(key)
        if raw is None:
            compiler.commit({key: _wire(value)})
            return copy.deepcopy(value)
        return _json(raw)

    @staticmethod
    def _source_input(data):
        # Drafts, error text, prompts and dates are not evidence identities.
        return {key: data[key] for key in ('kind', 'pages', 'input_summaries', 'topic_ids', 'source_budget') if key in data}

    def reject(self, compiler, name, draft, issues):
        identity = _sha(_wire(draft))
        with self._ledger() as history:
            node = self._scope(history)['nodes'].setdefault(self.source_id + ':' + logical_node(name), {})
            node.setdefault('rejected', {})[identity] = list(issues)

    def retained_candidates(self, compiler, name):
        """Read registered immutable old paid checkpoints; never mutate or fill fields."""
        root = compiler.prefix + name.removesuffix('.json')
        keys = self.checkpoint_keys.get(self.source_id, ())
        for key in reversed(tuple(keys)):
            if not key.startswith(root) or not any(term in key for term in
                    ('-resume-v1-', '-response-v1-', '-input.json')):
                continue
            if '-failed-response' in key:
                continue
            raw = compiler.cached(key)
            if raw is None:
                continue
            try:
                value = _json(raw)
                if 'resume-v1' in key:
                    if any(term in str(value.get('error', '')) for term in ('截断', '达到长度上限')):
                        continue
                    value = _json(value['draft']) if isinstance(value.get('draft'), str) else value.get('draft')
                elif '-input.json' in key:
                    value = value.get('data', {}).get('draft')
                if isinstance(value, dict) and 'markdown' in value:
                    yield value
            except (ValueError, TypeError, KeyError):
                continue

    def recover(self, compiler, name, prompt, data, validator, draft, base):
        if not self.registered(name) or not any(k in data for k in ('pages', 'input_summaries')):
            raise RecoveryBlocked('Recovery is restricted to explicitly registered failed source nodes.')
        key = self._key(compiler, name)
        input_hash = _sha(_wire(self._source_input(data)))
        frozen = self._freeze(compiler, key + '-input.json', {'input_hash': input_hash, 'prompt': prompt,
                              'data': copy.deepcopy(data), 'draft': copy.deepcopy(draft)})
        if frozen.get('input_hash') != input_hash:
            raise RecoveryBlocked('Frozen node evidence changed; no new allowance or old approval reuse.')
        with self._ledger() as history:
            node = self._scope(history)['nodes'].setdefault(self.source_id + ':' + logical_node(name), {})
            if node.get('input_hash', input_hash) != input_hash:
                raise RecoveryBlocked('Frozen authorization evidence mismatch.')
            node.update(input_hash=input_hash, input_key=key + '-input.json')
            rejected = copy.deepcopy(node.get('rejected', {}))
        local = self.local_candidates.get(self.source_id, {}).get(logical_node(name))
        if local is not None and local['input_hash'] != input_hash:
            raise RecoveryBlocked('Local candidate input hash differs from the frozen node evidence.')
        retained, error = draft, ''
        first_failure = True
        def provenance(candidate, result, origin, derivation):
            record = {'input_hash': input_hash, 'source_id': self.source_id, 'node': logical_node(name),
                      'origin': origin, 'derivation': derivation, 'candidate': copy.deepcopy(result),
                      'original_candidate_sha256': _sha(_wire(candidate)),
                      'candidate_sha256': _sha(_wire(result)), 'requires_source_review': True}
            record_key = key + '-candidate-' + _sha(_wire(record)) + '.json'
            self._freeze(compiler, record_key, record)
        def validate(candidate, *, origin=None):
            if isinstance(candidate, str):
                candidate = _json(candidate)
            original = copy.deepcopy(candidate)
            original_hash = _sha(_wire(original))
            if original_hash in rejected:
                raise ValueError('Source review rejected this exact draft: ' + '; '.join(rejected[original_hash]))
            normalized = False
            try:
                result = validator(copy.deepcopy(candidate))
            except ValueError as exc:
                markdown = candidate.get('markdown') if isinstance(candidate, dict) else None
                if (not isinstance(markdown, str) or not any(term in str(exc) for term in ('正文当前', '正文超过5000'))
                        or len(markdown) <= 4850 and len('# ' + str(candidate.get('title', '')) + '\n\n' + markdown + '\n') <= 5000):
                    raise
                candidate = copy.deepcopy(candidate)
                candidate['markdown'] = remove_strong_emphasis(markdown)
                if candidate['markdown'] == markdown:
                    raise
                result = validator(candidate)
                normalized = True
            issues = rejected.get(_sha(_wire(result)))
            if issues:
                raise ValueError('Source review rejected this exact draft: ' + '; '.join(issues))
            if normalized or origin == 'explicit-local-candidate':
                provenance(original, result, origin or 'retained-draft',
                           'remove-paired-strong-markers-only' if normalized else 'none')
            return result
        # Reuse every saved slot before attempting requests or hitting generation limits.
        candidates = [(draft, None), (frozen.get('draft'), None)]
        candidates.extend((candidate, None) for candidate in self.retained_candidates(compiler, name))
        for slot in range(2):
            raw_key = key + '-slot-' + str(slot) + '-response.txt'
            raw = compiler.cached(raw_key)
            if raw is not None and compiler.cached(raw_key + '-failure.json') is None:
                candidates.append((raw, 'saved-authorized-response'))
        if local is not None:
            candidates.append((copy.deepcopy(local['candidate']), 'explicit-local-candidate'))
        # Current structural validation is always the first, zero-network operation.
        for candidate, origin in candidates:
            if candidate is None:
                continue
            try:
                result = validate(candidate, origin=origin)
            except (ValueError, TypeError, AttributeError) as exc:
                if origin == 'explicit-local-candidate':
                    raise RecoveryBlocked('Explicit local candidate rejected; no replacement generation requested: ' + str(exc)) from None
                if first_failure:
                    retained, error, first_failure = candidate, str(exc), False
            else:
                compiler.commit({base: _wire(result)})
                return result
        if retained is None:
            raise RecoveryBlocked('No retained failed draft; not regenerating saved source pages.')
        known = ('精炼总结', '精炼总结合并', '整份精炼总结', '主题讲义页码无效',
                 '讲义引用页码无效', '讲义数学符号不一致', 'Source review rejected',
                 '知识库响应被截断', '文本字段', '讲义表格')
        if not error.startswith(known):
            raise RecoveryBlocked('Unrecognized failure; no automatic AI budget granted: ' + error)
        from .summary_policy import length_request, cache_friendly_data
        for slot in range(2):
            actual_prompt, actual_data = length_request(frozen['prompt'] + RECOVERY_GUIDANCE,
                {**frozen['data'], 'draft': retained, 'validation_error': error, 'authorized_repair_slot': slot})
            value = {'prompt': actual_prompt, 'data': cache_friendly_data(actual_data)}
            request_input = self._freeze(compiler, key + '-slot-' + str(slot) + '-input.json', value)
            raw_key = key + '-slot-' + str(slot) + '-response.txt'
            failed_key = raw_key + '-failure.json'
            failure = compiler.cached(failed_key)
            raw = compiler.cached(raw_key)
            if raw is None:
                try:
                    with self.node_scope(name):
                        raw = compiler.request('deepseek-chat', request_input['prompt'],
                                               json.dumps(request_input['data'], ensure_ascii=False))
                except KnowledgeResponseError as exc:
                    raw = exc.content
                    compiler.commit({raw_key: raw, failed_key: _wire({'error': str(exc)})})
                    failure = _wire({'error': str(exc)})
                else:
                    # Raw, untrimmed, and immutable before parsing/validation.
                    compiler.commit({raw_key: raw})
            try:
                if failure:
                    raise ValueError(_json(failure)['error'])
                result = validate(raw, origin='saved-authorized-response')
            except (ValueError, TypeError, AttributeError) as exc:
                retained, error = raw, str(exc)
                continue
            compiler.commit({base: _wire(result)})
            return result
        raise RecoveryBudgetExhausted(error or 'Both frozen recovery slots failed; checkpoints retained.')

    def status(self):
        with self._ledger() as history:
            scope = self._scope(history)
            return {'scope': self.scope, 'requests': scope['requests'], 'max_calls': self.max_calls,
                    'linked_requests': history['requests'], 'linked_limit': history['limit'],
                    'outcome_unknown': sum(not self._response_path(a).is_file() for a in scope['attempts'])}
