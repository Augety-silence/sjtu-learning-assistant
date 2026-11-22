"""显式选择真实失败工件，隔离验证3000字总结；不写生产清单或数据库。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient
from sjtu_learning_assistant.ai_keychain import get_ai_api_key
from sjtu_learning_assistant.knowledge_documents import preferred_visual_output
from sjtu_learning_assistant.knowledge_summary import SummaryCompiler
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.transcript_notes import compile_summary, original_markdown, lesson_markdown
from sjtu_learning_assistant.summary_policy import MAX_SUMMARY_CHARS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--page-evidence', type=Path)
    parser.add_argument('--subtitle-directory', type=Path)
    args = parser.parse_args()
    assert args.page_evidence or args.subtitle_directory
    settings = SettingsStore().load()
    assert settings.ai_base_url.rstrip('/') == 'https://models.sjtu.edu.cn/api/v1'
    key = get_ai_api_key()
    assert key, '没有可读取的已保存凭据'
    output = Path(tempfile.mkdtemp(prefix='sjtu-summary-validation-'))
    clients, usage = {}, []

    class ObservedClient(OpenAIClassificationClient):
        def chat_completion(self, messages, **kwargs):
            print(json.dumps({'phase': 'request', 'model': self.model, 'input_chars': sum(len(m['content']) for m in messages)}), flush=True)
            response = super().chat_completion(messages, **kwargs)
            print(json.dumps({'phase': 'response', 'model': self.model, 'content_chars': len(response.get('content', ''))}), flush=True)
            return response

    def client(model):
        if model not in clients:
            clients[model] = ObservedClient(api_key=key, base_url=settings.ai_base_url, model=model,
                timeout=httpx.Timeout(connect=15, read=240, write=60, pool=15),
                usage_recorder=lambda model, value: usage.append({'model': model, 'usage': value}))
        return clients[model]

    def runner(model, prompt, payload):
        result = client(model).chat_completion([{'role': 'system', 'content': prompt}, {'role': 'user', 'content': payload}],
            max_tokens=5000, temperature=0.1, system_prompt=False, knowledge_compilation=True)
        if (result.get('response_metadata') or {}).get('finish_reason') == 'length':
            raise ValueError('真实响应达到输出上限，未标为完成')
        return result['content']

    report = {'production_writes': False, 'max_summary_chars': MAX_SUMMARY_CHARS, 'results': []}
    try:
        if args.page_evidence:
            directory = args.page_evidence.resolve()
            manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
            inputs, entries = {}, []
            for number, name in sorted(manifest['pages'].items(), key=lambda row: int(row[0])):
                path = directory/name
                payload = path.read_bytes()
                assert hashlib.sha256(payload).hexdigest() == manifest['outputs'][name]
                inputs[path] = hashlib.sha256(payload).hexdigest()
                note = payload.decode('utf-8')
                entry = {'page': int(number), 'source_text': note.split('## 原始提取\n\n', 1)[-1].split('\n\n## 逐页知识解读', 1)[0],
                         'text': note.split('## 逐页知识解读\n\n', 1)[-1]}
                preferred = preferred_visual_output(manifest['outputs'], int(number))
                if preferred:
                    path = directory/preferred
                    payload = path.read_bytes()
                    assert hashlib.sha256(payload).hexdigest() == manifest['outputs'][preferred]
                    inputs[path] = hashlib.sha256(payload).hexdigest()
                    entry.update(text=payload.decode('utf-8'), visual_rechecked=True)
                entries.append(entry)
            stored = {}
            body, _, review = SummaryCompiler(runner, stored.get, stored.update).compile(entries)
            assert len(body) <= MAX_SUMMARY_CHARS and all(c['approved'] for c in review['checks'])
            assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in inputs.items())
            (output/'course-summary.md').write_text(body, encoding='utf-8')
            report['results'].append({'kind': 'course', 'pages': len(entries), 'summary_chars': len(body),
                'source_budget': review['source_budget'], 'source_reviewed': True, 'source_unchanged': True})
            print(json.dumps({'phase': 'course_complete', **report['results'][-1]}, ensure_ascii=False), flush=True)
        if args.subtitle_directory:
            directory = args.subtitle_directory.resolve()
            raw = (directory/'raw.vtt').read_bytes()
            manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
            digest = hashlib.sha256(raw).hexdigest()
            assert digest == manifest['raw_sha256']
            stored = {}
            body, review = compile_summary(raw.decode('utf-8'), client('deepseek-chat'), cached=stored.get,
                                          commit=lambda k, v: stored.setdefault(k, v))
            assert len(body) <= MAX_SUMMARY_CHARS
            assert hashlib.sha256((directory/'raw.vtt').read_bytes()).hexdigest() == digest
            (output/'lesson.md').write_text(lesson_markdown(manifest, original_markdown(raw.decode('utf-8')), body), encoding='utf-8')
            report['results'].append({'kind': 'subtitle', 'source_cues': review['source_cues'], 'chunks': len(review['covered_chunks']),
                'summary_chars': len(body), 'calls': review['calls'], 'source_budget': review['source_budget'],
                'source_reviewed': True, 'source_unchanged': True})
            print(json.dumps({'phase': 'subtitle_complete', **report['results'][-1]}, ensure_ascii=False), flush=True)
        report.update(ok=True, usage=usage)
        (output/'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'phase': 'complete', 'output': str(output), **report}, ensure_ascii=False), flush=True)
    finally:
        for item in clients.values():
            item.close()


if __name__ == '__main__':
    main()
