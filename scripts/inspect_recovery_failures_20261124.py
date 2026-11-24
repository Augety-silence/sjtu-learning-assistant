"""只读列出失败检查点结构，省略原正文与模型请求。"""
import collections
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.knowledge_documents import CONTRACT, VISION_MODEL

root = Path.home() / 'Documents/SJTU Study/Knowledge Markdown'
items = json.loads((root / '99_System/Course-Sync.json').read_text())['items']
for relative, item in items.items():
    if item.get('status') not in ('pending', 'failed'):
        continue
    folder = root / '90_Inbox/Documents' / hashlib.sha256(relative.encode()).hexdigest()[:16]
    source = root.parent / relative
    if not source.is_file():
        continue
    rev = hashlib.sha256((hashlib.sha256(source.read_bytes()).hexdigest()+CONTRACT+VISION_MODEL).encode()).hexdigest()[:16]
    directory = folder / rev
    if not directory.is_dir():
        print(json.dumps(dict(file=source.name, checkpoint=False), ensure_ascii=False))
        continue
    manifest = json.loads((directory / 'manifest.json').read_text())
    errors = []
    for path in sorted(directory.rglob('*.json')):
        value = json.loads(path.read_text())
        if value.get('error'):
            draft = value.get('draft')
            try:
                draft = json.loads(draft) if isinstance(draft,str) else draft
            except ValueError:
                draft = None
            errors.append(dict(checkpoint=path.relative_to(directory).as_posix(), error=value['error'], step=value.get('step'),
                keys=list(draft) if isinstance(draft,dict) else None,
                markdown_chars=len(draft.get('markdown','')) if isinstance(draft,dict) else None,
                topics=draft.get('topics') if isinstance(draft,dict) and len(str(draft.get('topics'))) < 1700 else None,
                covered_pages=draft.get('covered_pages') if isinstance(draft,dict) else None))
    print(json.dumps(dict(file=source.name, pages=len(manifest['pages']), page_count=manifest.get('page_count'),
        status=item['status'], paused=item.get('auto_paused'), errors=errors[-8:]),ensure_ascii=False))

support = Path.home() / 'Library/Application Support/SJTU Learning Assistant'
for directory in sorted((support / 'transcripts/v1/videos').iterdir()):
    path = directory / 'manifest.json'
    if not path.is_file():
        continue
    manifest = json.loads(path.read_text())
    notes = manifest.get('lesson_notes',{})
    if notes.get('status') == 'completed':
        continue
    cache = directory / 'lesson-checkpoints'
    reviews=[]
    if cache.is_dir():
        for path in cache.rglob('*.json'):
            value=json.loads(path.read_text())
            if not isinstance(value,dict):
                continue
            if 'content' in value:
                try:
                    value=json.loads(value['content'])
                except (TypeError,ValueError):
                    continue
            if isinstance(value,dict) and value.get('approved') is False:
                reviews.append(dict(checkpoint=path.relative_to(directory).as_posix(),covered=value.get('covered_chunks'),issues=value.get('issues')))
    print(json.dumps(dict(transcript=directory.name,notes=notes,failed_reviews=reviews),ensure_ascii=False))
