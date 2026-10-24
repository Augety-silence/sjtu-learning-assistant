from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PyInstaller.utils.win32.winmanifest import read_manifest_from_executable


LONG_PATH_TAG = (
    "{http://schemas.microsoft.com/SMI/2016/WindowsSettings}longPathAware"
)


def verify_long_path_manifest(executable: Path) -> None:
    manifest = read_manifest_from_executable(str(executable))
    root = ET.fromstring(manifest)
    element = root.find(f".//{LONG_PATH_TAG}")
    if element is None or (element.text or "").strip().lower() != "true":
        raise RuntimeError("Windows 应用未启用 longPathAware，拒绝发布。")


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 Windows EXE 内嵌清单")
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    if not args.executable.is_file():
        parser.error(f"可执行文件不存在：{args.executable}")
    verify_long_path_manifest(args.executable)
    print(f"Windows longPathAware 清单校验通过：{args.executable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
