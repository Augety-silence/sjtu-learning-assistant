"""Validated private page checkpoints; never persist signed image URLs or JWTs."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sjtu_learning_assistant.video_service import SJTUVideoError


class VideoSlideCache:
    def __init__(self, root: Path, source_id: str, records: list[dict[str, Any]]) -> None:
        # Signed query parameters rotate without changing the underlying page.
        identity = repr([(r["index"], r.get("order_time"), urlsplit(r["url"]).hostname,
                          urlsplit(r["url"]).path) for r in records])
        source = hashlib.sha256(source_id.encode()).hexdigest()
        revision = hashlib.sha256(identity.encode()).hexdigest()
        self.root = Path(root)
        for directory in (self.root, self.root / source, self.root / source / revision):
            if directory.is_symlink():
                raise SJTUVideoError("课件缓存目录不安全。")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = self.root / source / revision

    def read(self, name: str, limit: int) -> bytes | None:
        target = self.directory / name
        if target.is_symlink():
            raise SJTUVideoError("课件缓存文件不安全。")
        if not target.is_file() or target.stat().st_size > limit:
            return None
        with target.open("rb") as stream:
            value = stream.read(limit + 1)
        # Companion checksum detects interrupted/corrupted local checkpoints.
        checksum = self.directory / (name + ".sha256")
        if checksum.is_symlink():
            raise SJTUVideoError("课件缓存校验文件不安全。")
        if len(value) > limit or not checksum.is_file() or checksum.stat().st_size != 64:
            return None
        return value if hashlib.sha256(value).hexdigest() == checksum.read_text() else None

    def write(self, name: str, data: bytes) -> None:
        for filename, value in ((name, data), (name + ".sha256", hashlib.sha256(data).hexdigest().encode())):
            target = self.directory / filename
            if target.is_symlink():
                raise SJTUVideoError("课件缓存文件不安全。")
            descriptor, temporary_name = tempfile.mkstemp(prefix=".page-", dir=self.directory)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
