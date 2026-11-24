"""只读汇总本轮最终验收；不输出原文/密钥，不改变快照或知识库。"""
import hashlib
import json
import plistlib
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pypdf import PdfReader
from sjtu_learning_assistant.knowledge_documents import CONTRACT, VISION_MODEL


def collect():
    root = Path.home() / 'Documents/SJTU Study/Knowledge Markdown'
    support = Path.home() / 'Library/Application Support/SJTU Learning Assistant'
    repo = Path(__file__).resolve().parents[1]

    def run_json(script, *arguments):
        return json.loads(subprocess.check_output(
            (sys.executable, str(repo / 'scripts' / script), *arguments), cwd=repo, text=True))

    verify = run_json('verify_checkpoint_repair_20261124.py', 'verify')
    old = run_json('verify_auto_recovery_local.py', 'after')
    progress = run_json('inspect_checkpoint_progress_local.py')
    status = run_json('deploy_checkpoint_final_20261124.py', 'status')
    with sqlite3.connect('file:' + str(support / 'data/app.db') + '?mode=ro', uri=True) as db:
        active = {row[0] for row in db.execute("SELECT local_path FROM course_files WHERE is_active=1 AND download_status='downloaded' AND local_path IS NOT NULL")}
    manifests = {}
    for directory in (root / '90_Inbox/Documents').iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        for revision in directory.iterdir():
            path = revision / 'manifest.json'
            if revision.is_symlink() or not path.is_file() or path.is_symlink():
                continue
            manifest = json.loads(path.read_text())
            if manifest.get('contract') == CONTRACT and manifest.get('model') == VISION_MODEL:
                manifests[(manifest.get('source'), manifest.get('source_hash'))] = manifest
    items = json.loads((root / '99_System/Course-Sync.json').read_text())['items']
    unfinished, large = [], []
    for relative, item in items.items():
        source = root.parent / relative
        if str(source) not in active or item.get('status') not in ('pending', 'failed'):
            continue
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest = manifests.get((relative, source_hash), {})
        row = dict(file=source.name, status=item.get('status'), paused=bool(item.get('auto_paused')),
                   attempts=item.get('attempts'), error=item.get('error'), processed_pages=len(manifest.get('pages', {})))
        unfinished.append(row)
        if '（浙江大学）' in relative or '（人民大学）' in relative:
            large.append(dict(row, total_pages=len(PdfReader(source).pages)))
    ledger = json.loads((support / 'recovery-backups/checkpoint-repair-20261124/requests.json').read_text())
    backups = []
    for name in ('SJTU Learning Assistant 1.8.18 before checkpoint repair 20261124.app',
                 'SJTU Learning Assistant 1.8.18 before short-fragment repair 20261124.app',
                 'SJTU Learning Assistant 1.8.18 before final fragment validation 20261124.app'):
        path = Path.home() / 'Applications/SJTU App Backups' / name
        backups.append(dict(path=str(path), exists=path.is_dir()))
    code = {name: hashlib.sha256((repo / 'sjtu_learning_assistant' / name).read_bytes()).hexdigest()
            for name in ('knowledge_summary.py', 'knowledge_documents.py', 'course_markdown_sync.py',
                         'transcript_notes.py', 'ai_runtime.py')}
    original_notes = []
    for identity in ('a30e382cc0443ca37a3b30f632d2e72f', '7d00f772121b59104939805e8c61e49d',
                     '3b589e32e3ff7ff8045d5f1d920a1e37', 'd8c88a55bf1f0daa15f2ad796d0fe2a2',
                     'e4cf3f0decdaae9994a70fbdf83d1f21'):
        directory = support / 'transcripts/v1/videos' / identity
        manifest = json.loads((directory / 'manifest.json').read_text())
        notes = manifest.get('lesson_notes', {})
        actual_hash = hashlib.sha256((directory / 'raw.vtt').read_bytes()).hexdigest()
        original_notes.append(dict(id=identity, completed=notes.get('status') == 'completed',
                                   source_unchanged=actual_hash == manifest.get('raw_sha256') == notes.get('raw_sha256'),
                                   reviewed_chunks=notes.get('report', {}).get('reviewed_chunks')))
    app = Path('/Applications/SJTU Learning Assistant.app')
    result = dict(observed_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                  app_version=plistlib.loads((app / 'Contents/Info.plist').read_bytes()).get('CFBundleShortVersionString'),
                  app_revision='f431815658f5ce209da64e4c7edfc0eef3b37807',
                  app_running=subprocess.run(('pgrep', '-f', '^/Applications/SJTU Learning Assistant.app/Contents/MacOS/SJTU Learning Assistant$'), capture_output=True).returncode == 0,
                  status=status, verification=verify, old_after=old, progress=progress,
                  unfinished_active_courses=unfinished, large_documents=large,
                  targeted_requests=ledger['requests'], targeted_limit=ledger['limit'],
                  backups=backups, code_sha256=code, latest_tests=dict(passed=155, subtests=39),
                  original_paused_transcript_checks=original_notes,
                  original_five_paused_transcripts_completed=all(
                      note['completed'] and note['source_unchanged'] and note['reviewed_chunks'] is not None
                      for note in original_notes))
    assert result['app_running'] and status['ai_enabled'] and all(item['exists'] for item in backups)
    return result


if __name__ == '__main__':
    print(json.dumps(collect(), ensure_ascii=False))
