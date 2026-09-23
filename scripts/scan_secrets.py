#!/usr/bin/env python3
"""Scan repository text files for likely committed secrets without printing values."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s:'\"]+:[^\s@'\"]+@", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key|secret|token|password)\s*[=:]\s*['\"][A-Za-z0-9_./+=-]{20,}['\"]", re.IGNORECASE),
)
SKIP_SUFFIXES = {".png", ".icns", ".jpg", ".jpeg", ".gif", ".pdf", ".lock"}
ALLOWLIST = {
    "tests/test_archive_service.py",
    "tests/test_canvas_client.py",
    "tests/test_database.py",
    "tests/test_desktop_app.py",
    "tests/test_notifications.py",
    "tests/test_sjtu_pan.py",
    "tests/test_sync_runner.py",
}


def candidates() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def main() -> int:
    findings: set[str] = set()
    for file_path in candidates():
        relative = file_path.relative_to(ROOT).as_posix()
        if relative in ALLOWLIST or file_path.suffix.lower() in SKIP_SUFFIXES or not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(pattern.search(text) for pattern in PATTERNS):
            findings.add(relative)
    if findings:
        print("疑似秘密出现在以下文件（不显示匹配内容）：", file=sys.stderr)
        for name in sorted(findings):
            print(name, file=sys.stderr)
        return 1
    print("秘密模式扫描通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
