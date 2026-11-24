"""本轮最终只读验收：部署代码、原件保护与残留检查点，不调用模型。"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_checkpoint_repair_20261124 import observe
from sjtu_learning_assistant.knowledge_documents import CONTRACT, VISION_MODEL

repo = Path(__file__).resolve().parents[1]
python = repo / '.venv/bin/python'
def run(*args):
    return subprocess.check_output(tuple(str(arg) for arg in args), cwd=repo, text=True)

preservation = json.loads(run(python, 'scripts/verify_chat_preservation_20261124.py', 'verify'))
recovery = json.loads(run(python, 'scripts/verify_checkpoint_repair_20261124.py', 'verify'))
current, notes, protected, counts = observe()
root = Path.home() / 'Documents/SJTU Study/Knowledge Markdown'
residual = []
for relative, item in current.items():
    if item.get('status') not in ('failed', 'pending'):
        continue
    source = root.parent / relative
    folder = root / '90_Inbox/Documents' / hashlib.sha256(relative.encode()).hexdigest()[:16]
    revision = hashlib.sha256((hashlib.sha256(source.read_bytes()).hexdigest() + CONTRACT + VISION_MODEL).encode()).hexdigest()[:16]
    directory = folder / revision
    errors, pages, total = set(), None, None
    if directory.is_dir():
        manifest = json.loads((directory / 'manifest.json').read_text())
        pages = len(manifest.get('pages', {}))
        for checkpoint in directory.rglob('*.json'):
            value = json.loads(checkpoint.read_text())
            if isinstance(value, dict) and isinstance(value.get('error'), str):
                errors.add(value['error'][:220])
    if source.suffix.casefold() == '.pdf':
        from pypdf import PdfReader
        total = len(PdfReader(source).pages)
    residual.append(dict(file=source.name, status=item.get('status'), paused=item.get('auto_paused'),
                         checkpoint_pages=pages, total_pages=total,
                         current_error=item.get('error'), checkpoint_errors=sorted(errors)))
native = json.loads(Path('/tmp/sjtu-chat-steering-native-20261124.json').read_text())
app = Path('/Applications/SJTU Learning Assistant.app')
run('codesign', '--verify', '--deep', '--strict', app)
result = dict(observed_at=time.time(), code_commit='8a65a99aecbe86e76aa8bae864a2403ce1caebda',
              version='1.8.18', installed_executable_sha256=hashlib.sha256((app/'Contents/MacOS/SJTU Learning Assistant').read_bytes()).hexdigest(),
              preservation=preservation, recovery=recovery, residual=residual,
              native=native, backend_tests=169, backend_subtests=38,
              knowledge_regression_tests=49, knowledge_regression_subtests=16, frontend_tests=619,
              backup=str(Path.home()/'Applications/SJTU App Backups/SJTU Learning Assistant 1.8.18 before chat steering 20261124.app'))
assert Path(result['backup']).is_dir()
destination = Path('/tmp/sjtu-chat-final-verification-20261124.json')
destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(dict(observed_at=result['observed_at'], version=result['version'],
                     code_commit=result['code_commit'], protected_files=preservation['protected_files'],
                     protected_changed=preservation['protected_changed'], residual=residual), ensure_ascii=False))
