"""官方 GitHub 安装包的后台下载、校验与本机安装协调。"""
from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

import certifi
import httpx
from sjtu_learning_assistant.update_service import (
    DownloadPlan, UpdateService, UpdateServiceError, normalize_sha256,
    require_sha256, validate_update_url,
)

MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
ACTIVE_PHASES = frozenset({"downloading", "verifying", "preparing", "waiting", "installing"})


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def download_package(service: UpdateService, plan: DownloadPlan, destination: Path,
                     progress: Callable[[int], None], cancelled: threading.Event) -> Path:
    """仅接受后端计划，每跳验证 HTTPS/主机，精确大小与 SHA-256 双重校验。"""
    if type(plan.size) is not int or not 0 < plan.size <= MAX_PACKAGE_BYTES:
        raise UpdateServiceError('安装包大小无效或超过限制。')
    expected = normalize_sha256(plan.sha256)
    current = validate_update_url(plan.download_url, purpose='asset')
    partial = destination.with_suffix('.part')
    deadline = time.monotonic() + 1800
    tls = ssl.create_default_context()
    tls.load_verify_locations(cafile=certifi.where())
    try:
        with httpx.Client(transport=service.transport, timeout=service.timeout,
                          verify=tls, trust_env=False, follow_redirects=False,
                          headers={'Accept-Encoding': 'identity', 'User-Agent': 'SJTU-Learning-Assistant-Updater'}) as client:
            for _ in range(4):
                with client.stream('GET', current) as response:
                    if response.is_redirect:
                        location = response.headers.get('location')
                        if not location:
                            raise UpdateServiceError('安装包下载重定向无效。')
                        current = validate_update_url(urljoin(current, location), purpose='asset')
                        continue
                    response.raise_for_status()
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise UpdateServiceError('安装包传输编码无效。')
                    length = response.headers.get('content-length')
                    if length is not None and int(length) != plan.size:
                        raise UpdateServiceError('安装包大小与官方发布记录不一致。')
                    count = 0
                    with partial.open('xb') as handle:
                        for chunk in response.iter_bytes(1024 * 1024):
                            if cancelled.is_set():
                                raise UpdateServiceError('更新下载已取消。')
                            if time.monotonic() > deadline:
                                raise UpdateServiceError('安装包下载超时，请重试。')
                            count += len(chunk)
                            if count > plan.size:
                                raise UpdateServiceError('安装包超出声明大小。')
                            handle.write(chunk)
                            progress(count)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if count != plan.size:
                        raise UpdateServiceError('安装包下载不完整。')
                    require_sha256(partial, expected)
                    os.replace(partial, destination)
                    return destination
            raise UpdateServiceError('安装包下载重定向次数过多。')
    except UpdateServiceError:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise UpdateServiceError('GitHub 安装包下载失败，请检查网络与可用磁盘空间后重试。') from exc
    finally:
        partial.unlink(missing_ok=True)


class AutomaticUpdater:
    def __init__(self, service: UpdateService, root: Path, target: Path,
                 shutdown: Callable[[Callable[[], None]], None]) -> None:
        self.service, self.root, self.target, self.shutdown = service, root, target, shutdown
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.thread: threading.Thread | None = None
        self.state_path = root / 'status.json'
        self.state = dict(phase='idle', message='检查到新版后会自动下载并安装。', received=0, total=0)
        if self.state_path.is_file():
            try:
                saved = json.loads(self.state_path.read_text(encoding='utf-8'))
                if saved.get('phase') in ACTIVE_PHASES:
                    saved.update(phase='failed', message='上次更新未完成，可重新检查更新。')
                self.state = saved
            except (ValueError, OSError):
                pass

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def _state(self, **changes) -> None:
        with self.lock:
            self.state.update(changes)
            write_state(self.state_path, self.state)

    def start(self, plan: DownloadPlan) -> dict:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return dict(self.state)
            if not plan.sha256:
                raise UpdateServiceError('官方安装包缺少 SHA-256，无法自动安装，请查看发布页。')
            self.cancelled.clear()
            self._state(phase='downloading', version=plan.version, received=0, total=plan.size,
                        message=f'正在从 GitHub 下载 {plan.version}…')
            self.thread = threading.Thread(target=self._run, args=(plan,), daemon=True, name='official-update')
            self.thread.start()
            return dict(self.state)

    def cancel(self) -> dict:
        with self.lock:
            if self.state.get('phase') not in {'downloading', 'verifying', 'preparing'}:
                raise UpdateServiceError('安装已准备就绪，当前阶段不能取消。')
            self.cancelled.set()
            return dict(self.state)

    def _run(self, plan: DownloadPlan) -> None:
        staged: Path | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            job = Path(tempfile.mkdtemp(prefix='job-', dir=self.root))
            package = job / ('release.dmg' if sys.platform == 'darwin' else 'release.zip')
            last_write = 0.0
            def progress(received):
                nonlocal last_write
                with self.lock:
                    self.state['received'] = received
                if time.monotonic() - last_write >= 1:
                    last_write = time.monotonic()
                    self._state(received=received)
            download_package(self.service, plan, package, progress, self.cancelled)
            self._state(phase='verifying', message='安装包 SHA-256 校验已通过。')
            if self.cancelled.is_set():
                raise UpdateServiceError('更新已取消。')
            from sjtu_learning_assistant.update_installer import prepare_package
            self._state(phase='preparing', message='正在验证新版应用与本机安装位置…')
            staged = prepare_package(package, self.target, plan.version, job)
            if self.cancelled.is_set():
                raise UpdateServiceError('更新已取消。')
            manifest = job / 'install.json'
            write_state(manifest, dict(target=str(self.target), staged=str(staged), parent_pid=os.getpid(),
                                      version=plan.version, status=str(self.state_path)))
            self._state(phase='waiting', message='新版已就绪，正在保存任务并等待应用安全退出…')
            # Windows helper must run from a separate onedir so it never locks the installation.
            if sys.platform == 'win32':
                helper = job / 'helper'
                shutil.copytree(self.target, helper)
                executable = helper / Path(sys.executable).name
            else:
                executable = Path(sys.executable)
            def launch_helper():
                subprocess.Popen([str(executable), '--apply-update', str(manifest)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})
            self.shutdown(launch_helper)
        except Exception as exc:
            if staged and staged.exists():
                shutil.rmtree(staged, ignore_errors=True)
            message = str(exc) if isinstance(exc, UpdateServiceError) else '更新未安装，当前版本仍可使用。请重试或打开发布页。'
            self._state(phase='cancelled' if self.cancelled.is_set() else 'failed', message=message)


def installed_target() -> Path | None:
    if not getattr(sys, 'frozen', False):
        return None
    if sys.platform == 'darwin':
        path = Path(sys.executable).resolve()
        if path.parent.name == 'MacOS' and path.parent.parent.name == 'Contents':
            return path.parent.parent.parent
    if sys.platform == 'win32':
        return Path(sys.executable).resolve().parent
    return None
