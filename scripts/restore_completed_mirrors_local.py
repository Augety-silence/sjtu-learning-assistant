"""将跳过规则误降级的已验收稿从已登记备份恢复；不改来源，不调用模型。"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.knowledge_documents import digest, safe_destination, target_lock, compilation_fingerprint
from sjtu_learning_assistant.knowledge_compiler import _atomic_write
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.database import APP_SUPPORT_DIR


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--resume-since', type=float, default=0)
    args = parser.parse_args()
    source = Path(SettingsStore().load().archive_root).resolve()
    target = source / 'Knowledge Markdown'
    marker = target / '99_System/Course-Sync.json'
    baseline = safe_destination(target, args.baseline)
    if not baseline.name.startswith('Course-Sync.before-checkpoint-1.8.17-'):
        raise RuntimeError('仅可恢复本轮已登记的课程清单备份')
    old = json.loads(baseline.read_text())
    restored = []
    resumed = 0
    with target_lock(target):
        current = json.loads(marker.read_text())
        if old['source_root'] != current['source_root'] or old['source_root'] != str(source):
            raise RuntimeError('清单来源不一致，未恢复')
        for relative, item in old['items'].items():
            active = current['items'].get(relative, {})
            if item.get('status') != 'completed' or active.get('status') != 'retained':
                continue
            original = safe_destination(source, source / relative)
            if compilation_fingerprint(digest(original.read_bytes())) != item.get('fingerprint'):
                raise RuntimeError('来源已更新，不覆盖新版本')
            mirror = safe_destination(target, target / 'Courses' / (relative + '.md'))
            previous = safe_destination(target, target / '90_Inbox/Previous' / (relative + '.md') / (item['mirror_hash'] + '.md'))
            if digest(mirror.read_bytes()) != active.get('mirror_hash') or digest(previous.read_bytes()) != item['mirror_hash']:
                raise RuntimeError('正文或备份哈希不一致，未覆盖用户内容')
            restored.append((relative, mirror, previous.read_text(), dict(item)))
        if args.resume_since:
            for item in current['items'].values():
                dispatched = item.get('last_dispatched_at')
                if dispatched and item.get('status') == 'failed' and datetime.fromisoformat(dispatched).timestamp() >= args.resume_since:
                    item.update(status='pending')
                    item.pop('retry_after', None)
                    resumed += 1
        if args.apply and (restored or resumed):
            backup = marker.with_name('Course-Sync.before-completed-guard-' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.json')
            if backup.exists():
                raise RuntimeError('备份已存在，未覆盖')
            _atomic_write(backup, marker.read_text())
            for relative, mirror, body, item in restored:
                saved = safe_destination(target, target / '90_Inbox/Previous' / (relative + '.md') / (digest(mirror.read_bytes()) + '.md'))
                if not saved.exists():
                    _atomic_write(saved, mirror.read_text())
                _atomic_write(mirror, body)
                current['items'][relative] = item
            _atomic_write(marker, json.dumps(current, ensure_ascii=False, indent=2))
    resumed_notes = 0
    if args.resume_since:
        root = APP_SUPPORT_DIR / 'transcripts/v1/videos'
        for directory in sorted(root.iterdir())[:5000]:
            path = safe_destination(root, directory / 'manifest.json')
            if not path.is_file():
                continue
            value = json.loads(path.read_text())
            notes = value.get('lesson_notes', {})
            dispatched = notes.get('last_dispatched_at')
            if (dispatched and notes.get('retry_after') and notes.get('status') == 'pending'
                    and datetime.fromisoformat(dispatched).timestamp() >= args.resume_since):
                notes.pop('retry_after', None)
                notes.pop('error_code', None)
                resumed_notes += 1
                if args.apply:
                    backup = path.with_name('manifest.before-5000-pause-resume.json')
                    if not backup.exists():
                        _atomic_write(backup, path.read_text())
                    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2))
    print(json.dumps(dict(applied=args.apply, restored=len(restored), resumed_courses=resumed,
                          resumed_notes=resumed_notes, api_requests=0, sources_changed=False), ensure_ascii=False))


if __name__ == '__main__':
    main()
