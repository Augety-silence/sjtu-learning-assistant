"""只读本机编译快照；仅输出数量、摘要和请求阶段，不输出正文或凭据。"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.local_settings import SettingsStore


def main():
    settings = SettingsStore().load()
    root = Path(settings.archive_root).resolve()
    manifest = root / "Knowledge Markdown/99_System/Course-Sync.json"
    items = json.loads(manifest.read_text())["items"]
    sources = hashlib.sha256()
    course_status = Counter()
    valid_sources = 0
    cooling = 0
    for relative, item in sorted(items.items()):
        path = root / relative
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
            valid_sources += 1
            sources.update(relative.encode())
            sources.update(hashlib.sha256(path.read_bytes()).digest())
        course_status[str(item.get("status"))] += 1
        try:
            retry = datetime.fromisoformat(item.get("retry_after", ""))
            cooling += retry.replace(tzinfo=retry.tzinfo or timezone.utc).timestamp() > time.time()
        except (ValueError, TypeError):
            pass
    notes = Counter()
    subtitle_hash = hashlib.sha256()
    cache_files = 0
    for directory in sorted((APP_SUPPORT_DIR / "transcripts/v1/videos").iterdir()):
        path = directory / "manifest.json"
        raw = directory / "raw.vtt"
        if directory.is_symlink() or not path.is_file():
            continue
        data = json.loads(path.read_text())
        if not raw.is_file():
            continue
        subtitle_hash.update(directory.name.encode())
        subtitle_hash.update(hashlib.sha256(raw.read_bytes()).digest())
        notes[str(data.get("lesson_notes", {}).get("status"))] += 1
        cache_files += sum(1 for p in directory.rglob("*.json") if p.is_file())
    runtime = APP_SUPPORT_DIR / "ai-runtime.sqlite3"
    request_stats = []
    recent_course = None
    dispatched = [(str(item.get("last_dispatched_at", "")), relative, item)
                  for relative, item in items.items() if item.get("last_dispatched_at")]
    if dispatched:
        last, relative, item = max(dispatched)
        source_path = root / relative
        if source_path.is_file():
            from sjtu_learning_assistant.knowledge_documents import CONTRACT, VISION_MODEL, digest
            source_id = digest(relative.encode())[:16]
            revision = digest((digest(source_path.read_bytes()) + CONTRACT + VISION_MODEL).encode())[:16]
            checkpoint = root / "Knowledge Markdown/90_Inbox/Documents" / source_id / revision / "manifest.json"
            state = json.loads(checkpoint.read_text()) if checkpoint.is_file() else {}
            recent_course = dict(source_id=source_id, source_type=source_path.suffix,
                status=item.get("status"), attempts=item.get("attempts"), dispatched=last,
                pages=len(state.get("pages", {})), outputs=len(state.get("outputs", {})))
    if runtime.is_file():
        with closing(sqlite3.connect(runtime.as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute("SELECT model,phase,coalesce(outcome,'active_or_queued'),count(id),"
                "max(queued),max(started),max(ended),sum(input),sum(output) "
                "FROM requests GROUP BY model,phase,outcome").fetchall()
            request_stats = [dict(zip(("model", "phase", "outcome", "count", "last_queued", "last_started", "last_ended", "input", "output"), row)) for row in rows]
    print(json.dumps(dict(timestamp=time.time(), courses=dict(course_status), subtitles=dict(notes),
        valid_course_sources=valid_sources, course_cooldown_items=cooling,
        automatic_enabled=settings.course_markdown_enabled, ai_enabled=settings.ai_enabled,
        recent_course_task=recent_course,
        source_digest=sources.hexdigest(), subtitle_digest=subtitle_hash.hexdigest(),
        subtitle_json_files=cache_files, requests=request_stats), ensure_ascii=False))


if __name__ == "__main__":
    main()
