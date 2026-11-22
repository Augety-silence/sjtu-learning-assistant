"""复用隔离验收已付费结果；来源/输出逐项验哈希，备份后换入，不调用模型。"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.knowledge_documents import CONTRACT, VISION_MODEL, digest, safe_destination
from sjtu_learning_assistant.local_settings import SettingsStore


def checked(directory):
    state = json.loads(safe_destination(directory, directory / 'manifest.json').read_text())
    if state.get('pending') or len(state.get('outputs', {})) > 4050:
        raise RuntimeError('验收检查点未完整提交')
    for name, expected in state['outputs'].items():
        path = safe_destination(directory, directory / name)
        if path.stat().st_size > 100 * 1024**2 or digest(path.read_bytes()) != expected:
            raise RuntimeError('检查点输出哈希不一致，未推广')
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    cache = args.cache.resolve()
    if args.cache.is_symlink() or not cache.name.startswith('sjtu-checkpoint-recovery-'):
        raise RuntimeError('仅可推广本程序创建的隔离验收目录')
    ledger = json.loads((cache / 'verification.json').read_text())
    if ledger.get('kind') != 'course':
        raise RuntimeError('验收类型不一致')
    source = Path(SettingsStore().load().archive_root).resolve()
    target = source / 'Knowledge Markdown'
    marker = target / '99_System/Course-Sync.json'
    marker_hash = digest(marker.read_bytes())
    state = json.loads(marker.read_text())
    relatives = [relative for relative in state['items'] if Path(relative).name == 'SQL概述与数据定义.pptx']
    if len(relatives) != 1:
        raise RuntimeError('已验收课件不唯一，未推广')
    relative = relatives[0]
    original = safe_destination(source, source / relative)
    source_hash = digest(original.read_bytes())
    source_id = digest(relative.encode())[:16]
    revision = digest((source_hash + CONTRACT + VISION_MODEL).encode())[:16]
    tail = Path('90_Inbox/Documents') / source_id / revision
    production = safe_destination(target, target / tail)
    verified = safe_destination(cache, cache / tail)
    old, new = checked(production), checked(verified)
    if any(new.get(key) != old.get(key) for key in ('source', 'source_hash', 'version', 'contract', 'model')) or new['source_hash'] != source_hash:
        raise RuntimeError('验收来源身份已变化')
    if not set(old['outputs']) <= set(new['outputs']):
        raise RuntimeError('验收缓存缺少旧输出，未推广')
    report = json.loads((verified / 'review.json').read_text())
    if report.get('summary_chars', 999999) > 5000 or not report.get('checks') or not all(row.get('approved') is True for row in report['checks']):
        raise RuntimeError('精炼结果没有通过来源审校')
    changes = sum(old['outputs'].get(key) != value for key, value in new['outputs'].items())
    backup = None
    if args.apply and changes:
        if digest(marker.read_bytes()) != marker_hash or digest(original.read_bytes()) != source_hash:
            raise RuntimeError('推广期间生产来源发生变化')
        backup = production.with_name(revision + '.before-1.8.17-' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        if backup.exists():
            raise RuntimeError('备份已存在，未覆盖')
        staged = Path(tempfile.mkdtemp(prefix='.verified-stage-', dir=production.parent))
        shutil.copytree(verified, staged, dirs_exist_ok=True)
        checked(staged)
        os.rename(production, backup)
        try:
            os.rename(staged, production)
        except Exception:
            os.rename(backup, production)
            raise
    print(json.dumps(dict(applied=args.apply, promoted_outputs=changes, api_requests=0,
                          source_unchanged=True, backup=str(backup) if backup else None), ensure_ascii=False))


if __name__ == '__main__':
    main()
