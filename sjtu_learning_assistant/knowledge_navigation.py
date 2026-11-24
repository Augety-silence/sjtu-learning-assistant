"""只合并身份一致的系统课件／字幕目录，不改变文档ID或物理路径。"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import PurePosixPath

AUTOMATIC_SOURCES = frozenset({"automatic-courses", "automatic-transcripts", "automatic-recording-asr"})


def navigation_scopes(documents: list[dict]) -> dict[str, str]:
    """已知学期、同路径课程身份必须唯一；冲突时保留来源隔离。

    虚拟scope只用于导航查询。读取／Finder定位仍用原source_id和文档ID。
    基于完整目录判断，不能因关键词或分页只剩一个结果而错误合并。
    未知身份只随既有文件夹浏览，不填充term_id/course_id，不参与身份过滤。
    """
    groups = defaultdict(list)
    for doc in documents:
        parts = PurePosixPath(doc["folder"]).parts
        if (doc.get("source_id") in AUTOMATIC_SOURCES or doc.get("source_id", "").startswith("automatic-recording-asr:")) and len(parts) >= 2:
            groups[parts[0]].append(doc)
    scopes = {}
    for top, docs in groups.items():
        terms = {doc.get("term_id") for doc in docs} - {None, "", "unknown"}
        if len(terms) != 1:
            continue
        courses = defaultdict(set)
        for doc in docs:
            if doc.get("course_id"):
                courses[PurePosixPath(doc["folder"]).parts[1]].add(doc["course_id"])
        if any(len(ids) > 1 for ids in courses.values()):
            continue
        identity = str(next(iter(terms))) + "\0" + top
        scope = "automatic-learning:" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        scopes.update({doc["id"]: scope for doc in docs})
    return scopes
