"""可重启的本学期临时下载转 MD 队列，与旧付费课件编译完全独立。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import threading
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .course_scope import latest_term_courses, term_identity
from .models import Course, CourseFile, SyncState
from .path_safety import is_macos_system_alias

SOURCE = 'current-term-md'
OUTPUT = 'Knowledge Markdown/Current Term'
MAX_FILE_BYTES = 500 * 1024**2


def safe_path(root: Path, relative: str = '') -> Path:
    root = Path(root)
    parts = PurePosixPath(relative).parts
    if not root.is_absolute() or PurePosixPath(relative).is_absolute() or any(p in {'.', '..'} for p in parts) or '\\' in relative or '\0' in relative:
        raise ValueError('临时转换路径无效。')
    path = root.joinpath(*parts)
    if any(p.is_symlink() and not is_macos_system_alias(p) for p in (path, *path.parents)):
        raise ValueError('临时转换路径包含符号链接，已停止。')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('临时转换路径越界。')
    return path


def file_fingerprint(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError('转换对象不是普通文件或超过上限。')
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        final = os.fstat(stream.fileno())
        identity = lambda s: [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]
        if identity(info) != identity(final):
            raise ValueError('校验期间文件发生变化，已保留。')
    return dict(size=info.st_size, sha256=digest.hexdigest(), identity=identity(info))


def version_of(file: CourseFile) -> str:
    stamp = file.source_updated_at
    if stamp is not None:
        stamp = stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    value = [file.source_id, file.course_id, stamp, file.size, file.display_name, file.filename]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def latest_ids(session) -> set[int]:
    rows = [dict(course.raw_data or {}, id=course.id, term_name=course.term_name)
            for course in session.scalars(select(Course))]
    selected = latest_term_courses(rows)
    # 无可靠学期时绝不把所有历史课程作为后备下载范围。
    return set() if selected is rows else {row['id'] for row in selected}


def _jobs(session, root: Path) -> dict[str, dict]:
    result = {}
    for state in session.scalars(select(SyncState).where(SyncState.source == SOURCE)):
        try:
            job = json.loads(state.cursor or '')
            if type(job) is dict and job.get('schema') == 1 and job.get('root') == str(root) and job.get('key') == state.resource:
                result[state.resource] = job
        except (ValueError, TypeError, RecursionError):
            continue
    return result


def verify_md(root: Path, job: dict, *, full: bool = False) -> Path:
    path = safe_path(root, job['md_path'])
    actual = file_fingerprint(path)
    expected = job['md_fingerprint']
    if actual['sha256'] != expected['sha256'] or actual['size'] != expected['size']:
        raise ValueError('MD 内容发生变化；临时原件已保留。')
    if actual['size'] > 24 * 1024**2 or not path.read_text(encoding='utf-8').strip():
        raise ValueError('MD 为空或不可读；临时原件已保留。')
    for relative, expected_asset in job.get('assets', {}).items():
        asset = safe_path(root, relative)
        if full:
            actual_asset = file_fingerprint(asset)
            if any(actual_asset[k] != expected_asset[k] for k in ('size', 'sha256')):
                raise ValueError('MD 资源校验失败；临时原件已保留。')
        elif not asset.is_file() or asset.stat().st_size != expected_asset['size']:
            raise ValueError('MD 资源不可用。')
    return path


def ready_records(engine, root: Path) -> list[dict]:
    if not callable(getattr(engine, 'connect', None)):
        return []
    root = Path(root)
    with Session(engine) as session:
        files = {f.source_id: (f, c) for f, c in session.execute(
            select(CourseFile, Course).join(Course).where(CourseFile.is_active.is_(True)))}
        jobs = _jobs(session, root)
        result = []
        for job in jobs.values():
            pair = files.get(job.get('source_id'))
            if not pair or job.get('state') not in {'md_ready', 'complete', 'retained'} or job.get('version') != version_of(pair[0]):
                continue
            try:
                verify_md(root, job)
            except (ValueError, OSError, KeyError, TypeError, UnicodeError):
                continue
            term, label = term_identity(pair[1].raw_data or {}, pair[1].term_name)
            result.append(dict(job, course_id=pair[1].source_id, course_name=pair[1].name,
                term_id=term, term_name=label, course_file_source_id=pair[0].source_id))
        return result


class CurrentTermMarkdown:
    def __init__(self, engine, settings, downloader, *, converter=None, interval=300, sync_lock=None):
        self.engine, self.settings, self.downloader = engine, settings, downloader
        if converter is None:
            from .current_term_md_conversion import convert_local
            converter = convert_local
        self.converter, self.interval, self.sync_lock = converter, interval, sync_lock
        self._stop, self._wake = threading.Event(), threading.Event()
        self._lock, self._thread = threading.Lock(), None
        self._active_root = None
        self._status = dict(status='idle', total=0, existing=0, completed=0, pending=0, failed=0, issues=[])

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='current-term-md', daemon=True)
            self._thread.start()

    def notify(self):
        self._wake.set()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()

    def status(self):
        return dict(self._status, enabled=self.settings().current_term_md_enabled)

    def _cancelled(self):
        settings = self.settings()
        return self._stop.is_set() or not settings.current_term_md_enabled or (
            self._active_root is not None and str(self._active_root) != settings.archive_root)

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.run_once()
            except Exception:
                self._status = dict(self._status, status='blocked', issues=['后台队列暂不可用，等待下次扫描。'])
            self._wake.wait(self.interval)

    def _save(self, root, job):
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            state = session.scalar(select(SyncState).where(SyncState.source == SOURCE, SyncState.resource == job['key']))
            if state is None:
                state = SyncState(source=SOURCE, resource=job['key'])
                session.add(state)
            elif state.cursor:
                previous = json.loads(state.cursor)
                if previous.get('schema') != 1 or previous.get('key') != job['key']:
                    raise ValueError('既有队列记录异常，保留并停止。')
            state.cursor = json.dumps(job, ensure_ascii=False)
            state.status = 'success' if job['state'] == 'complete' else 'idle'
            state.last_sync_at = now
            state.last_error = job.get('error')

    def _snapshot(self, root):
        with Session(self.engine) as session:
            ids = latest_ids(session)
            rows = list(session.scalars(select(CourseFile).where(
                CourseFile.is_active.is_(True), CourseFile.course_id.in_(ids)).order_by(CourseFile.id)))
            for row in rows:
                session.expunge(row)
            return rows, _jobs(session, root)

    @staticmethod
    def _existing(file, root):
        # 曾登记下载的一律保留，不自动把缺失/变更原件当成清理候选。
        if file.download_status == 'downloaded':
            return True
        if file.local_path:
            path = Path(file.local_path)
            if not path.is_absolute():
                path = root / path
            return path.exists() or path.is_symlink()
        return False

    def run_once(self):
        if self._cancelled():
            self._status = dict(self._status, status='disabled')
            return self.status()
        if not self._lock.acquire(blocking=False):
            return self.status()
        sync_acquired = False
        try:
            if self.sync_lock is not None:
                sync_acquired = self.sync_lock.acquire(blocking=False)
                if not sync_acquired:
                    return self.status()
            root = Path(self.settings().archive_root)
            self._active_root = root
            safe_path(root)
            rows, jobs = self._snapshot(root)
            summary = dict(status='running', total=len(rows), existing=0, completed=0, pending=0, failed=0, issues=[], scanned_at=datetime.now(timezone.utc).isoformat())
            for file in rows:
                if self._cancelled():
                    break
                version = version_of(file)
                key = hashlib.sha256((str(root) + '\0' + file.source_id + '\0' + version).encode()).hexdigest()
                job = jobs.get(key)
                if self._existing(file, root) and not job:
                    summary['existing'] += 1
                    continue
                if job and job['state'] in {'complete', 'retained'}:
                    try:
                        verify_md(root, job)
                        summary['completed'] += 1
                    except Exception:
                        summary['failed'] += 1
                        summary['issues'].append('已有 MD 校验失败，保留记录和原件，请检查。')
                    continue
                if job and job.get('state') == 'failed' and (job.get('attempts', 0) >= 3 or time.time() < job.get('retry_after', 0)):
                    summary['failed'] += 1
                    continue
                if not job:
                    job = dict(schema=1, root=str(root), key=key, version=version,
                        source_id=file.source_id, name=file.display_name or file.filename or '文件',
                        originally_downloaded=False, state='pending', attempts=0)
                self._status = dict(summary, current_file=job['name'])
                try:
                    self._process(root, file, job)
                    summary['completed'] += 1
                except InterruptedError:
                    summary['pending'] += 1
                    break
                except Exception as exc:
                    # 不把下载 URL、正文、凭据或异常路径写进状态。
                    job.update(state='failed', error='转换或校验未完成，临时原件保留。',
                        error_type=type(exc).__name__, retry_after=time.time()+3600)
                    self._save(root, job)
                    summary['failed'] += 1
            summary.update(status='idle' if not self._cancelled() else 'paused', current_file=None)
            summary['pending'] = max(summary['pending'], summary['total']-summary['existing']-summary['completed']-summary['failed'])
            self._status = summary
            with Session(self.engine) as session, session.begin():
                state = session.scalar(select(SyncState).where(SyncState.source == SOURCE, SyncState.resource == 'scan'))
                if state is None:
                    state = SyncState(source=SOURCE, resource='scan')
                    session.add(state)
                state.cursor = json.dumps(dict(summary, root=str(root)), ensure_ascii=False)
                state.status = 'success'
                state.last_sync_at = datetime.now(timezone.utc)
            return self.status()
        finally:
            self._active_root = None
            if sync_acquired:
                self.sync_lock.release()
            self._lock.release()

    def _fresh(self, root, file):
        with Session(self.engine) as session:
            fresh = session.get(CourseFile, file.id)
            if fresh is None or not fresh.is_active or fresh.course_id not in latest_ids(session) or version_of(fresh) != version_of(file):
                raise ValueError('来源版本或学期变化，临时原件保留。')
            return self._existing(fresh, root)

    def _process(self, root, file, job):
        job['attempts'] += 1
        self._save(root, job)
        if job.get('md_path'):
            verify_md(root, job, full=True)
            self._cleanup(root, file, job)
            return
        if self._fresh(root, file):
            raise ValueError('用户已下载原件，停止临时转换。')
        if not job.get('temporary_path'):
            # 下载器先提交收据和固定名字，再返回；进程中断也不重复下载。
            if job.get('cache'):
                recovered = safe_path(root, job['cache']+'/source.bin')
                receipt_path = safe_path(root, job['cache']+'/download.json')
                if recovered.is_file() and receipt_path.is_file():
                    receipt = json.loads(receipt_path.read_text())
                    fingerprint = file_fingerprint(recovered)
                    if receipt.get('source_id') != file.source_id or any(receipt.get(k) != fingerprint[k] for k in ('size', 'sha256')):
                        raise ValueError('已下载临时原件与收据不一致，停止。')
                    job.update(temporary_path=recovered.relative_to(root).as_posix(), temporary_fingerprint=fingerprint, state='downloaded')
                    self._save(root, job)
        if not job.get('temporary_path'):
            token = uuid.uuid4().hex
            relative = f'{OUTPUT}/.cache/{job["key"]}/{token}'
            directory = safe_path(root, relative)
            directory.mkdir(parents=True, exist_ok=False, mode=0o700)
            (directory / 'owner.json').write_text(json.dumps(dict(key=job['key'], token=token)))
            job.update(state='downloading', cache=relative, token=token)
            self._save(root, job)
            path, size, digest = self.downloader(file.source_id, directory, file.size, self._cancelled)
            path = Path(path)
            if path.parent != directory or path.is_symlink():
                raise ValueError('临时下载路径不属于当前任务。')
            fingerprint = file_fingerprint(path)
            if size != fingerprint['size'] or digest != fingerprint['sha256']:
                raise ValueError('临时下载校验失败。')
            job.update(temporary_path=path.relative_to(root).as_posix(), temporary_fingerprint=fingerprint, state='downloaded')
            self._save(root, job)
        source = safe_path(root, job['temporary_path'])
        if file_fingerprint(source) != job['temporary_fingerprint']:
            raise ValueError('临时原件发生变化，停止转换。')
        output = f'{OUTPUT}/Documents/{job["key"]}/{uuid.uuid4().hex}'
        converted = self.converter(source, job['name'], safe_path(root, output), self._cancelled)
        if self._cancelled():
            raise InterruptedError()
        if file_fingerprint(source) != job['temporary_fingerprint']:
            raise ValueError('转换期间原件变化，停止清理。')
        job.update(md_path=output+'/'+converted['md'], md_fingerprint=converted['md_fingerprint'],
            assets={output+'/'+p: fp for p, fp in converted['assets'].items()}, units=converted['units'], state='md_ready')
        verify_md(root, job, full=True)
        self._fresh(root, file)
        self._save(root, job)  # 正文、资源与来源版本确认后持久化，再执行清理。
        self._cleanup(root, file, job)

    def _cleanup(self, root, file, job):
        if self._cancelled():
            raise InterruptedError()
        verify_md(root, job, full=True)
        if self._fresh(root, file):
            job.update(state='retained', error='用户已下载原件，临时副本保留。')
            self._save(root, job)
            return
        cache = safe_path(root, job['cache'])
        owner = json.loads(safe_path(cache, 'owner.json').read_text())
        if owner != dict(key=job['key'], token=job['token']) or job.get('originally_downloaded') is not False:
            raise ValueError('临时原件所有权不匹配，停止清理。')
        path = safe_path(root, job['temporary_path'])
        if path.parent != cache:
            raise ValueError('临时原件不在任务缓存中。')
        if path.exists():
            if file_fingerprint(path) != job['temporary_fingerprint']:
                raise ValueError('临时原件身份或内容变化，停止清理。')
            # 只删除私有、逐任务隔离目录中本次下载的单个普通文件。
            path.unlink()
        job.update(state='complete', cleaned=True, error=None)
        self._save(root, job)
