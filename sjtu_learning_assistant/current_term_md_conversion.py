"""本地基础 MD 转换；保留图片与页图，不调用模型，不标记 AI 审校完成。"""
from __future__ import annotations

import os
from pathlib import Path
import re

from .document_text import DOCUMENT_EXTENSIONS, DocumentTextError, extract_document, _open_ooxml
from .media_features import _read_entry

TEXT_SUFFIXES = frozenset({'.txt', '.md', '.markdown', '.csv', '.json', '.yaml', '.yml',
                          '.py', '.m', '.r', '.c', '.cpp', '.h', '.java', '.tex'})
MAX_ASSET_BYTES = 256 * 1024**2


def convert_local(source: Path, name: str, destination: Path, cancelled=lambda: False) -> dict:
    from .current_term_markdown import safe_path, file_fingerprint
    if cancelled():
        raise InterruptedError()
    suffix = Path(name).suffix.casefold()
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    assets, used = {}, 0

    def write(relative: str, payload: bytes):
        nonlocal used
        if cancelled():
            raise InterruptedError()
        used += len(payload)
        if used > MAX_ASSET_BYTES:
            raise DocumentTextError('转换资源超过完整保存上限；临时原件已保留。')
        target = safe_path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open('xb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        return file_fingerprint(target)

    title = re.sub(r'[\r\n\x00-\x1f<>]', ' ', name)[:240]
    body = f'# {title}\n\n本地基础转换；未进行 AI 总结或审校。\n\n'
    if suffix in TEXT_SUFFIXES:
        payload = source.read_bytes()
        if len(payload) > 16 * 1024**2 or b'\0' in payload:
            raise DocumentTextError('文本文件超过上限或包含二进制内容；原件已保留。')
        try:
            text = payload.decode('utf-8-sig')
        except UnicodeError:
            text = payload.decode('gb18030')
        if not text.strip():
            raise DocumentTextError('文本为空；原件已保留。')
        fence = '`' * max(3, 1 + max((len(run) for run in re.findall(r'`+', text)), default=0))
        body += f'{fence}\n{text}\n{fence}\n'
        count = 1
    elif suffix in DOCUMENT_EXTENSIONS:
        document = extract_document(source, name, max_chars=6_000_000)
        if document.truncated or not document.units or len(document.units) != document.unit_count:
            raise DocumentTextError('文档转换不完整；临时原件已保留。')
        count = document.unit_count
        if suffix == '.pdf':
            from .knowledge_documents import page_images
            rendered = 0
            for number, jpeg in page_images(source):
                if number != rendered + 1 or jpeg is None or number > count:
                    raise DocumentTextError('PDF 页图不完整；原件已保留。')
                relative = f'assets/page-{number:04d}.jpg'
                assets[relative] = write(relative, jpeg)
                body += f'## 第 {number} 页\n\n{document.units[number-1]}\n\n![原始页面]({relative})\n\n'
                rendered += 1
            if rendered != count:
                raise DocumentTextError('PDF 页数与正文不一致；原件已保留。')
        else:
            body += '\n\n'.join(document.units) + '\n'
            # 提取器的模型候选会过滤小图/重复图；清理原件前额外持久化全部内嵌资源。
            archive, members = _open_ooxml(source)
            try:
                attachments = sorted(member for member in members if
                    re.match(r'^(?:ppt|word|xl)/(?:media|embeddings)/', member)
                    and not member.endswith('/'))
                if attachments:
                    body += '\n## 内嵌图片与附件\n\n'
                for number, member in enumerate(attachments, 1):
                    relative = f'assets/attachment-{number:04d}{Path(member).suffix[:12]}'
                    assets[relative] = write(relative, _read_entry(archive, member, 24 * 1024**2))
                    label = f'内嵌资源 {number}'
                    prefix = '!' if Path(member).suffix.casefold() in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'} else ''
                    body += f'{prefix}[{label}]({relative})\n\n'
            finally:
                archive.close()
    else:
        raise DocumentTextError('此格式暂不支持完整转 MD；临时原件已保留。')
    md = write('document.md', body.encode('utf-8'))
    return dict(md='document.md', md_fingerprint=md, assets=assets, units=count)
