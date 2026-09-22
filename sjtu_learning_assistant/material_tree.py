"""Build a deterministic, read-only Canvas material tree."""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

CATEGORY_LABELS = {
    "assignments": "课程作业",
    "courseware": "课件",
    "supplementary": "补充资料",
    "other": "其他",
}
CATEGORY_ORDER = tuple(CATEGORY_LABELS)
KEYWORDS = (
    ("assignments", ("作业", "homework", "assignment", "exercise", "实验", "lab")),
    ("courseware", ("课件", "讲义", "lecture", "slide", "ppt", "slides", "教案")),
    (
        "supplementary",
        ("补充", "课程资料", "additional", "reading", "reference", "supplementary", "拓展", "参考"),
    ),
)


def classify_material(
    *,
    module_names: list[str],
    folder_names: list[str],
    filename: str,
) -> str:
    """Classify dynamically: module first, folder second, filename last."""
    for values in (module_names, folder_names, [filename]):
        for name in values:
            text = (name or "").strip().casefold()
            for category, keywords in KEYWORDS:
                for keyword in keywords:
                    normalized = keyword.casefold()
                    if normalized.isascii():
                        matched = normalized in re.split(r"[^a-z]+", text)
                    else:
                        matched = normalized in text
                    if matched:
                        return category
    return "other"


def safe_folder_chain(folder_id: int | None, folder_by_id: dict[int, Any]) -> list[Any]:
    """Return a finite root-to-leaf chain even if database links contain a cycle."""
    chain: list[Any] = []
    seen: set[int] = set()
    current = folder_id
    while current is not None and current not in seen:
        seen.add(current)
        folder = folder_by_id.get(current)
        if folder is None:
            break
        chain.append(folder)
        current = folder.parent_folder_id
    chain.reverse()
    # Canvas' top-level “course files” row is an API root, not a user folder.
    if chain and chain[0].parent_folder_id is None:
        chain = chain[1:]
    return chain


def _new_node(node_id: str, kind: str, name: str) -> dict[str, Any]:
    return {"id": node_id, "kind": kind, "name": name, "children": []}


def build_material_tree(session: Any) -> dict[str, Any]:
    """Build semester → course → category → Canvas folders → files."""
    from sqlalchemy import select

    from sjtu_learning_assistant.models import (
        Course,
        CourseFile,
        CourseFolder,
        CourseModule,
        CourseModuleItem,
    )

    courses = list(
        session.scalars(
            select(Course).order_by(Course.term_name.desc(), Course.name.asc())
        ).all()
    )
    files = list(
        session.scalars(
            select(CourseFile).where(CourseFile.is_active.is_(True))
        ).all()
    )
    folders = list(
        session.scalars(
            select(CourseFolder).where(CourseFolder.is_active.is_(True))
        ).all()
    )
    module_rows = list(
        session.execute(
            select(CourseModuleItem, CourseModule)
            .join(CourseModule, CourseModule.id == CourseModuleItem.module_id)
            .where(
                CourseModuleItem.is_active.is_(True),
                CourseModule.is_active.is_(True),
                CourseModuleItem.content_file_id.is_not(None),
            )
            .order_by(
                CourseModule.position.asc().nulls_last(),
                CourseModuleItem.position.asc().nulls_last(),
                CourseModule.id.asc(),
            )
        ).all()
    )

    course_by_id = {course.id: course for course in courses}
    folder_by_id = {folder.id: folder for folder in folders}
    modules_by_file: dict[int, list[str]] = defaultdict(list)
    for item, module in module_rows:
        if module.name not in modules_by_file[item.content_file_id]:
            modules_by_file[item.content_file_id].append(module.name)

    roots: list[dict[str, Any]] = []
    term_nodes: dict[str, dict[str, Any]] = {}
    course_nodes: dict[int, dict[str, Any]] = {}
    category_nodes: dict[tuple[int, str], dict[str, Any]] = {}

    for course in courses:
        term = course.term_name or "未分组学期"
        if term not in term_nodes:
            term_nodes[term] = _new_node(f"term:{len(term_nodes)}", "term", term)
            roots.append(term_nodes[term])
        course_node = _new_node(f"course:{course.source_id}", "course", course.name)
        course_node["course_id"] = course.source_id
        term_nodes[term]["children"].append(course_node)
        course_nodes[course.id] = course_node
        for category in CATEGORY_ORDER:
            node = _new_node(
                f"category:{course.source_id}:{category}",
                "category",
                CATEGORY_LABELS[category],
            )
            node["category"] = category
            course_node["children"].append(node)
            category_nodes[(course.id, category)] = node

    seen_source_ids: set[str] = set()
    ordered_files = sorted(
        files,
        key=lambda item: (
            course_by_id[item.course_id].name.casefold()
            if item.course_id in course_by_id
            else "",
            (item.display_name or item.filename or "").casefold(),
            item.id,
        ),
    )
    for file in ordered_files:
        if file.source_id in seen_source_ids or file.course_id not in course_nodes:
            continue
        seen_source_ids.add(file.source_id)
        chain = safe_folder_chain(file.folder_id, folder_by_id)
        category = classify_material(
            module_names=modules_by_file.get(file.id, []),
            # Nearest Canvas folder gets first chance within the folder signal.
            folder_names=[folder.name for folder in reversed(chain)],
            filename=file.display_name or file.filename or "无名文件",
        )
        parent = category_nodes[(file.course_id, category)]
        for folder in chain:
            node_id = f"folder:{file.course_id}:{category}:{folder.id}"
            existing = next(
                (node for node in parent["children"] if node["id"] == node_id),
                None,
            )
            if existing is None:
                existing = _new_node(node_id, "folder", folder.name)
                existing["position"] = folder.position
                parent["children"].append(existing)
            parent = existing
        parent["children"].append(
            {
                "id": f"file:{file.source_id}",
                "kind": "file",
                "name": file.display_name or file.filename or "无名文件",
                "source_id": file.source_id,
                "course_id": course_by_id[file.course_id].source_id,
                "category": category,
                "size": file.size,
                "updated_at": file.source_updated_at.isoformat()
                if file.source_updated_at
                else None,
                "download_status": file.download_status,
                "can_open": file.download_status == "downloaded" and bool(file.local_path),
            }
        )

    def sort_node(node: dict[str, Any]) -> None:
        for child in node.get("children", []):
            sort_node(child)
        if node.get("kind") not in {"root", "term", "course"}:
            node.get("children", []).sort(
                key=lambda child: (
                    0 if child["kind"] == "folder" else 1,
                    child.get("position")
                    if child.get("position") is not None
                    else 10**9,
                    child["name"].casefold(),
                )
            )

    for root in roots:
        sort_node(root)
    return {
        "root": {"id": "root", "kind": "root", "name": "全部资料", "children": roots},
        "categories": [
            {"id": key, "label": label} for key, label in CATEGORY_LABELS.items()
        ],
        "download_statuses": ["all", "downloaded", "pending", "failed"],
    }
