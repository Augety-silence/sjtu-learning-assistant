"""真实未打开录像的隔离字幕验收；不修改生产字幕，不调用模型。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.canvas_sync import get_saved_token, build_http_client, DEFAULT_BASE_URL
from sjtu_learning_assistant.database import DEFAULT_SQLITE_PATH
from sjtu_learning_assistant.transcript_service import TranscriptService, DEFAULT_TRANSCRIPT_ROOT
from sjtu_learning_assistant.transcript_pipeline import parse_vtt
from sjtu_learning_assistant.video_service import SJTUVideoService


def production_snapshot():
    return {str(path.relative_to(DEFAULT_TRANSCRIPT_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (DEFAULT_TRANSCRIPT_ROOT / "videos").glob("*/raw.vtt") if not path.is_symlink()}


def load_sample():
    token = get_saved_token()
    if not token:
        raise RuntimeError("本机未保存 Canvas 凭据。")
    with sqlite3.connect(f"file:{DEFAULT_SQLITE_PATH}?mode=ro", uri=True) as connection:
        courses = connection.execute("select source_id, name from courses order by id desc").fetchall()
    existing = TranscriptService.__new__(TranscriptService)
    existing.root = DEFAULT_TRANSCRIPT_ROOT
    known_courses = {str(job.get("source_id", "")).split(":")[1]
                     for batch in existing._all_batches() for job in batch.get("jobs", [])
                     if str(job.get("source_id", "")).startswith("sjtu-video:")}
    courses.sort(key=lambda row: str(row[0]) not in known_courses)
    started = time.monotonic()
    with build_http_client(DEFAULT_BASE_URL, token, 15) as canvas:
        remote = SJTUVideoService(lambda: canvas, timeout=8)
        for course_id, name in courses[:8]:
            if time.monotonic() - started > 45:
                break
            if not str(course_id).isdigit():
                continue
            try:
                videos = remote.list_course_videos(course_id, course_name=name, limit=10000)
            except Exception:
                continue
            for video in videos:
                if existing.cached_subtitles(video["source_id"]) is not None:
                    continue
                captions = remote.subtitles(video["source_id"])
                if captions.get("status") != "ready":
                    continue
                with tempfile.TemporaryDirectory(prefix="sjtu-subtitle-acceptance-") as temporary:
                    def no_ai():
                        raise AssertionError("原始字幕同步不应调用模型。")
                    service = TranscriptService(Path(temporary) / "transcripts", autostart_worker=False,
                                                subtitle_fetcher=remote.subtitles, ai_context_provider=no_ai)
                    before = production_snapshot()
                    try:
                        queued = service.prefetch(course_id=str(course_id), course_name=name,
                                                  videos=[{**video, "title": video["name"]}])
                        service.run_pending(source_only=True)
                        local = service.cached_subtitles(video["source_id"])
                        assert local and local["vtt"] == captions["vtt"]
                        assert service.prefetch(course_id=str(course_id), course_name=name,
                                                videos=[{**video, "title": video["name"]}]) == 0
                        assert before == production_snapshot()
                        playback = remote.playback(video["source_id"])
                        assert playback["source_id"] == video["source_id"]
                        cues = parse_vtt(local["vtt"])
                        report = {"ok": True, "real_video_api": True, "previously_unopened": True,
                                  "queued": queued, "cues": len(cues), "model_calls": 0,
                                  "source_unchanged": True, "playback_source_matches": True,
                                  "seconds": round(time.monotonic() - started, 2)}
                        return {"url": playback["url"], "vtt": local["vtt"],
                                "cues": [{"startMs": cue.start_ms, "endMs": cue.end_ms, "text": cue.text} for cue in cues],
                                "report": report}
                    finally:
                        service.close()
            if time.monotonic() - started > 45:
                break
    raise RuntimeError("未在验收范围内找到可读取的未打开录像字幕。")


if __name__ == "__main__":
    try:
        print(json.dumps(load_sample()["report"], ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}, ensure_ascii=False))
        raise SystemExit(1)
