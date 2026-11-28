"""真实 DMG/原生 helper 的隔离升级演练，不修改生产应用或资料。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--package',type=Path);parser.add_argument('--github',action='store_true');args=parser.parse_args()
    if sys.platform!='darwin' or not args.root.is_absolute() or args.root.exists():raise RuntimeError('使用新的绝对隔离目录')
    args.root.mkdir(mode=0o700)
    support=args.root/'support'
    os.environ['SJTU_APP_SUPPORT_DIR']=str(support)
    from sjtu_learning_assistant.automatic_update import AutomaticUpdater
    from sjtu_learning_assistant.local_settings import SettingsStore
    from sjtu_learning_assistant.update_service import UpdateService
    from sjtu_learning_assistant import update_installer
    original_run=update_installer._run
    def diagnostic_run(argv, timeout=120):
        try:return original_run(argv,timeout)
        except Exception as exc:
            cause=exc.__cause__
            print(dict(command=argv,stderr=str(getattr(cause,'stderr',''))[:3000],stdout=str(getattr(cause,'stdout',''))[:1000]),flush=True)
            raise
    update_installer._run=diagnostic_run
    target=args.root/'SJTU Learning Assistant.app'
    subprocess.run(['ditto',str(args.bundle),str(target)],check=True)
    plist=target/'Contents/Info.plist';data=plistlib.loads(plist.read_bytes());data.update(CFBundleShortVersionString='2.0.0',CFBundleVersion='2.0.0');plist.write_bytes(plistlib.dumps(data))
    subprocess.run(['codesign','--force','--deep','--sign','-',str(target)],check=True)
    SettingsStore().update(dict(archive_root=str(args.root/'archive'), ai_enabled=False,current_term_md_enabled=False,course_markdown_enabled=False,recording_asr_enabled=False))
    if args.github:
        service=UpdateService('2.0.0')
    else:
        import httpx
        payload=args.package.read_bytes()
        url='https://github.com/Augety-silence/sjtu-learning-assistant/releases/download/v2.0.1/test-macOS-arm64.dmg'
        metadata=dict(tag_name='v2.0.1',draft=False,prerelease=False,html_url='https://github.com/Augety-silence/sjtu-learning-assistant/releases/tag/v2.0.1',body='',assets=[dict(name='test-macOS-arm64.dmg',browser_download_url=url,size=len(payload),digest='sha256:'+hashlib.sha256(payload).hexdigest())])
        service=UpdateService('2.0.0',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=metadata) if r.url.host=='api.github.com' else httpx.Response(200,content=payload)))
    result=service.check()
    if not result.update_available or result.latest_version!='2.0.1' or not result.download_plan:raise RuntimeError('升级元数据未命中2.0.1')
    ready=threading.Event()
    def shutdown(launch):
        launch();ready.set()
    updater=AutomaticUpdater(service,support/'updates',target,shutdown)
    sys.executable=str(target/'Contents/MacOS/SJTU Learning Assistant')
    state=updater.start(result.download_plan)
    (args.root/'check.json').write_text(json.dumps(dict(source='GitHub' if args.github else 'isolated-local-package',available=result.update_available,version=result.latest_version,size=result.download_plan.size,sha256=result.download_plan.sha256,phase=state.get('phase'))))
    deadline=time.monotonic()+600
    while not ready.wait(.25):
        if updater.status().get('phase') in {'failed','cancelled'}:raise RuntimeError(updater.status().get('message'))
        if time.monotonic()>deadline:raise RuntimeError('升级准备超时')
    print(json.dumps(dict(helper_launched=True,root=str(args.root))))
    return 0

if __name__=='__main__':raise SystemExit(main())
