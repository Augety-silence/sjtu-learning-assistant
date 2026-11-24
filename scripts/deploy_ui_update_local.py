"""安全替换本机 App：暂停新请求，等响应落盘，保留旧包，恢复后台。

仅对显式指定的 1.8.18 本地发布使用，不读取或输出 API 密钥。
"""
import argparse
import json
import os
import plistlib
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

STATE = Path("/tmp/sjtu-ui-deploy-1.8.18.json")
APP = Path("/Applications/SJTU Learning Assistant.app")
BACKUP = Path.home() / "Applications/SJTU App Backups/SJTU Learning Assistant 1.8.17 before UI 1.8.18.app"
STAGED = Path("/Applications/SJTU Learning Assistant 1.8.18 staging.app")


def requests():
    runtime = APP_SUPPORT_DIR / "ai-runtime.sqlite3"
    with sqlite3.connect("file:" + quote(str(runtime)) + "?mode=ro", uri=True) as db:
        now = time.time()
        inflight = db.execute("SELECT count(id) FROM requests WHERE started IS NOT NULL AND ended IS NULL AND lease>?", (now,)).fetchone()[0]
        queued = db.execute("SELECT count(id) FROM requests WHERE started IS NULL AND ended IS NULL AND lease>?", (now,)).fetchone()[0]
    return dict(inflight=inflight, queued=queued)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pause", "status", "install", "resume"))
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    store = SettingsStore()
    if args.action == "pause":
        if STATE.exists():
            raise RuntimeError("已有部署状态，请先核对，不覆盖")
        settings = store.load()
        STATE.write_text(json.dumps(dict(ai_enabled=settings.ai_enabled, paused_at=time.time())))
        os.chmod(STATE, 0o600)
        store.save(replace(settings, ai_enabled=False))
        print(json.dumps(dict(paused=True, **requests())))
    elif args.action == "status":
        print(json.dumps(dict(ai_enabled=store.load().ai_enabled, **requests())))
    elif args.action == "install":
        if not STATE.exists() or store.load().ai_enabled:
            raise RuntimeError("必须先暂停新请求")
        if any(requests().values()):
            raise RuntimeError("仍有在途或排队请求，不关闭 App")
        bundle = args.bundle.resolve(strict=True)
        with (bundle / "Contents/Info.plist").open("rb") as stream:
            version = plistlib.load(stream).get("CFBundleShortVersionString")
        if version != "1.8.18" or bundle.suffix != ".app":
            raise RuntimeError("不是指定的新版本 App")
        if BACKUP.exists() or STAGED.exists():
            raise RuntimeError("备份或暂存路径已存在，不覆盖")
        subprocess.run(("codesign", "--verify", "--deep", "--strict", str(bundle)), check=True)
        # 已退出时不能发送 AppleEvent：macOS 可能先重新启动 App，再执行 quit，
        # 从而又触发启动同步，部署会误以为旧 App 一直不退出。
        main_running = subprocess.run(("pgrep", "-f", "^/Applications/SJTU Learning Assistant.app/Contents/MacOS/SJTU Learning Assistant$"), capture_output=True).returncode == 0
        if main_running:
            subprocess.run(("osascript", "-e", 'tell application "SJTU Learning Assistant" to quit'), check=True)
        deadline = time.monotonic() + 30
        while subprocess.run(("pgrep", "-f", "^/Applications/SJTU Learning Assistant.app/Contents/MacOS/"), capture_output=True).returncode == 0:
            if time.monotonic() > deadline:
                raise RuntimeError("App 未正常退出，不强制杀进程")
            time.sleep(.2)
        subprocess.run(("/usr/bin/ditto", str(bundle), str(STAGED)), check=True)
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        os.replace(APP, BACKUP)
        try:
            os.replace(STAGED, APP)
        except BaseException:
            os.replace(BACKUP, APP)
            raise
        print(json.dumps(dict(installed=version, old_app_preserved=True)))
    else:
        state = json.loads(STATE.read_text())
        store.save(replace(store.load(), ai_enabled=bool(state["ai_enabled"])))
        subprocess.run(("open", "-a", str(APP)), check=True)
        print(json.dumps(dict(resumed=True, ai_enabled=store.load().ai_enabled)))


if __name__ == "__main__":
    main()
