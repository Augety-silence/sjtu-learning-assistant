"""正常同步时补齐全部可访问学期的资料目录，不下载、不调用模型。"""
from __future__ import annotations

from sjtu_learning_assistant.canvas_sync import (
    CanvasCheckError,
    fetch_all_courses,
    fetch_course_announcements_incremental,
    fetch_course_assignments_incremental,
    fetch_course_files_incremental,
    fetch_course_folders,
    fetch_course_module_items,
    fetch_course_modules,
)
from sjtu_learning_assistant.repository import (
    get_sync_state,
    persist_canvas_data,
    persist_course_catalog,
)


def sync_historical_materials(engine, client, current_ids: set[str], *, progress=None):
    """保留当前待办范围，按课程保存进度；不可读资源不推进游标。"""
    catalog = fetch_all_courses(client)
    persist_course_catalog(engine, catalog)
    rows = [row for row in catalog if str(row["id"]) not in current_ids]
    issues = []
    file_count = 0
    for index, course in enumerate(rows):
        source = str(course["id"])
        if progress:
            progress(index, len(rows), "补齐往期课程资料目录")
        if course.get("access_restricted_by_date") is True:
            issues.append({"course_id": source, "resource": "course", "http": 403})
            continue
        files, folders, modules, items, cursors = {}, {}, {}, {}, {}
        state = get_sync_state(engine, "canvas", f"course:{source}:files")
        try:
            result = fetch_course_files_incremental(
                client, source, etag=state.cursor if state else None,
            )
            course_folders = fetch_course_folders(client, source)
        except CanvasCheckError as exc:
            if exc.status_code not in (403, 404):
                raise
            issues.append({"course_id": source, "resource": "files", "http": exc.status_code})
        else:
            folders[source] = course_folders
            cursors[source] = result.etag
            if not result.not_modified:
                files[source] = result.records
                file_count += len(result.records)
        try:
            course_modules = fetch_course_modules(client, source)
            course_items = []
            for module in course_modules:
                if module.get("id") is not None:
                    course_items.extend(fetch_course_module_items(client, source, module["id"]))
        except CanvasCheckError as exc:
            if exc.status_code not in (403, 404):
                raise
            issues.append({"course_id": source, "resource": "modules", "http": exc.status_code})
        else:
            modules[source] = course_modules
            items[source] = course_items
        announcements, assignments, announcement_cursors, assignment_cursors = {}, {}, {}, {}
        for resource, fetcher, records, checkpoints in (
            ("announcements", fetch_course_announcements_incremental, announcements, announcement_cursors),
            ("assignments", fetch_course_assignments_incremental, assignments, assignment_cursors),
        ):
            saved = get_sync_state(engine, "canvas", f"course:{source}:{resource}")
            try:
                result = fetcher(client, source, etag=saved.cursor if saved else None)
            except CanvasCheckError as exc:
                if exc.status_code not in (403, 404):
                    raise
                issues.append({"course_id": source, "resource": resource, "http": exc.status_code})
            else:
                if not result.not_modified:
                    records[source] = result.records
                checkpoints[source] = result.etag
        persist_canvas_data(
            engine, raw_courses=[course], announcements_by_course=announcements, assignments_by_course=assignments,
            announcement_cursors=announcement_cursors, assignment_cursors=assignment_cursors,
            folders_by_course=folders, files_by_course=files, modules_by_course=modules,
            module_items_by_course=items, file_cursors=cursors, default_scope=False,
        )
    if progress:
        progress(len(rows), len(rows), "往期资料目录已保存")
    return {"courses": len(rows), "files": file_count, "issues": issues}
