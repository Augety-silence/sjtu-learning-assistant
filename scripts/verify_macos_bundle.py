#!/usr/bin/env python3
"""Validate a macOS bundle without disclosing matched sensitive values."""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import sys
from collections.abc import Iterable
from pathlib import Path

HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
DATABASE_CREDENTIAL_PATTERN = re.compile(
    rb"(?:postgres(?:ql)?|mysql|mariadb)(?:\+[A-Za-z0-9_]+)?://"
    rb"[^\s:/@'\"]+:([^\s/@'\"]+)@",
    re.IGNORECASE,
)
PLACEHOLDER_PASSWORDS = {
    b"changeme",
    b"example",
    b"password",
    b"pass",
    b"secret",
    b"test",
    b"your_password",
}
PERSONAL_PATH_PATTERN = re.compile(rb"/Users/(?!Shared/)[^/\x00\s]+/")
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".map",
    ".plist",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
FORBIDDEN_PACKAGE_NAMES = (
    "asyncpg",
    "fastapi",
    "psycopg",
    "psycopg2",
    "uvicorn",
)


def _relative(path: Path, app: Path) -> str:
    return path.relative_to(app).as_posix()


def _owned_scan_targets(app: Path, executable: Path, plist_path: Path) -> Iterable[Path]:
    """Yield only first-party/config artifacts, never third-party package examples."""
    for path in (plist_path, executable):
        if path.is_file():
            yield path

    own_roots = (
        app / "Contents" / "Resources" / "dashboard-web" / "dist",
        app / "Contents" / "Resources" / "config",
        app / "Contents" / "Resources" / "sjtu_learning_assistant",
    )
    for root in own_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in TEXT_SUFFIXES:
                yield path


def _has_sensitive_value(payload: bytes, forbidden_path: bytes) -> bool:
    if forbidden_path and forbidden_path in payload:
        return True
    if PERSONAL_PATH_PATTERN.search(payload):
        return True
    if any(pattern.search(payload) for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS):
        return True
    for match in DATABASE_CREDENTIAL_PATTERN.finditer(payload):
        password = match.group(1).lower()
        if password not in PLACEHOLDER_PASSWORDS and not password.startswith((b"<", b"${")):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--forbid-path", default=str(Path.home()))
    args = parser.parse_args()
    app = args.bundle.resolve()
    plist_path = app / "Contents" / "Info.plist"
    executable_path = app / "Contents" / "MacOS" / "SJTU Learning Assistant"
    errors: list[str] = []

    if not plist_path.is_file():
        errors.append("缺少 Contents/Info.plist")
    else:
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
        expected = {
            "CFBundleIdentifier": "io.github.sjtu-learning-assistant",
            "CFBundleName": "SJTU Learning Assistant",
        }
        for key, value in expected.items():
            if plist.get(key) != value:
                errors.append(f"Info.plist 的 {key} 不正确")
        if not plist.get("CFBundleShortVersionString"):
            errors.append("Info.plist 缺少版本")

    forbidden_path = os.fsencode(args.forbid_path.rstrip("/")) + b"/"
    seen: set[Path] = set()
    for file_path in _owned_scan_targets(app, executable_path, plist_path):
        if file_path in seen:
            continue
        seen.add(file_path)
        try:
            payload = file_path.read_bytes()
        except OSError:
            errors.append(f"无法读取 {_relative(file_path, app)}")
            continue
        if _has_sensitive_value(payload, forbidden_path):
            errors.append(f"发现个人路径或高置信秘密：{_relative(file_path, app)}")

    if not executable_path.is_file():
        errors.append("缺少应用主二进制")

    for file_path in app.rglob("*"):
        relative = _relative(file_path, app).lower()
        components = re.split(r"[/.\\_-]+", relative)
        if any(name in components for name in FORBIDDEN_PACKAGE_NAMES):
            errors.append(f"包含禁止的服务端或 PostgreSQL 驱动模块：{_relative(file_path, app)}")

    for error in errors:
        print(f"错误：{error}", file=sys.stderr)
    if errors:
        return 1
    print("macOS bundle 验证通过：plist、第一方路径/秘密及禁止模块检查均通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
