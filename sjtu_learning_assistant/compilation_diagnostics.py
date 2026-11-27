"""Read-only deterministic diagnostics. No model, credentials, writes or repairs."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import threading
import time
from itertools import islice
from pathlib import Path
from .markdown_progress import _safe, course_progress, transcript_progress
DIAGNOSTIC_POLICY = 'readonly-local-revalidation-20261125-v1'
REASONS = dict(
    ready=('本地记录未发现阻断项。', 'continue_existing_checkpoint'),
    source_changed=('来源与检查点身份不同；原件与旧稿保留。', 'confirm_source_revision'),
    source_unavailable=('来源路径不可用或不安全。', 'check_local_source'),
    manifest_corrupt=('编译记录损坏或无法校验；不覆盖。', 'inspect_preserved_manifest'),
    manual_edit=('输出已变化或被人工编辑；自动操作停止。', 'preserve_user_edit'),
    requires_authorized_segmented_repair=('既有恢复预算已耗尽；需显式有界分段修复。', 'authorize_segmented_repair'),
    checkpoint_compatible=('旧草稿结构可离线校验，但未据此伪造来源审校。', 'authorize_checkpoint_revalidation'),
    checkpoint_incompatible=('旧草稿仍未通过结构或来源页码校验。', 'authorize_segmented_repair'),
    renderer_status_only=('渲染器或路径异常；仅核验状态，不自动重发模型请求。', 'check_local_renderer'),
    lock_status_only=('锁由操作系统管理；不删除锁文件。', 'check_active_worker'),
    account_payment_required=('账户或额度异常；不自动付费或重试。', 'check_provider_account'),
    request_outcome_unknown=('请求结果不确定；保留预约，不自动重发。', 'inspect_preserved_response'),
    runtime_config=('运行配置未就绪；不读取凭据。', 'check_settings'),
    static_source_only=('原件仅收纳；静态证据或缺口须核验。', 'inspect_static_gaps'),
    local_scan_limited=('检查点过大，本地诊断已达到有界读取上限。', 'inspect_large_checkpoint'))

def _result(code, item=None):
    reason, action = REASONS[code]
    item = item or dict()
    return dict(code=code, reason=reason, next_action=action, automatic_allowed=code == 'ready',
                attempts=max(0, int(item.get('attempts', 0))), diagnostic_checks=max(0, int(item.get('diagnostic_checks', 0))))

def _json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2097152:
        raise ValueError()
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if type(value) is not dict:
        raise ValueError()
    return value

def diagnose_source(source: Path, path: Path, target: Path, item: dict):
    """Local source/draft validation only; never instantiates a network compiler."""
    try:
        if not _safe(source, path) or not path.is_file() or path.stat().st_size > 104857600:
            return _result('source_unavailable', item)
        relative = path.relative_to(source).as_posix()
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if item.get('source_hash') and item.get('source_hash') != source_hash:
            return _result('source_changed', item)
        mirror = target / 'Courses' / (relative + '.md')
        if item.get('mirror_hash'):
            if not _safe(target, mirror) or not mirror.is_file() or hashlib.sha256(mirror.read_bytes()).hexdigest() not in (item.get('mirror_hash'), item.get('pending_hash')):
                return _result('manual_edit', item)
        source_id = hashlib.sha256(relative.encode()).hexdigest().__getitem__(slice(16))
        root = target / '90_Inbox' / 'Documents' / source_id
        compatible = False
        output_bytes = 0
        if root.exists():
            if not _safe(target, root):
                return _result('manifest_corrupt', item)
            for index, directory in enumerate(root.iterdir()):
                if index >= 20:
                    return _result('manifest_corrupt', item)
                manifest = directory / 'manifest.json'
                if not _safe(target, manifest):
                    return _result('manifest_corrupt', item)
                state = _json(manifest)
                if state.get('source') != relative or state.get('source_hash') != source_hash:
                    return _result('source_changed', item)
                outputs = state.get('outputs')
                if state.get('version') != 1 or type(outputs) is not dict or len(outputs) > 4050:
                    return _result('manifest_corrupt', item)
                for name, expected in outputs.items():
                    if type(name) is not str or type(expected) is not str:
                        return _result('manifest_corrupt', item)
                    candidate = directory / name
                    if not _safe(target, candidate) or not candidate.is_file() or candidate.stat().st_size > 104857600:
                        return _result('manifest_corrupt', item)
                    output_bytes += candidate.stat().st_size
                    if output_bytes > 67108864:
                        return _result('local_scan_limited', item)
                    if hashlib.sha256(candidate.read_bytes()).hexdigest() != expected:
                        return _result('manual_edit', item)
                    if name.startswith('summary/') and name.endswith('.json') and re.search(r'part-\d+', name):
                        draft = _json(candidate)
                        if type(draft.get('draft')) is str:
                            try:
                                draft = json.loads(draft['draft'])
                            except (ValueError, TypeError, RecursionError):
                                continue
                        if type(draft) is dict and 'markdown' in draft and 'covered_pages' in draft:
                            from .knowledge_summary import validate_summary
                            try:
                                declared = draft.get('covered_pages')
                                physical = set(int(n) for n in state.get('pages', dict()))
                                if type(declared) is not list or not declared or any(type(n) is not int for n in declared) or not set(declared) <= physical:
                                    continue
                                validate_summary(copy.deepcopy(draft), sorted(set(declared)), draft.get('covered_topics', list()), fragment=True)
                                compatible = True  # Structure only; no approval or invented total coverage.
                            except (ValueError, TypeError, KeyError):
                                pass
        error = str(item.get('error', '')).casefold()
        if '402' in error or any(t in error for t in ('payment', '余额', 'insufficient_balance')):
            return _result('account_payment_required', item)
        if any(t in error for t in ('timeout', '超时', 'outcome_unknown', 'uncertain', '未返回')):
            return _result('request_outcome_unknown', item)
        if any(t in error for t in ('lock', '另一个进程', '锁')):
            return _result('lock_status_only', item)
        if any(t in error for t in ('pdfium', 'renderer', '渲染', '路径')):
            return _result('renderer_status_only', item)
        if item.get('auto_paused') or int(item.get('attempts', 0)) >= 3:
            return _result('requires_authorized_segmented_repair', item)
        if item.get('static_reason'):
            return _result('static_source_only', item)
        if item.get('status') == 'failed' and any(t in error for t in ('精炼', '主题', 'summary', '校验', '审校', '引用', '修复额度')):
            return _result('checkpoint_compatible' if compatible else 'checkpoint_incompatible', item)
        return _result('ready', item)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return _result('manifest_corrupt')

def catalog_snapshot(source, candidates, transcripts, runtime=None, asr=None, ffmpeg_available=None):
    """Separate base-course MD, compiled summaries, raw transcripts and ASR summaries."""
    target = source / 'Knowledge Markdown'
    findings = list()
    try:
        marker = target / '99_System' / 'Course-Sync.json'
        if not _safe(source, marker):
            raise ValueError()
        state = _json(marker)
        if state.get('version') != 1 or state.get('source_root') != str(source) or type(state.get('items')) is not dict:
            raise ValueError()
        items = state.get('items')
        for path in islice(candidates, 100):
            relative = path.relative_to(source).as_posix()
            item = items.get(relative, dict())
            if item.get('status') in ('failed', 'retained') or item.get('auto_paused'):
                cached = item.get('diagnosis') or dict()
                code = cached.get('code') if item.get('diagnostic_policy') == DIAGNOSTIC_POLICY else None
                if code not in REASONS:
                    error = str(item.get('error', '')).casefold()
                    code = ('requires_authorized_segmented_repair' if item.get('auto_paused') or item.get('attempts', 0) >= 3 else
                            'account_payment_required' if '402' in error or '余额' in error else
                            'request_outcome_unknown' if '超时' in error or 'timeout' in error else
                            'renderer_status_only' if 'pdfium' in error or '渲染' in error else
                            'static_source_only' if item.get('status') == 'retained' else 'checkpoint_incompatible')
                finding = _result(code, item)
                finding['local_revalidation'] = 'cached' if cached else 'not_yet_checked'
                finding.update(source_id=hashlib.sha256(relative.encode()).hexdigest().__getitem__(slice(16)))
                findings.append(finding)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        findings.append(_result('manifest_corrupt'))
    runtime, asr = runtime or dict(), asr or dict()
    safe_counts = dict((key, value) for key, value in asr.items() if key in ('total', 'completed', 'failed', 'pending', 'running', 'skipped_source_text') and type(value) is int)
    def safe_group(group):
        group['issues'] = list(dict(code=row.get('code')) for row in group.get('issues', list()))
        return group
    return dict(policy=DIAGNOSTIC_POLICY, readonly=True,
                courses=safe_group(course_progress(source, candidates)), transcripts=safe_group(transcript_progress(transcripts)), findings=findings,
                runtime=dict(ai_ready=bool(runtime.get('ai_ready')), automatic=bool(runtime.get('automatic')), packaged_ffmpeg_available=ffmpeg_available),
                asr_counts=safe_counts, count_summary=dict(entries_reported=len(findings), requires_authorization=sum(f.get('code') == 'requires_authorized_segmented_repair' for f in findings)),
                truncated=len(candidates) > 100)

class DiagnosticSnapshotCache:
    """Seven-second async cache. UI calls never perform the heavy local scan."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, threading.Lock()
        self.value, self.updated, self.running = None, float('-inf'), False
    def snapshot(self, loader):
        with self.lock:
            if not self.running and self.clock() - self.updated >= 7:
                self.running = True
                threading.Thread(target=self._load, args=(loader,), daemon=True, name='compilation-diagnose').start()
            return copy.deepcopy(self.value) if self.value is not None else dict(readonly=True, status='scanning')
    def _load(self, loader):
        try:
            result = loader()
        except Exception:
            result = dict(readonly=True, status='unavailable', findings=list((_result('manifest_corrupt'),)))
        with self.lock:
            self.value, self.updated, self.running = result, self.clock(), False

def asr_snapshot_counts(root):
    """Inspect existing job records, without constructing/mutating the ASR service."""
    counts = dict(total=0, completed=0, failed=0, pending=0, running=0, skipped_source_text=0)
    jobs = root / 'jobs'
    if not _safe(root, jobs) or not jobs.is_dir():
        return counts
    for directory in islice(jobs.iterdir(), 5000):
        path = directory / 'job.json'
        if not _safe(root, path):
            continue
        try:
            job = _json(path)
            state = job.get('state')
            counts['total'] += 1
            key = 'skipped_source_text' if state == 'skipped_source_text' else 'completed' if state == 'completed' else 'failed' if state in ('failed', 'error') else 'running' if state in ('checking_source', 'extracting', 'transcribing', 'publishing') else 'pending'
            counts[key] += 1
        except (OSError, ValueError, TypeError, RecursionError):
            counts['failed'] += 1
    return counts

def ffmpeg_status():
    """Existence/executable bits only: no FFmpeg/version/subprocess/credential calls."""
    import os
    import sys
    import shutil
    if getattr(sys, 'frozen', False):
        root = getattr(sys, '_MEIPASS', None)
        if not root:
            return False
        path = Path(root) / 'ffmpeg' / 'bin' / ('ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg')
        return path.is_file() and os.access(path, os.X_OK)
    found = shutil.which('ffmpeg')
    return bool(found and Path(found).is_file())
