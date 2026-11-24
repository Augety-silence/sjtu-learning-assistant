"""本轮独立只读保护快照与增量验收；不调用旧before，不改知识库。"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sqlite3
import time

SUPPORT = Path.home() / 'Library/Application Support/SJTU Learning Assistant'
ROOT = Path.home() / 'Documents/SJTU Study/Knowledge Markdown'
SNAPSHOT = Path('/tmp/sjtu-checkpoint-repair-preservation-20261124.json')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def observe():
    items = json.loads((ROOT / '99_System/Course-Sync.json').read_text())['items']
    with sqlite3.connect('file:' + str(SUPPORT / 'data/app.db') + '?mode=ro', uri=True) as db:
        active = set(row[0] for row in db.execute("SELECT local_path FROM course_files WHERE is_active=1 AND download_status='downloaded' AND local_path IS NOT NULL"))
    current = {r:i for r,i in items.items() if str(ROOT.parent / r) in active}
    notes = {}
    for directory in (SUPPORT / 'transcripts/v1/videos').iterdir():
        p = directory / 'manifest.json'
        if not directory.is_symlink() and p.is_file():
            notes[str(directory)] = json.loads(p.read_text()).get('lesson_notes', {})
    protected = {}
    def protect(path):
        if path.is_file() and not path.is_symlink():
            protected[str(path)] = sha(path)
    for relative,item in current.items():
        protect(ROOT.parent / relative)
        if item.get('status') == 'completed':
            protect(ROOT / 'Courses' / (relative + '.md'))
            if item.get('document'):
                protect(ROOT / item['document'])
    for directory,note in notes.items():
        directory = Path(directory)
        protect(directory / 'raw.vtt')
        protect(directory / 'original.md')
        if note.get('status') == 'completed':
            protect(directory / 'lesson.md')
            protect(directory / 'lecture-summary.md')
    counts = dict(courses=dict(collections.Counter(i.get('status') for i in current.values())),
                  transcripts=dict(collections.Counter(i.get('status') for i in notes.values())))
    return current,notes,protected,counts

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action', choices=('capture','verify'))
    args=parser.parse_args()
    current,notes,protected,counts=observe()
    if args.action == 'capture':
        with SNAPSHOT.open('x') as out:
            json.dump(dict(time=time.time(),protected=protected,counts=counts,
                           completed_courses=[r for r,i in current.items() if i.get('status') == 'completed'],
                           completed_notes=[r for r,i in notes.items() if i.get('status') == 'completed']),out)
        print(json.dumps(dict(captured=True,protected_files=len(protected),**counts),ensure_ascii=False))
        return
    baseline=json.loads(SNAPSHOT.read_text())
    changed=[p for p,h in baseline['protected'].items() if not Path(p).is_file() or sha(Path(p))!=h]
    assert not changed, '本轮保护文件发生变化；停止并调查'
    checks=[]
    for relative,item in current.items():
        if item.get('status') != 'completed' or relative in baseline['completed_courses']:
            continue
        document=ROOT / item['document']
        report=json.loads((document.parent / 'review.json').read_text())
        assert report['summary_chars'] <= 5000 and report.get('checks') and all(c.get('approved') is True for c in report['checks'])
        manifest=json.loads((document.parent / 'manifest.json').read_text())
        assert set(map(int,manifest['pages'])) == set(report['covered_pages'])
        assert manifest['source_hash'] == sha(ROOT.parent / relative)
        checks.append(dict(file=Path(relative).name,pages=len(report['covered_pages']),chars=report['summary_chars']))
    new_notes=[]
    for directory,note in notes.items():
        if note.get('status')!='completed' or directory in baseline['completed_notes']:
            continue
        path=Path(directory)/'lecture-summary.md'
        assert path.is_file() and len(path.read_text())<=5000 and note.get('report',{}).get('reviewed_chunks') is not None
        new_notes.append(dict(id=Path(directory).name,chars=len(path.read_text())))
    result=dict(observed_at=time.time(),protected_files=len(baseline['protected']),protected_changed=0,
                new_courses=len(checks),new_transcripts=len(new_notes),course_checks=checks,note_checks=new_notes,**counts)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    main()
