"""只读检查编译状态和失败检查点；不打印正文、提示词或凭据。"""
import collections
import hashlib
import json
import sqlite3
import time
from pathlib import Path

root = Path.home() / 'Documents/SJTU Study/Knowledge Markdown'
items = json.loads((root / '99_System/Course-Sync.json').read_text())['items']
print('course_counts', dict(collections.Counter(i.get('status') for i in items.values())))
inspected = False
for relative, item in items.items():
    if item.get('status') not in ('pending', 'failed'):
        continue
    print(json.dumps({'file': Path(relative).name, **{key: item.get(key) for key in
        ('status', 'attempts', 'retry_after', 'last_dispatched_at', 'error', 'auto_recovery_version')}}, ensure_ascii=False))
    if inspected or '完整主题结构' not in str(item.get('error')):
        continue
    inspected = True
    folder = root / '90_Inbox/Documents' / hashlib.sha256(relative.encode()).hexdigest()[:16]
    count = 0
    for f in folder.rglob('*-resume-v1-*.json'):
        state = json.loads(f.read_text())
        if '主题' not in str(state.get('error')):
            continue
        try:
            draft = json.loads(state.get('draft') or '{}')
        except ValueError:
            draft = {}
        topics = draft.get('topics')
        print('checkpoint', json.dumps({'file': f.name, 'step': state.get('step'), 'error': state.get('error'),
            'keys': list(draft), 'topics_type': type(topics).__name__,
            'topics_count': len(topics) if isinstance(topics, (list, dict)) else None,
            'markdown_chars': len(draft.get('markdown') or '')}, ensure_ascii=False))
        count += 1
        if count == 4:
            break
support = Path.home() / 'Library/Application Support/SJTU Learning Assistant'
notes = []
pending_directories = []
for f in (support / 'transcripts/v1/videos').glob('*/manifest.json'):
    data = json.loads(f.read_text())
    value = data.get('lesson_notes', {})
    notes.append(value)
    if value.get('status') == 'pending':
        pending_directories.append(f.parent)
print('notes_counts', dict(collections.Counter(i.get('status') for i in notes)))
print('notes_pending', [{k: i.get(k) for k in ('error_code', 'attempts', 'retry_after', 'last_dispatched_at')}
                        for i in notes if i.get('status') == 'pending'])
with sqlite3.connect('file:' + str(support / 'ai-runtime.sqlite3') + '?mode=ro', uri=True) as db:
    print('runtime', db.execute('SELECT count(id),sum(started IS NOT NULL),sum(ended IS NOT NULL) FROM requests WHERE lease>?', (time.time(),)).fetchone())
with sqlite3.connect('file:' + str(support / 'data/app.db') + '?mode=ro', uri=True) as db:
    active = set(row[0] for row in db.execute("SELECT local_path FROM course_files WHERE is_active=1 AND download_status='downloaded' AND local_path IS NOT NULL"))
    print('registered_paths', len(active))
    for relative, item in items.items():
        if not item.get('last_dispatched_at') and item.get('status') in ('pending', 'failed'):
            path = root.parent / relative
            print('undispatched_source', json.dumps({'file': path.name, 'exists': path.is_file(), 'active': str(path) in active}, ensure_ascii=False))
if '--pending-errors' in __import__('sys').argv:
    from sjtu_learning_assistant.transcript_notes import compile_summary
    class NoAPI:
        def chat_completion(self, *args, **kwargs):
            raise RuntimeError('需要新请求，诊断已阻止')
    for directory in pending_directories[:4]:
        parent = directory / 'lesson-checkpoints'
        folders = list(parent.iterdir()) if parent.exists() else []
        if not folders:
            continue
        cache_dir = folders[0]
        def cached(key):
            f = cache_dir / (key + '.json')
            return json.loads(f.read_text()) if f.exists() else None
        try:
            compile_summary((directory / 'raw.vtt').read_text(), NoAPI(), cached=cached, review_percent=20)
            print('offline_pending_check', '已缓存完整结果')
        except Exception as exc:
            print('offline_pending_check', type(exc).__name__, str(exc))
