#!/usr/bin/env python3
"""只读核验本机知识目录身份和跨来源导航，不打印正文／凭据，不运行同步。"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge
from sjtu_learning_assistant.database import DEFAULT_SQLITE_PATH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-unified", action="store_true")
    args = parser.parse_args()
    path = DEFAULT_SQLITE_PATH.resolve(strict=True)
    # 活跃WAL库使用正常mode=ro；不使用immutable、不迁移、不恢复数据库。
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(
        path.as_uri() + "?mode=ro", uri=True), poolclass=NullPool)
    try:
        service = CompiledKnowledge(engine=engine)
        result = service.search(limit=50)
        sources, documents, issues, truncated = service._catalog()
        roots = [item for item in result["folders"] if "/" not in item["path"]]
        checks = []
        for item in roots:
            scoped = service.search(source_id=item["source_id"], folder=item["path"], limit=50)
            assert scoped["count"] == item["count"], (item, scoped["count"])
            checks.append({"folder": item["name"], "scope": item["source_id"], "count": item["count"]})
        if args.expect_unified:
            assert any(item["source_id"].startswith("automatic-learning:") for item in roots), roots
            assert len({item["path"] for item in roots}) == len(roots), roots
        print(json.dumps({"read_only": True, "count": len(documents),
            "sources": {s["id"]: s["count"] for s in sources}, "roots": checks,
            "term_ids": dict(Counter(d.get("term_id", "missing") for d in documents)),
            "unique_document_ids": len({d["id"] for d in documents}),
            "issues": issues, "truncated": truncated}, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
