"""真实资料隔离验收：总请求硬限16，不改生产稿、不启动生产队列。"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import threading
import time
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient
from sjtu_learning_assistant.ai_keychain import get_ai_api_key
from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.knowledge_documents import DocumentCompiler, CompilationDeferred, CONTRACT, VISION_MODEL
from sjtu_learning_assistant.knowledge_compiler import KnowledgeResponseError
from sjtu_learning_assistant.knowledge_summary import CONCISE_REVIEW_PROMPT, SUMMARY_PROMPT, CONCISE_COMPRESSION_PROMPT
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.transcript_notes import compile_summary, SummaryDeferred


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=('course', 'transcript'))
    parser.add_argument('--resume-cache', type=Path)
    args = parser.parse_args()
    kind = args.kind
    settings, uses, clients, calls = SettingsStore().load(), [], {}, []
    started = time.monotonic()
    if args.resume_cache is not None:
        target = args.resume_cache.resolve()
        if args.resume_cache.is_symlink() or not target.is_dir() or not target.name.startswith('sjtu-checkpoint-recovery-'):
            raise RuntimeError('仅可续用本验收程序创建的检查点目录')
    else:
        target = Path(tempfile.mkdtemp(prefix='sjtu-checkpoint-recovery-')).resolve()
    ledger = target / 'verification.json'
    history = json.loads(ledger.read_text()) if ledger.is_file() else {'kind': kind, 'requests': 0}
    if history['kind'] != kind:
        raise RuntimeError('验收检查点类型不匹配')
    def client(model):
        if model not in clients:
            import httpx
            clients[model] = OpenAIClassificationClient(api_key=get_ai_api_key(), base_url=settings.ai_base_url,
                model=model, timeout=httpx.Timeout(connect=15, read=180, write=30, pool=15),
                usage_recorder=lambda model, usage: uses.append(usage))
        return clients[model]
    def runner(model, system, content):
        if history['requests'] >= 16:
            raise RuntimeError('验收请求额度已用尽；不会继续调用')
        history['requests'] += 1
        ledger.write_text(json.dumps(history))
        calls.append(model)
        print(json.dumps(dict(event='request_started', request=history['requests'], model=model), ensure_ascii=False), flush=True)
        result = client(model).chat_completion([{'role': 'system', 'content': system},
            {'role': 'user', 'content': content}], max_tokens=2048 if system.startswith(CONCISE_REVIEW_PROMPT) else
            6500 if system.startswith(CONCISE_COMPRESSION_PROMPT) else 7500,
            system_prompt=False, knowledge_compilation=True, response_format={'type': 'json_object'})
        if (result.get('response_metadata') or {}).get('finish_reason') == 'length':
            raise KnowledgeResponseError('验收响应被截断；请压缩并重写完整JSON。', content=result.get('content') or '')
        return result['content']
    try:
        # 验收失败也保留隔离检查点，后续定位不重复付费；不启动生产队列。
        with nullcontext(target):
            if kind == 'course':
                source = Path(settings.archive_root)
                production = source / 'Knowledge Markdown'
                marker = production / '99_System/Course-Sync.json'
                before_marker = hashlib.sha256(marker.read_bytes()).hexdigest()
                items = json.loads(marker.read_text())['items']
                selected = [(relative, source / relative) for relative, item in items.items()
                            if (source / relative).name == 'SQL概述与数据定义.pptx']
                if not selected:
                    raise RuntimeError('真实失败课件不存在')
                relative, path = selected[0]
                source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                source_id = hashlib.sha256(relative.encode()).hexdigest()[:16]
                revision = hashlib.sha256((source_hash + CONTRACT + VISION_MODEL).encode()).hexdigest()[:16]
                cache_path = production / '90_Inbox/Documents' / source_id / revision
                destination = target / '90_Inbox/Documents' / source_id / revision
                cache_before = hashlib.sha256((cache_path / 'manifest.json').read_bytes()).hexdigest()
                if not destination.exists():
                    shutil.copytree(cache_path, destination)
                for slices in range(12):
                    try:
                        note = DocumentCompiler(runner, threading.Event(), slice_calls=2, text_first=True).compile(source, path, target)
                        break
                    except CompilationDeferred:
                        pass
                else:
                    raise RuntimeError('验收轮次用尽')
                count = len(calls)
                resumed = DocumentCompiler(runner, threading.Event(), slice_calls=2, text_first=True).compile(source, path, target)
                assert count == len(calls) and resumed.read_bytes() == note.read_bytes()
                report = json.loads((note.parent / 'review.json').read_text())
                assert report['summary_chars'] <= 5000 and all(c['approved'] for c in report['checks'])
                assert before_marker == hashlib.sha256(marker.read_bytes()).hexdigest()
                assert source_hash == hashlib.sha256(path.read_bytes()).hexdigest()
                assert cache_before == hashlib.sha256((cache_path / 'manifest.json').read_bytes()).hexdigest()
                detail = dict(pages=len(report['covered_pages']), summary_chars=report['summary_chars'], slices=slices+1)
            else:
                candidates = []
                for directory in (APP_SUPPORT_DIR / 'transcripts/v1/videos').iterdir():
                    path = directory / 'manifest.json'
                    raw = directory / 'raw.vtt'
                    if directory.is_symlink() or not path.is_file() or not raw.is_file():
                        continue
                    data = json.loads(path.read_text())
                    if data.get('lesson_notes', {}).get('status') == 'pending':
                        candidates.append((raw.stat().st_size, raw))
                raw_path = min(candidates)[1]
                raw = raw_path.read_text()
                raw_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                cache = {}
                class NotesClient:
                    def chat_completion(self, messages, **kwargs):
                        return {'content': runner('deepseek-chat', messages[0]['content'], messages[1]['content']), 'finish_reason': 'stop'}
                options = dict(cached=cache.get, commit=lambda key, value: cache.setdefault(key, value), max_calls=2)
                for slices in range(12):
                    try:
                        body, report = compile_summary(raw, NotesClient(), **options)
                        break
                    except SummaryDeferred:
                        pass
                else:
                    raise RuntimeError('验收轮次用尽')
                count = len(calls)
                resumed, resume_report = compile_summary(raw, NotesClient(), **options)
                assert resumed == body and len(calls) == count and resume_report['calls'] == 0
                assert raw_hash == hashlib.sha256(raw_path.read_bytes()).hexdigest() and len(body) <= 5000
                detail = dict(chunks=len(report['covered_chunks']), summary_chars=len(body), slices=slices+1)
            print(json.dumps(dict(kind=kind, passed=True, real_api=True, requests=len(calls), resume_requests=0,
                source_unchanged=True, seconds=round(time.monotonic()-started,2),
                reported_tokens=sum(u.get('total_tokens') or 0 for u in uses), **detail), ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps(dict(kind=kind, passed=False, error_type=type(exc).__name__, requests=len(calls),
                             validation_message=str(exc) if isinstance(exc, (ValueError, CompilationDeferred)) else
                             str(exc) if type(exc).__name__ == 'DocumentTextError' else None,
                             checkpoint=str(target),
                             seconds=round(time.monotonic()-started,2)), ensure_ascii=False), flush=True)
        return 1
    finally:
        for value in clients.values():
            value.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
