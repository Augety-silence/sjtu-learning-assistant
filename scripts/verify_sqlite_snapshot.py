#!/usr/bin/env python3
"""只读验证已关闭SQLite回退快照，不创建WAL/SHM，不读取学习正文。"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args()
    path = args.snapshot.resolve(strict=True)
    # 只用于已完成的独立快照，不可用于仍在写入的生产数据库。
    with sqlite3.connect(path.as_uri()+"?immutable=1", uri=True) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        healthy = connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    print(json.dumps(dict(ok=healthy and revision==args.expected_revision,
        revision=revision, integrity_ok=healthy, sha256=hashlib.sha256(path.read_bytes()).hexdigest())))
    return 0 if healthy and revision==args.expected_revision else 1


if __name__ == "__main__":
    raise SystemExit(main())
