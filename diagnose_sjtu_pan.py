#!/usr/bin/env python3
"""Read-only SJTU Pan connectivity diagnostic; credentials come from Keychain."""

from __future__ import annotations

import json
import sys

from sjtu_learning_assistant.cloud_storage import CloudStorageError, SJTUCloudPanProvider


def _size(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "-"


def main() -> int:
    try:
        with SJTUCloudPanProvider() as provider:
            provider.validate_token()
            space = provider.get_space_info()
            root = provider.list_directory(page=1, page_size=20)
        summary = {
            "connected": True,
            "space": {
                "capacity": _size(space.capacity),
                "used": _size(space.used),
                "available": _size(space.available),
            },
            "root": {
                "total": root.total,
                "shown": len(root.items),
                "directories": sum(item.is_directory for item in root.items),
                "files": sum(not item.is_directory for item in root.items),
            },
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except CloudStorageError as exc:
        print(f"交大云盘诊断失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
