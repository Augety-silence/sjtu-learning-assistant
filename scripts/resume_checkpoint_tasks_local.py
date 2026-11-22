"""只恢复旧长度/截断失败的课件；保留所有原文、稿件和检查点，清单先备份。"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.knowledge_compiler import _atomic_write
from sjtu_learning_assistant.local_settings import SettingsStore

VERSION = 'checkpoint-recovery-1.8.17-5000'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = Path(SettingsStore().load().archive_root).resolve()
    marker = root / 'Knowledge Markdown/99_System/Course-Sync.json'
    if marker.is_symlink() or not marker.is_file():
        raise RuntimeError('课程清单不可安全读取')
    state = json.loads(marker.read_text())
    resumed = 0
    for item in state['items'].values():
        if (item.get('status') != 'failed' or item.get('checkpoint_recovery_version') == VERSION
                or not any(term in str(item.get('error', ''))
                           for term in ('精炼总结正文', '响应达到长度上限', '响应被截断'))):
            continue
        item['checkpoint_recovery_version'] = VERSION
        item['status'] = 'pending'
        item.pop('retry_after', None)
        resumed += 1
    if args.apply and resumed:
        backup = marker.with_name('Course-Sync.before-checkpoint-1.8.17-' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.json')
        if backup.exists() or backup.is_symlink():
            raise RuntimeError('备份已存在，未改写课程清单')
        shutil.copy2(marker, backup)
        _atomic_write(marker, json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps(dict(applied=args.apply, resumed=resumed, originals_changed=False,
                          checkpoints_reset=False), ensure_ascii=False))


if __name__ == '__main__':
    main()
