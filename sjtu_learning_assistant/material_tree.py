"""Build a deterministic, read-only Canvas material tree."""

from __future__ import annotations

from typing import Any

from sjtu_learning_assistant.material_classifier import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    archive_folder_names,
    classify_material,
    is_known_category,
    load_module_signals,
    normalize_category,
    safe_folder_chain,
)


def _new_node(node_id: str, kind: str, name: str) -> dict[str, Any]:
    return {"id": node_id, "kind": kind, "name": name, "children": []}


def build_material_tree(session: Any) -> dict[str, Any]:
    """Build semester → course → category → Canvas folders → files."""
    from sqlalchemy import select

    from sjtu_learning_assistant.models import Course, CourseFile, CourseFolder

    courses = list(
        session.scalars(
            select(Course).order_by(Course.term_name.desc(), Course.name.asc())
        ).all()
    )
    files = list(
        session.scalars(select(CourseFile).where(CourseFile.is_active.is_(True))).all()
    )
    folders = list(
        session.scalars(
            select(CourseFolder).where(CourseFolder.is_active.is_(True))
        ).all()
    )

    course_by_id = {course.id: course for course in courses}
    folder_by_id = {folder.id: folder for folder in folders}
    module_signals = load_module_signals(session)

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
            node["course_id"] = course.source_id
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
        canvas_chain = safe_folder_chain(file.folder_id, folder_by_id)
        module_names, module_item_names = module_signals.get(file.id, ((), ()))
        rule_category = classify_material(
            module_names=module_names,
            module_item_names=module_item_names,
            folder_names=[folder.name for folder in reversed(canvas_chain)],
            filename=file.display_name or file.filename or "无名文件",
        )
        automatic_category = (
            normalize_category(file.ai_category)
            if is_known_category(file.ai_category)
            else rule_category
        )
        manual_override = bool(
            file.manual_override and is_known_category(file.manual_category)
        )
        category = (
            normalize_category(file.manual_category)
            if manual_override
            else automatic_category
        )
        if manual_override and file.manual_folder_id is not None:
            manual_folder = folder_by_id.get(file.manual_folder_id)
            chain = (
                safe_folder_chain(file.manual_folder_id, folder_by_id)
                if manual_folder is not None and manual_folder.course_id == file.course_id
                else ()
            )
        else:
            chain = () if manual_override else canvas_chain
        chain = archive_folder_names(category, chain)
        parent = category_nodes[(file.course_id, category)]
        for folder in chain:
            node_id = f"folder:{file.course_id}:{category}:{folder.id}"
            existing = next(
                (node for node in parent["children"] if node["id"] == node_id), None
            )
            if existing is None:
                existing = _new_node(node_id, "folder", folder.name)
                existing["position"] = folder.position
                existing["course_id"] = course_by_id[file.course_id].source_id
                existing["category"] = category
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
                "manual_override": manual_override,
                "size": file.size,
                "updated_at": file.source_updated_at.isoformat()
                if file.source_updated_at
                else None,
                "download_status": file.download_status,
                "local_path": file.local_path,
                "can_open": file.download_status == "downloaded"
                and bool(file.local_path),
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
