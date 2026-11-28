"""安装前验证与退出后原子替换；仅处理本机协调器产生的私有清单。"""
from __future__ import annotations

import ctypes
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from sjtu_learning_assistant.update_service import UpdateServiceError, SemanticVersion


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, check=True, capture_output=True, timeout=timeout,
                              env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateServiceError('新版应用验证或安装准备失败，原版本保持可用。') from exc


def validate_macos_bundle(bundle: Path, installed: Path, version: str) -> None:
    try:
        info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
        previous = plistlib.loads((installed / 'Contents/Info.plist').read_bytes())
        name = info.get('CFBundleExecutable')
        if not isinstance(name, str) or Path(name).name != name or name != 'SJTU Learning Assistant':
            raise ValueError('executable')
        if (info.get('CFBundleIdentifier') != previous.get('CFBundleIdentifier')
                or not info.get('CFBundleIdentifier') or info.get('CFBundleShortVersionString') != version
                or info.get('CFBundleVersion') != version):
            raise ValueError('identity')
        if not (bundle / 'Contents/MacOS' / name).is_file():
            raise ValueError('missing executable')
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        raise UpdateServiceError('新版应用身份或版本与官方发布记录不一致。') from exc
    _run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)])


def extract_windows_package(package: Path, output: Path) -> Path:
    """不接受穿越、符号链接、重复路径或解压炸弹。"""
    with zipfile.ZipFile(package) as archive:
        infos = archive.infolist()
        if len(infos) > 50000 or sum(i.file_size for i in infos) > 4 * 1024 ** 3:
            raise UpdateServiceError('Windows 安装包超出解压预算。')
        seen = set()
        for info in infos:
            name = info.filename
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith('/') or '\\' in name or ':' in name
                    or any(p in {'.', '..'} or p.endswith((' ', '.')) for p in parts)
                    or any(ord(c) < 32 for c in name) or len(name) > 1024
                    or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1):
                raise UpdateServiceError('Windows 安装包含不安全文件路径。')
            folded = name.rstrip('/').casefold()
            if folded in seen:
                raise UpdateServiceError('Windows 安装包含重复路径。')
            seen.add(folded)
            if info.file_size > 1024 ** 3 or (info.file_size > 1024 ** 2 and info.file_size > max(info.compress_size, 1) * 500):
                raise UpdateServiceError('Windows 安装包压缩比例异常。')
        archive.extractall(output)
    candidates = list(output.glob('*/SJTU Learning Assistant.exe'))
    if (output / 'SJTU Learning Assistant.exe').is_file():
        candidates.append(output / 'SJTU Learning Assistant.exe')
    if len(candidates) != 1:
        raise UpdateServiceError('Windows 安装包缺少唯一应用目录。')
    return candidates[0].parent


def prepare_package(package: Path, target: Path, version: str, job: Path) -> Path:
    SemanticVersion.parse(version)
    if not target.is_dir() or target.is_symlink():
        raise UpdateServiceError('当前安装位置不可安全更新，请重新安装应用。')
    staged = target.with_name('.' + target.name + '.update-' + uuid.uuid4().hex)
    # 在下载完成后、退出前确认可写，避免退出后才发现 /Applications 权限不足。
    try:
        staged.mkdir(mode=0o700)
    except OSError as exc:
        raise UpdateServiceError('当前安装目录不可写，请从发布页安装到有权限的位置。') from exc
    try:
        if sys.platform == 'darwin':
            mount = job / 'mount'
            mount.mkdir()
            attached = False
            try:
                _run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen',
                      '-mountpoint', str(mount), str(package)])
                attached = True
                apps = list(mount.glob('*.app'))
                if len(apps) != 1 or apps[0].is_symlink():
                    raise UpdateServiceError('安装映像缺少唯一应用。')
                validate_macos_bundle(apps[0], target, version)
                shutil.copytree(apps[0], staged, symlinks=True, dirs_exist_ok=True)
            finally:
                if attached:
                    _run(['/usr/bin/hdiutil', 'detach', str(mount)])
            validate_macos_bundle(staged, target, version)
            executable = staged / 'Contents/MacOS/SJTU Learning Assistant'
        elif sys.platform == 'win32':
            extracted = extract_windows_package(package, job / 'extracted')
            shutil.copytree(extracted, staged, dirs_exist_ok=True)
            # 保留原安装器卸载入口，用户数据始终在 AppSupport 而非安装目录。
            for old in target.glob('unins*'):
                if old.is_file() and old.suffix.lower() in {'.exe', '.dat', '.msg'}:
                    shutil.copy2(old, staged / old.name)
            executable = staged / 'SJTU Learning Assistant.exe'
        else:
            raise UpdateServiceError('此系统暂不支持自动安装。')
        report = job / 'startup.json'
        _run([str(executable), '--startup-self-test', str(report)], timeout=180)
        result = json.loads(report.read_text(encoding='utf-8'))
        if (result.get('result') != 'passed' or not result.get('data_isolated')
                or not result.get('network_blocked')):
            raise UpdateServiceError('新版应用启动验证未通过，原版本保持可用。')
        if sys.platform == 'win32':
            # Windows 版本资源由发行门禁绑定；本机再次读取 PE 文件版本。
            from sjtu_learning_assistant.update_windows_version import file_version
            if file_version(executable) != version:
                raise UpdateServiceError('Windows 新版应用版本与发布记录不一致。')
        return staged
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise


def parent_alive(pid: int) -> bool:
    if sys.platform == 'win32':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_uint32()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def atomic_install(staged: Path, target: Path, launch) -> Path:
    """保留旧包；替换或重启失败时恢复，绝不改动资料/配置。"""
    backup = target.with_name('.' + target.name + '.backup-' + uuid.uuid4().hex)
    target.rename(backup)
    try:
        staged.rename(target)
        launch(target)
    except Exception:
        if target.exists():
            target.rename(staged)
        backup.rename(target)
        launch(target)
        raise
    return backup


def apply_update(manifest: Path) -> int:
    from sjtu_learning_assistant.automatic_update import write_state
    from sjtu_learning_assistant.database import APP_SUPPORT_DIR
    status = APP_SUPPORT_DIR / 'updates/status.json'
    try:
        root = (APP_SUPPORT_DIR / 'updates').resolve()
        if manifest.is_symlink() or not manifest.resolve().is_relative_to(root) or manifest.stat().st_size > 8192:
            return 2
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if Path(data['status']).resolve() != status.resolve():
            return 2
        target, staged = Path(data['target']), Path(data['staged'])
        if (not target.is_absolute() or not staged.is_absolute() or target.is_symlink() or staged.is_symlink()
                or target.parent != staged.parent or not re.fullmatch(r'\.' + re.escape(target.name) + r'\.update-[a-f0-9]{32}', staged.name)):
            return 2
        if type(data['parent_pid']) is not int or data['parent_pid'] <= 1:
            return 2
        SemanticVersion.parse(data['version'])
        deadline = time.monotonic() + 300
        while parent_alive(data['parent_pid']):
            if time.monotonic() > deadline:
                raise UpdateServiceError('应用未能安全退出，更新已停止，原版本仍可使用。')
            time.sleep(.25)
        if sys.platform == 'darwin':
            validate_macos_bundle(staged, target, data['version'])
        def launch(app):
            if sys.platform == 'darwin':
                _run(['/usr/bin/open', '-n', str(app)])
            else:
                subprocess.Popen([str(app / 'SJTU Learning Assistant.exe')], cwd=app,
                                 env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})
        write_state(status, dict(phase='installing', version=data['version'], message='正在安装新版…'))
        backup = atomic_install(staged, target, launch)
        write_state(status, dict(phase='completed', version=data['version'], message='更新已安装并重新启动。', backup=str(backup)))
        return 0
    except Exception:
        write_state(status, dict(phase='failed', message='自动安装未完成；原安装包已保留，可重新检查更新。'))
        return 2
