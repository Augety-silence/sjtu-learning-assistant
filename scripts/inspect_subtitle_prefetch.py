"""只读字幕预同步计数；不读取凭据、输出字幕正文或运行模型。"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.transcript_service import TranscriptService, DEFAULT_TRANSCRIPT_ROOT

service = TranscriptService.__new__(TranscriptService)
service.root = DEFAULT_TRANSCRIPT_ROOT
batches = [batch for batch in service._all_batches() if batch.get("origin") == "prefetch"]
jobs = [job for batch in batches for job in batch.get("jobs", [])]
directories = service.root / "videos"
originals = sum(1 for directory in directories.iterdir()
                if not directory.is_symlink() and directory.is_dir() and (directory / "raw.vtt").is_file())
print(json.dumps({"prefetch_batches": len(batches), "prefetch_jobs": len(jobs),
                  "states": Counter(job.get("status") for job in jobs), "cached_originals": originals}, ensure_ascii=False))
