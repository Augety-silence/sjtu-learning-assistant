#!/usr/bin/env python3
"""只读本机基础 MD / 总结进度与计量，不启动 worker、不读取字幕正文或凭据。"""
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.database import APP_SUPPORT_DIR, DEFAULT_SQLITE_PATH
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.markdown_progress import course_progress, transcript_progress


def main():
    settings = SettingsStore().load()
    paths = []
    usage = []
    if DEFAULT_SQLITE_PATH.is_file():
        with sqlite3.connect("file:" + quote(str(DEFAULT_SQLITE_PATH)) + "?mode=ro", uri=True) as connection:
            paths = [Path(row[0]) for row in connection.execute(
                "SELECT local_path FROM course_files WHERE is_active=1 AND download_status='downloaded' AND local_path IS NOT NULL")]
            usage = connection.execute("SELECT model, count(id), max(created_at), sum(prompt_tokens), sum(completion_tokens), sum(total_tokens) FROM provider_usage GROUP BY model").fetchall()
    print(json.dumps(dict(courses=course_progress(Path(settings.archive_root), paths),
        transcripts=transcript_progress(APP_SUPPORT_DIR / "transcripts" / "v1"),
        ai_enabled=settings.ai_enabled, ai_key_saved=settings.ai_key_saved, model=settings.ai_model,
        automatic=settings.course_markdown_enabled, daily_limit=0, usage_by_model=usage), ensure_ascii=False))


if __name__ == "__main__":
    main()
