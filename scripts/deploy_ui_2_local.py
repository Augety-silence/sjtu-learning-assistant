"""2.0.0 本机升级：正常退出、数据快照、可回退替换与设置恢复。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.local_settings import SettingsStore

APP = Path("/Applications/SJTU Learning Assistant.app")
TARGET = "2.0.0"


def active_requests():
    runtime = APP_SUPPORT_DIR / "ai-runtime.sqlite3"
    if not runtime.is_file():
        return dict(inflight=0, queued=0)
    with sqlite3.connect("file:" + quote(str(runtime)) + "?mode=ro", uri=True) as db:
        now = time.time()
        rows = db.execute("SELECT started FROM requests WHERE ended IS NULL AND lease>?", (now,)).fetchall()
    return dict(inflight=sum(r[0] is not None for r in rows), queued=sum(r[0] is None for r in rows))


def running():
    return subprocess.run(("pgrep", "-f", "^/Applications/SJTU Learning Assistant.app/Contents/MacOS/"), capture_output=True).returncode == 0


def write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def snapshot_files(roots):
    result = {}
    excluded = {"Backups", "recovery-backups", "logs", "run", "webview", ".git"}
    for root in roots:
        if not root.is_dir():
            continue
        for directory, folders, files in os.walk(root):
            folders[:] = [n for n in folders if n not in excluded]
            for name in files:
                path = Path(directory) / name
                if path.is_symlink() or path.suffix in {".db", ".sqlite3", ".sqlite"} or name.endswith(("-wal", "-shm")) or path == APP_SUPPORT_DIR / "settings.json":
                    continue
                stat = path.stat()
                entry = dict(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
                if path.suffix in {".md", ".txt"} and stat.st_size <= 2000000:
                    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                result[str(path)] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pause", "status", "install", "resume"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise RuntimeError("只能在 macOS 执行本机安装")
    store = SettingsStore()
    if args.action == "pause":
        if args.state.exists():
            raise RuntimeError("部署状态已存在，不覆盖")
        settings = store.load()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = Path.home() / "Applications/SJTU App Backups" / ("before-2.0.0-" + stamp)
        write_state(args.state, dict(ai_enabled=settings.ai_enabled, backup=str(backup), paused_at=time.time(), installed=False))
        store.save(replace(settings, ai_enabled=False))
        print(json.dumps(dict(paused=True, **active_requests())))
        return
    state = json.loads(args.state.read_text())
    if args.action == "status":
        print(json.dumps(dict(ai_enabled=store.load().ai_enabled, running=running(), **active_requests())))
        return
    if args.action == "resume":
        store.save(replace(store.load(), ai_enabled=bool(state["ai_enabled"])))
        subprocess.run(("open", "-a", str(APP)), check=True)
        state["resumed_at"] = time.time()
        write_state(args.state, state)
        print(json.dumps(dict(resumed=True, ai_enabled=store.load().ai_enabled, installed=state["installed"])))
        return
    if state["installed"] or store.load().ai_enabled or any(active_requests().values()):
        raise RuntimeError("需暂停新请求并等待在途任务结束，不重复安装")
    if args.bundle is None:
        raise RuntimeError("必须提供新应用路径")
    bundle = args.bundle.resolve(strict=True)
    with (bundle / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleShortVersionString") != TARGET or bundle.suffix != ".app":
        raise RuntimeError("应用版本不是2.0.0")
    subprocess.run(("codesign", "--verify", "--deep", "--strict", str(bundle)), check=True)
    if running():
        subprocess.run(("osascript", "-e", 'tell application "SJTU Learning Assistant" to quit'), check=True)
    deadline = time.monotonic() + 45
    while running():
        if time.monotonic() > deadline:
            raise RuntimeError("应用未正常退出；保留旧应用，请稍后继续安装")
        time.sleep(.2)
    backup = Path(state["backup"])
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    # 原学习资料保持原位，仅核对元数据和小文本哈希；SQLite使用一致性备份。
    settings = store.load()
    roots = [APP_SUPPORT_DIR]
    if settings.archive_root:
        archive = Path(settings.archive_root).expanduser()
        if archive != APP_SUPPORT_DIR and APP_SUPPORT_DIR not in archive.parents:
            roots.append(archive)
    before = snapshot_files(roots)
    (backup / "preserved-files.json").write_text(json.dumps(before, ensure_ascii=False))
    for path in APP_SUPPORT_DIR.rglob("*"):
        relative = path.relative_to(APP_SUPPORT_DIR)
        if not path.is_file() or path.is_symlink() or any(p in {"Backups", "recovery-backups", "logs", "run"} for p in relative.parts):
            continue
        if path.suffix not in {".db", ".sqlite", ".sqlite3"} and path != store.path:
            continue
        target = backup / "data" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if path == store.path:
            shutil.copy2(path, target)
        else:
            with sqlite3.connect("file:" + quote(str(path)) + "?mode=ro", uri=True) as source, sqlite3.connect(target) as dest:
                source.backup(dest)
                if dest.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("数据库备份校验未通过")
    webview = APP_SUPPORT_DIR / "webview"
    if webview.exists():
        shutil.copytree(webview, backup / "webview", symlinks=True)
    staged = APP.with_name("SJTU Learning Assistant 2.0.0 staging.app")
    if staged.exists():
        raise RuntimeError("暂存路径已存在，不覆盖")
    subprocess.run(("ditto", str(bundle), str(staged)), check=True)
    old = backup / "SJTU Learning Assistant.app"
    os.replace(APP, old)
    try:
        os.replace(staged, APP)
        subprocess.run(("codesign", "--verify", "--deep", "--strict", str(APP)), check=True)
        if before != snapshot_files(roots):
            raise RuntimeError("安装期间学习文件发生变化，回退应用")
    except BaseException:
        if APP.exists():
            os.replace(APP, backup / "failed-2.0.0.app")
        os.replace(old, APP)
        raise
    state.update(installed=True, installed_at=time.time(), protected_files=len(before))
    write_state(args.state, state)
    print(json.dumps(dict(installed=TARGET, backup=str(backup), protected_files=len(before), protected_files_changed=0), ensure_ascii=False))


if __name__ == "__main__":
    main()
