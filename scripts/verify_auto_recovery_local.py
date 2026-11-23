"""本地安装前后只读验收；保存哈希快照，不输出正文或凭据。"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sqlite3
import time

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=('before', 'after'))
args = parser.parse_args()
support = Path.home() / 'Library/Application Support/SJTU Learning Assistant'
root = Path.home() / 'Documents/SJTU Study/Knowledge Markdown'
snapshot = Path('/tmp/sjtu-auto-recovery-preservation-full-20261123.json')
marker = root / '99_System/Course-Sync.json'
items = json.loads(marker.read_text())['items']
with sqlite3.connect('file:' + str(support / 'data/app.db') + '?mode=ro', uri=True) as db:
    active = set(row[0] for row in db.execute("SELECT local_path FROM course_files WHERE is_active=1 AND download_status='downloaded' AND local_path IS NOT NULL"))
current = {relative: item for relative, item in items.items() if str(root.parent / relative) in active}
notes = []
protected = {}
def protect(path):
    if path.is_file() and not path.is_symlink():
        protected[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
for relative, item in current.items():
    protect(root.parent / relative)
    if item.get('status') == 'completed':
        protect(root / 'Courses' / (relative + '.md'))
for directory in (support / 'transcripts/v1/videos').iterdir():
    f = directory / 'manifest.json'
    if not f.is_file() or directory.is_symlink():
        continue
    data = json.loads(f.read_text())
    notes.append(data.get('lesson_notes', {}))
    protect(directory / 'raw.vtt')
    if data.get('lesson_notes', {}).get('status') == 'completed':
        for name in ('lesson.md', 'lecture-summary.md'):
            protect(directory / name)
result = {'observed_at': time.time(), 'courses': dict(collections.Counter(i.get('status') for i in current.values())),
          'transcripts': dict(collections.Counter(i.get('status') for i in notes)),
          'auto_paused_courses': sum(bool(i.get('auto_paused')) for i in current.values()),
          'auto_paused_transcripts': sum(bool(i.get('auto_paused')) for i in notes)}
if args.action == 'before':
    with snapshot.open('x') as stream:
        json.dump({'protected': protected, 'baseline': result}, stream)
    result['protected_files'] = len(protected)
else:
    saved = json.loads(snapshot.read_text())
    changed = sum(not Path(path).is_file() or hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
                  for path, digest in saved['protected'].items())
    result.update(protected_files=len(saved['protected']), protected_changed=changed,
                  elapsed_seconds=round(result['observed_at'] - saved['baseline']['observed_at'], 1),
                  new_courses=result['courses'].get('completed', 0) - saved['baseline']['courses'].get('completed', 0),
                  new_transcripts=result['transcripts'].get('completed', 0) - saved['baseline']['transcripts'].get('completed', 0))
    assert changed == 0, '来源或已完成稿发生变化；请停止部署验收并检查'
    from sjtu_learning_assistant.knowledge_documents import CONTRACT, VISION_MODEL
    checked_courses = []
    for relative, item in current.items():
        mirror = root / 'Courses' / (relative + '.md')
        if item.get('status') != 'completed' or str(mirror) in saved['protected']:
            continue
        source_hash = protected[str(root.parent / relative)]
        source_id = hashlib.sha256(relative.encode()).hexdigest()[:16]
        revision = hashlib.sha256((source_hash + CONTRACT + VISION_MODEL).encode()).hexdigest()[:16]
        report = json.loads((root / '90_Inbox/Documents' / source_id / revision / 'review.json').read_text())
        assert report['summary_chars'] <= 5000 and all(check['approved'] for check in report['checks'])
        checked_courses.append({'pages': len(report['covered_pages']), 'summary_chars': report['summary_chars']})
    result['new_course_checks'] = checked_courses
print(json.dumps(result, ensure_ascii=False))
