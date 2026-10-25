from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PyInstaller.utils.win32.winmanifest import read_manifest_from_executable


LONG_PATH_TAG = (
    "{http://schemas.microsoft.com/SMI/2016/WindowsSettings}longPathAware"
)
EXECUTION_LEVEL_TAG = (
    "{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel"
)
ASSEMBLY_IDENTITY_TAG = (
    "{urn:schemas-microsoft-com:asm.v1}assemblyIdentity"
)


def verify_long_path_manifest(executable: Path) -> None:
    manifest = read_manifest_from_executable(str(executable))
    if isinstance(manifest, tuple):
        manifest = manifest[1]
    root = ET.fromstring(manifest)
    element = root.find(f".//{LONG_PATH_TAG}")
    if element is None or (element.text or "").strip().lower() != "true":
        raise RuntimeError("Windows 应用未启用 longPathAware，拒绝发布。")
    execution_level = root.find(f".//{EXECUTION_LEVEL_TAG}")
    if execution_level is None or execution_level.get("level") != "asInvoker":
        raise RuntimeError("Windows 应用清单缺少 asInvoker 执行级别，拒绝发布。")
    identities = root.findall(f".//{ASSEMBLY_IDENTITY_TAG}")
    if not any(
        identity.get("name") == "Microsoft.Windows.Common-Controls"
        and identity.get("version") == "6.0.0.0"
        for identity in identities
    ):
        raise RuntimeError("Windows 应用清单缺少 Common-Controls v6 依赖，拒绝发布。")


def _verify_or_report(executable: Path) -> int:
    try:
        verify_long_path_manifest(executable)
    except (ValueError, ET.ParseError, RuntimeError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(f"Windows 清单校验通过（longPathAware、asInvoker、Common-Controls v6）：{executable}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 Windows EXE 内嵌清单")
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("只能在 Windows 上校验 EXE 清单。")
    if not args.executable.is_file():
        parser.error(f"可执行文件不存在：{args.executable}")
    return _verify_or_report(args.executable)


if __name__ == "__main__":
    sys.exit(main())
