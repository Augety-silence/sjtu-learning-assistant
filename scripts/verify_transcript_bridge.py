#!/usr/bin/env python3
"""只读检查本机已保存字幕经 DesktopBridge 读取，绝不运行同步或模型。"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_app import DesktopBridge
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.transcript_service import TranscriptService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    if args.root.is_symlink() or not args.root.is_dir():
        parser.error("字幕根目录必须是已存在的真实目录")
    # Do not call __init__: it creates storage, chmods it and recovers jobs.
    service = TranscriptService.__new__(TranscriptService)
    service.root = args.root
    dashboard = DashboardService.__new__(DashboardService)
    dashboard._client_lock = threading.RLock()
    dashboard._transcript_service_instance = service
    bridge = DesktopBridge(dashboard)
    counts = dict(lesson=0, lesson_summary=0, original=0)
    failed = 0
    not_saved = 0
    note_status = Counter()
    note_errors = Counter()
    seen = set()
    for batch in service._all_batches():
        for job in batch.get("jobs", []):
            source = job.get("source_id")
            if not source or source in seen:
                continue
            seen.add(source)
            if len(seen) > 1000:
                raise RuntimeError("任务数量超过只读验收范围")
            directory = service._video_dir(source)
            if not directory.exists() or not (directory / "manifest.json").is_file():
                not_saved += 1
                continue
            notes = service._read_json(directory / "manifest.json").get("lesson_notes")
            if isinstance(notes, dict):
                note_status[str(notes.get("status", "unknown"))] += 1
                if notes.get("error_code"):
                    note_errors[str(notes["error_code"])] += 1
            listed = bridge.invoke("transcript_artifacts", {"job_id": job.get("id")})
            if not listed["ok"]:
                failed += 1
                continue
            for item in listed["data"]["items"]:
                if item["kind"] not in counts:
                    continue
                result = bridge.invoke("transcript_artifact_read", {"artifact_id": item["id"]})
                if result["ok"] and result["data"]["content"].strip():
                    counts[item["kind"]] += 1
                else:
                    failed += 1
    print(json.dumps(dict(checked_jobs=len(seen), jobs_without_saved_source=not_saved,
        readable=counts, failures=failed, note_status=note_status, note_error_codes=note_errors), ensure_ascii=False))
    return 1 if failed or not counts["original"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
