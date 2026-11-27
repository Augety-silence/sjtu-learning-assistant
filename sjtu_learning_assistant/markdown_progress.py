"""只读持久化转换进度；基础 MD 与 AI 总结分开，不触发扫描、模型或重试。"""
from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path

MAX_ITEMS = 5000
MAX_JSON_BYTES = 16 * 1024 * 1024


def _json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError()
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if type(value) is not dict:
        raise ValueError()
    return value


def _safe(root: Path, path: Path) -> bool:
    """路径边界内逐段拒绝符号链接，不解析到用户未授权的位置。"""
    if root.is_symlink():
        return False
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if any(part in {"..", "."} for part in relative.parts):
        return False
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return False
    return True


def _group() -> dict:
    return dict(total=0, completed=0, pending=0, failed=0,
                unavailable=0, ai_completed=0, ai_failed=0, issues=[], truncated=False)


def _source_states(root: Path) -> dict[str, str]:
    """只读抓取结果；兼容旧版空字幕记录，不把暂无字幕当作损坏清单。"""
    batches = root / 'batches'
    if not _safe(root, batches) or not batches.is_dir():
        return {}
    latest = {}
    for index, path in enumerate(batches.iterdir()):
        if index >= MAX_ITEMS:
            break
        if path.suffix != '.json' or not _safe(root, path):
            continue
        try:
            batch = _json(path)
            if not batch.get('source_only') or type(batch.get('jobs')) is not list:
                continue
            updated = str(batch.get('updated_at') or '')
            for job in batch['jobs'][:MAX_ITEMS]:
                if type(job) is not dict:
                    continue
                source_id = job.get('source_id')
                if type(source_id) is not str or not re.fullmatch(r'sjtu-video:\d+:\d+', source_id):
                    continue
                key = hashlib.sha256(source_id.encode()).hexdigest()[:32]
                empty = job.get('fetch_error_code') == 'source_empty' or (
                    job.get('status') == 'failed' and job.get('error') == '该录像暂无字幕。')
                value = 'empty' if empty else 'failed' if job.get('status') == 'failed' else 'pending'
                if key not in latest or updated >= latest[key][0]:
                    latest[key] = (updated, value)
        except (OSError, ValueError, TypeError, RecursionError):
            continue
    return {key: row[1] for key, row in latest.items()}


def _issue(group: dict, name: str, code: str) -> None:
    if len(group["issues"]) < 5:
        group["issues"].append(dict(name=name[:160], code=code))


def course_progress(source: Path, candidates: list[Path]) -> dict:
    from .course_inventory import local_course_inventory
    group = _group()
    target = source / "Knowledge Markdown"
    paths, inventory = local_course_inventory(source, candidates)
    group.update(total=len(paths), truncated=len(paths) > MAX_ITEMS or inventory["truncated"], inventory=inventory)
    items = {}
    marker = target / "99_System" / "Course-Sync.json"
    try:
        if _safe(source, marker) and marker.exists():
            state = _json(marker)
            if state.get("version") != 1 or state.get("source_root") != str(source) or type(state.get("items")) is not dict:
                raise ValueError()
            items = state["items"]
    except (OSError, ValueError, TypeError, RecursionError):
        _issue(group, "课件同步记录", "manifest")
    inventory.update(registered=sum(path.relative_to(source).as_posix() in items for path in paths if path.is_relative_to(source)))
    inventory["unregistered"] = len(paths) - inventory["registered"]
    for path in paths[:MAX_ITEMS]:
        try:
            if not _safe(source, path) or path.is_relative_to(target) or not path.is_file():
                raise ValueError()
            relative = path.relative_to(source).as_posix()
            item = items.get(relative, {})
            if type(item) is not dict:
                raise ValueError()
            mirror = target / "Courses" / (relative + ".md")
            ready = bool(item.get("text_ready") and item.get("mirror_hash") and _safe(source, mirror) and mirror.is_file())
            if ready:
                group["completed"] += 1
            elif item.get("status") == "failed":
                group["failed"] += 1
                _issue(group, path.name, "conversion")
            if ready and item.get("status") == "completed":
                group["ai_completed"] += 1
            if item.get("status") == "failed":
                group["ai_failed"] += 1
                _issue(group, path.name, "timeout" if "超时" in str(item.get("error")) else "ai_validation")
        except (OSError, ValueError, TypeError):
            group["failed"] += 1
            _issue(group, path.name, "source")
    group["pending"] = group["total"] - group["completed"] - group["failed"]
    return group


def transcript_progress(root: Path) -> dict:
    group = _group()
    videos = root / "videos"
    if not _safe(root, videos) or not videos.is_dir():
        return group
    try:
        source_states = _source_states(root)
        # 有界枚举，不读取字幕正文；原子清单发布期间仍可读取上一份快照。
        for index, directory in enumerate(videos.iterdir()):
            if index >= MAX_ITEMS:
                group["truncated"] = True
                break
            if not re.fullmatch(r"[a-f0-9]{32}", directory.name):
                continue
            group["total"] += 1
            name = "课堂字幕"
            try:
                if not _safe(root, directory) or not directory.is_dir():
                    raise ValueError()
                marker = directory / 'manifest.json'
                if not marker.exists() and not marker.is_symlink():
                    state = source_states.get(directory.name)
                    if state == 'empty':
                        group['unavailable'] += 1
                    elif state == 'failed':
                        group['failed'] += 1
                        _issue(group, name, 'source')
                    elif any(p.name != '.lesson.lock' for p in directory.iterdir()):
                        raise ValueError()
                    continue
                manifest = _json(marker)
                video = manifest.get("video", {})
                name = str(video.get("title") or name) if type(video) is dict else name
                notes = manifest.get("lesson_notes", {})
                records = manifest.get("artifacts", {})
                if type(notes) is not dict or type(records) is not dict:
                    raise ValueError()
                ready = all(type(records.get(kind)) is dict
                            and records[kind].get("path") == filename
                            and re.fullmatch(r"[a-f0-9]{64}", str(records[kind].get("sha256", "")))
                            and _safe(root, directory / filename) and (directory / filename).is_file()
                            for kind, filename in (("lesson", "lesson.md"), ("original", "original.md")))
                if ready:
                    group["completed"] += 1
                    if notes.get("status") == "completed" and (directory / "lecture-summary.md").is_file() and _safe(root, directory / "lecture-summary.md"):
                        group["ai_completed"] += 1
                elif manifest.get("status") in {"failed", "partial", "interrupted", "cancelled"}:
                    group["failed"] += 1
                    _issue(group, name, "source")
                if notes.get("error_code") or notes.get("status") == "user_modified":
                    group["ai_failed"] += 1
                    code = str(notes.get("error_code") or "user_modified")
                    _issue(group, name, code if code in {"timeout", "network", "rate_limit", "credential", "user_modified", "response_format"} else "ai_validation")
            except (OSError, ValueError, TypeError, RecursionError):
                group["failed"] += 1
                _issue(group, name, "manifest")
    except OSError:
        _issue(group, "字幕目录", "source")
    group["pending"] = group["total"] - group["completed"] - group["failed"] - group['unavailable']
    return group
