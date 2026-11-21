"""共享的 macOS 固定系统目录别名判断，不放开用户可控符号链接。"""
from __future__ import annotations

import sys
from pathlib import Path

_MACOS_SYSTEM_ALIASES = {
    Path("/var"): Path("/private/var"),
    Path("/tmp"): Path("/private/tmp"),
}


def is_macos_system_alias(path: Path) -> bool:
    if sys.platform != "darwin" or path not in _MACOS_SYSTEM_ALIASES:
        return False
    try:
        return path.resolve(strict=True) == _MACOS_SYSTEM_ALIASES[path]
    except (OSError, RuntimeError):
        return False
