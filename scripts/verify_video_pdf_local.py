#!/usr/bin/env python3
"""用已保存 Canvas 凭据只读核验最多三节录像课件；不调用模型、不改原件。"""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.canvas_sync import get_saved_token, build_http_client, DEFAULT_BASE_URL, DEFAULT_TIMEOUT_SECONDS
from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.transcript_service import TranscriptService, DEFAULT_TRANSCRIPT_ROOT
from sjtu_learning_assistant.video_service import SJTUVideoService, SJTUVideoError


def main():
    token = get_saved_token()
    if not token:
        print(json.dumps({"ok": False, "reason": "canvas_credential_missing"}))
        return 1
    transcripts = TranscriptService.__new__(TranscriptService)
    transcripts.root = DEFAULT_TRANSCRIPT_ROOT
    sources = list(dict.fromkeys(job.get("source_id") for batch in transcripts._all_batches() for job in batch.get("jobs", []) if str(job.get("source_id", "")).startswith("sjtu-video:")))[:3]
    results = []
    with build_http_client(DEFAULT_BASE_URL, token, DEFAULT_TIMEOUT_SECONDS) as canvas:
        remote = SJTUVideoService(lambda: canvas, slide_cache_root=APP_SUPPORT_DIR / "video-slides" / "v1")
        for source in sources:
            try:
                metadata = remote.slides_metadata(source)
                if metadata["status"] != "ready":
                    results.append({"status": metadata["status"], "pages": len(metadata["pages"])})
                    continue
                first = remote.slides_pdf(source)
                again = remote.slides_pdf(source)
                print(json.dumps({"ok": True, "real_sjtu_video_api": True, "pages": first["page_count"], "bytes": first["size"], "sha256": hashlib.sha256(first["data"]).hexdigest(), "cache_reuse_identical": first["data"] == again["data"], "earlier": results}, ensure_ascii=False))
                return 0
            except SJTUVideoError as exc:
                results.append({"status": "unavailable", "message": str(exc)})
    print(json.dumps({"ok": False, "real_sjtu_video_api": True, "probes": results}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
