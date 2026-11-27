"""Bounded extraction-only PPTX evidence, never Office/object/network execution."""
from __future__ import annotations
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from .document_text import (DocumentTextError, DocumentUnsupportedError, _open_ooxml,
    _xml, _Budget, _drawing_blocks, _notes_text, _alt_texts, _rels_name, _REL_ID)
from .media_features import MediaFeatureError, _local_tag, _read_entry
STATIC_PPTX_POLICY = 'static-source-text-20261125-v1'

@dataclass(frozen=True)
class StaticPptx:
    pages: tuple[dict, ...]
    inventory: dict
    previews: dict[str, bytes]
    @property
    def complete(self):
        return bool(self.pages) and self.inventory['parser_complete']

def _relations(archive, names, part, budget):
    local, external = dict(), 0
    name = _rels_name(part)
    if name not in names:
        return local, external
    for node in _xml(archive, name, budget):
        rid, kind = node.get('Id', ''), node.get('Type', '').rsplit('/', 1).pop()
        if node.get('TargetMode', '').casefold() == 'external':
            external += 1
            continue
        target = node.get('Target', '')
        if not rid or not target or chr(92) in target or chr(0) in target or ':' in target:
            raise DocumentTextError('Static PPTX relationship path is invalid.')
        resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(part), target))
        if resolved.startswith('../') or resolved in ('.', '..'):
            raise DocumentTextError('Static PPTX relationship escapes its package.')
        if resolved in names:
            if rid in local:
                raise DocumentTextError('Static PPTX has duplicate relationship IDs.')
            local[rid] = (kind, resolved)
    return local, external

def _cached_chart(root):
    lines = list()
    for node in root.iter():
        if _local_tag(node) in ('numCache', 'strCache'):
            points = list()
            for point in node:
                if _local_tag(point) == 'pt':
                    value = next((child.text or '' for child in point if _local_tag(child) == 'v'), '')
                    points.append(point.get('idx', 'unknown') + ': ' + value)
            if points:
                lines.append('; '.join(points))
    return '\n'.join(lines)

def extract_static_pptx(path: Path, max_chars: int = 6000000) -> StaticPptx:
    """All actual slides inventoried; local raster bytes not decoded or sent to AI."""
    if path.suffix.casefold() != '.pptx' or path.is_symlink() or path.stat().st_size > 104857600:
        raise DocumentUnsupportedError('Static route requires a bounded ordinary local PPTX.')
    archive, names = _open_ooxml(path, list_active_parts=True)
    budget = _Budget(max_chars)
    try:
        if any('vbaproject' in n.casefold() or any(p in n.casefold() for p in ('/macros/', '/activex/', '/customui/')) for n in names):
            raise DocumentUnsupportedError('Static route does not support macros, ActiveX or custom UI.')
        if sum(info.file_size for name, info in names.items() if name.endswith(('.xml', '.rels'))) > 33554432:
            raise DocumentTextError('Static PPTX XML exceeds the extraction budget.')
        presentation = _xml(archive, 'ppt/presentation.xml', budget)
        rels, presentation_external = _relations(archive, names, 'ppt/presentation.xml', budget)
        slides = list()
        slide_lists = tuple(node for node in presentation if _local_tag(node) == 'sldIdLst')
        if len(slide_lists) != 1:
            raise DocumentUnsupportedError('Static PPTX must have one physical slide list.')
        for node in slide_lists.__getitem__(0):
            if _local_tag(node) != 'sldId':
                raise DocumentUnsupportedError('Static PPTX slide list contains an unknown entry.')
            relation = rels.get(node.get(_REL_ID))
            if not relation or relation.__getitem__(0) != 'slide':
                raise DocumentUnsupportedError('Static PPTX slide identity is incomplete.')
            slides.append(relation.__getitem__(1))
        physical = set(n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml', n))
        if not 1 <= len(slides) <= 500 or len(set(slides)) != len(slides) or set(slides) != physical:
            raise DocumentUnsupportedError('Static PPTX cannot prove complete physical slide coverage.')
        previews, pages, rows = dict(), list(), list()
        image_bytes = 0
        for number, slide in enumerate(slides, 1):
            root = _xml(archive, slide, budget)
            local, external = _relations(archive, names, slide, budget)
            gaps = set()
            if external or presentation_external:
                gaps.add('external_assets_unopened')
            tags = set(_local_tag(node) for node in root.iter())
            for node in root.iter():
                if _local_tag(node) == 'graphicData' and node.get('uri') and not node.get('uri').endswith(('/table', '/chart')):
                    raise DocumentUnsupportedError('Static PPTX contains unsupported graphic data.')
            if tags & set(('AlternateContent', 'contentPart', 'relIds')):
                raise DocumentUnsupportedError('Static PPTX contains unsupported alternate/SmartArt content.')
            if tags & set(('oleObj', 'control')):
                gaps.add('embedded_objects_unopened')
            if tags & set(('timing', 'transition', 'videoFile', 'audioFile', 'media')):
                gaps.add('dynamic_media_not_reviewed')
            if tags & set(('cxnSp', 'custGeom', 'prstGeom')):
                gaps.add('shape_layout_not_reviewed')
            lines = _drawing_blocks(root)
            lines.extend('（图片说明）' + t for t in _alt_texts(root))
            assets = list()
            for kind, member in local.values():
                if kind == 'notesSlide':
                    if not member.startswith('ppt/notesSlides/') or not member.endswith('.xml'):
                        raise DocumentUnsupportedError('Static PPTX notes relationship is unsupported.')
                    text = _notes_text(_xml(archive, member, budget))
                    if text:
                        lines.append('（演讲者备注）\n' + text)
                elif kind == 'chart':
                    if not member.startswith('ppt/charts/') or not member.endswith('.xml'):
                        raise DocumentUnsupportedError('Static PPTX chart relationship is unsupported.')
                    cached = _cached_chart(_xml(archive, member, budget))
                    if cached:
                        lines.append('（图表缓存值；未重算公式、未打开工作簿）\n' + cached)
                    gaps.add('chart_visual_not_reviewed')
                    if not cached:
                        gaps.add('chart_cache_missing')
                elif kind == 'image':
                    gaps.add('raster_visual_not_reviewed')
                    if not member.startswith('ppt/media/') or Path(member).suffix.casefold() not in ('.png', '.jpg', '.jpeg'):
                        gaps.add('unsupported_image_preserved_unopened')
                        continue
                    info = names[member]
                    if info.file_size > 4194304 or image_bytes + info.file_size > 16777216:
                        gaps.add('preview_over_budget')
                        continue
                    if member not in previews:
                        payload = _read_entry(archive, member, 4194304)
                        if not (payload.startswith(bytes.fromhex('89504e470d0a1a0a')) or payload.startswith(bytes.fromhex('ffd8ff'))):
                            gaps.add('preview_invalid_header')
                            continue
                        previews[member] = payload
                        image_bytes += len(payload)
                    assets.append(member)
                elif kind in ('oleObject', 'package'):
                    gaps.add('embedded_objects_unopened')
                elif kind in ('slideLayout', 'slideMaster'):
                    gaps.add('inherited_layout_not_reviewed')
                elif kind not in ('hyperlink', 'tags', 'theme', 'comments', 'commentAuthors'):
                    raise DocumentUnsupportedError('Static PPTX contains an unsupported local relationship.')
            if not lines:
                gaps.add('no_static_text')
            text = '\n'.join(lines) or '（本页没有静态文字证据；不据此推断原页为空）'
            if len(text) > 12000:
                raise DocumentTextError('Static PPTX page text exceeds the extraction budget.')
            gap_text = '、'.join(sorted(gaps)) or '仅静态文字；未进行原件整页视觉核验'
            evidence = '【原件第 %d 页 / 共 %d 页】\n%s\n（静态证据缺口）%s' % (number, len(slides), text, gap_text)
            if budget.take(evidence) != evidence:
                raise DocumentTextError('Static PPTX extraction is incomplete; no summary authorized.')
            rows.append(dict(page=number, hidden=root.get('show') == '0', gaps=sorted(gaps), previews=assets, static_text_chars=len(text)))
            pages.append(dict(page=number, source_text=evidence, text=evidence, evidence_mode='static-extraction-only', gaps=sorted(gaps)))
        if not any(row['static_text_chars'] > 40 and 'no_static_text' not in row['gaps'] for row in rows):
            raise DocumentUnsupportedError('Static PPTX has insufficient source text for a supported summary.')
        inventory = dict(policy=STATIC_PPTX_POLICY, parser_complete=True, unopened_embedded_parts=sum('/embeddings/' in n.casefold() for n in names), page_count=len(slides), covered_pages=list(range(1, len(slides) + 1)), pages=rows,
                         original_rendered=False, external_assets_opened=False, embedded_payloads_opened=False, formulas_computed=False)
        return StaticPptx(tuple(pages), inventory, previews)
    except (MediaFeatureError, KeyError) as exc:
        raise DocumentTextError('Static PPTX structure is invalid or incomplete.') from exc
    finally:
        archive.close()
