"""停滞修复本机验收：最多4次请求；已合并稿可显式补1次最终审校。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.ai_classifier import ClassificationInput, OpenAIClassificationClient
from sjtu_learning_assistant.ai_keychain import get_ai_api_key
from sjtu_learning_assistant.knowledge_documents import DocumentCompiler, CONTRACT, VISION_MODEL, digest, page_images
from sjtu_learning_assistant.knowledge_compiler import KnowledgeCompilerError, KnowledgeResponseError
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.knowledge_summary import CONCISE_REVIEW_PROMPT
from sjtu_learning_assistant.document_text import DocumentTextError
from sjtu_learning_assistant.markdown_progress import transcript_progress
from sjtu_learning_assistant.database import APP_SUPPORT_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('render', 'course', 'classify', 'progress'))
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--finish-review', action='store_true')
    args = parser.parse_args()
    settings = SettingsStore().load()
    started, clients, usage = time.monotonic(), {}, []
    source = Path(settings.archive_root)
    production = source / 'Knowledge Markdown'
    marker = production / '99_System/Course-Sync.json'
    items = json.loads(marker.read_text(encoding='utf-8'))['items']
    if args.action == 'progress':
        print(json.dumps(transcript_progress(APP_SUPPORT_DIR / 'transcripts/v1'), ensure_ascii=False))
        return 0
    target = args.checkpoint.resolve(strict=True) if args.checkpoint else Path(tempfile.mkdtemp(prefix='sjtu-stall-validation-')).resolve()
    if (args.checkpoint and args.checkpoint.is_symlink()) or not target.name.startswith('sjtu-stall-validation-'):
        raise RuntimeError('只可复用本验收创建的隔离目录')
    ledger = target / 'verification.json'
    history = json.loads(ledger.read_text()) if ledger.is_file() else dict(action=args.action, requests=0)
    if history['action'] != args.action:
        raise RuntimeError('检查点验收类型不匹配')
    if args.finish_review and (not args.checkpoint or args.action != 'course' or history['requests'] != 4):
        raise RuntimeError('只允许对已达到4请求的课件检查点补一次最终审校')
    def client(model):
        if model not in clients:
            clients[model] = OpenAIClassificationClient(api_key=get_ai_api_key(), base_url=settings.ai_base_url,
                model=model, timeout=httpx.Timeout(connect=10, read=150, write=30, pool=10),
                usage_recorder=lambda model, counts: usage.append((model, counts)))
        return clients[model]
    def runner(model, prompt, content):
        if (history['requests'] >= (5 if args.finish_review else 4)
                or (args.finish_review and (model != 'qwen3.8-27b' or not prompt.startswith(CONCISE_REVIEW_PROMPT)))):
            raise KnowledgeCompilerError('本机验收预算已用尽，保留副本检查点')
        history['requests'] += 1
        ledger.write_text(json.dumps(history), encoding='utf-8')
        print(json.dumps(dict(event='request_started', request=history['requests'], model=model)), flush=True)
        result = client(model).chat_completion([dict(role='system', content=prompt), dict(role='user', content=content)],
            system_prompt=False, knowledge_compilation=True, response_format=dict(type='json_object'),
            max_tokens=2048 if prompt.startswith(CONCISE_REVIEW_PROMPT) else 6500)
        if result.get('response_metadata', {}).get('finish_reason') == 'length':
            raise KnowledgeResponseError('验收响应截断；已保存副本', content=result.get('content') or '')
        return result['content']
    try:
        if args.action == 'render':
            candidates = []
            for relative, item in items.items():
                if 'PdfiumError' not in item.get('error', ''):
                    continue
                path = source / relative
                revision = digest((digest(path.read_bytes()) + CONTRACT + VISION_MODEL).encode())[:16]
                pdf = production / '90_Inbox/Documents' / digest(relative.encode())[:16] / revision / 'source-render.pdf'
                if pdf.is_file() and not pdf.is_symlink():
                    candidates.append(pdf)
            if not candidates:
                raise RuntimeError('没有真实 PDFium 失败缓存')
            pdf = candidates[0]
            with ThreadPoolExecutor(max_workers=4) as pool:
                pages = list(pool.map(lambda _: sum(1 for _ in page_images(pdf)), range(8)))
            assert len(set(pages)) == 1 and pages[0] > 0
            result = dict(passed=True, real_local_pdf=True, parallel_jobs=8, workers=4, pages_per_job=pages[0], requests=0)
        elif args.action == 'classify':
            if history['requests'] >= 1:
                raise RuntimeError('分类验收已调用，不重复请求')
            history['requests'] += 1
            ledger.write_text(json.dumps(history), encoding='utf-8')
            result = client('deepseek-reasoner').classify_many((ClassificationInput('probe-1', '合成课程', '第一讲.pdf'),))
            assert result == {'probe-1': 'courseware'}
            assert usage and usage[0][0] == 'deepseek-chat'
            result = dict(passed=True, synthetic_input=True, real_api=True, configured_model='deepseek-reasoner', actual_model=usage[0][0], requests=1)
        else:
            selected = [(relative, item) for relative, item in items.items()
                        if Path(relative).name == 'Section 1_En.pdf' and '来源引用' in item.get('error', '')]
            if not selected:
                raise RuntimeError('指定引用误判失败课件不存在')
            relative, _ = selected[0]
            path = source / relative
            before_source = digest(path.read_bytes())
            revision = digest((before_source + CONTRACT + VISION_MODEL).encode())[:16]
            cache = production / '90_Inbox/Documents' / digest(relative.encode())[:16] / revision
            before_cache = digest((cache / 'manifest.json').read_bytes())
            destination = target / cache.relative_to(production)
            if not destination.exists():
                shutil.copytree(cache, destination)
            compiler = DocumentCompiler(runner, threading.Event(), max_calls=4, text_first=True)
            note = compiler.compile(source, path, target)
            calls = history['requests']
            again = DocumentCompiler(runner, threading.Event(), max_calls=4, text_first=True).compile(source, path, target)
            assert calls == history['requests'] and again.read_bytes() == note.read_bytes()
            assert before_source == digest(path.read_bytes()) and before_cache == digest((cache / 'manifest.json').read_bytes())
            report = json.loads((note.parent / 'review.json').read_text(encoding='utf-8'))
            assert report['summary_chars'] <= 5000 and all(check['approved'] for check in report['checks'])
            result = dict(passed=True, real_api=True, file=path.name, pages=len(report['covered_pages']),
                          summary_chars=report['summary_chars'], cached_pages=compiler.cached_pages,
                          requests=calls, resume_requests=0, source_and_production_cache_unchanged=True)
        result.update(seconds=round(time.monotonic()-started, 2), reported_tokens=sum(counts.get('total_tokens', 0) for _, counts in usage), checkpoint=str(target))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps(dict(passed=False, error_type=type(exc).__name__,
            validation_message=str(exc) if isinstance(exc, (ValueError, KnowledgeCompilerError, DocumentTextError)) else None,
            checkpoint=str(target), requests=history['requests'], seconds=round(time.monotonic()-started, 2)), ensure_ascii=False), flush=True)
        return 1
    finally:
        for value in clients.values():
            value.close()


if __name__ == '__main__':
    import httpx
    raise SystemExit(main())
