"""课件只读盘点；发现未登记原件不会加入自动编译队列。"""
from __future__ import annotations

import os
from pathlib import Path

AUTO_COURSE_SUFFIXES = frozenset({".pptx", ".pdf", ".ppt", ".pptm", ".docm", ".xlsm", ".ppsm", ".potm"})
MAX_SCAN_ENTRIES = 50000


def local_course_inventory(source: Path, candidates: list[Path]) -> tuple[list[Path], dict]:
    from .markdown_progress import _safe
    known = {path if path.is_absolute() else source / path for path in candidates}
    found = set()
    visited = 0
    truncated = False
    if _safe(source, source) and source.is_dir():
        for folder, directories, files in os.walk(source, followlinks=False):
            directories[:] = sorted(name for name in directories
                                    if not name.startswith(".") and name != "Knowledge Markdown"
                                    and not (Path(folder) / name).is_symlink())
            visited += 1 + len(directories) + len(files)
            if visited > MAX_SCAN_ENTRIES:
                truncated = True
                break
            for name in files:
                path = Path(folder) / name
                if not name.startswith(".") and _safe(source, path) and path.is_file():
                    found.add(path)
    eligible = {path for path in known | found if path.suffix.casefold() in AUTO_COURSE_SUFFIXES
                and not path.is_relative_to(source / "Knowledge Markdown")}
    return sorted(eligible, key=str), dict(
        downloaded_records=len(known), local_originals=len(found),
        supported=len(eligible), unsupported_local=sum(path.suffix.casefold() not in AUTO_COURSE_SUFFIXES for path in found),
        untracked_local=sum(path not in known for path in found),
        untracked_supported=sum(path not in known for path in eligible),
        missing_local=sum(not _safe(source, path) or not path.is_file() for path in known),
        truncated=truncated,
    )
