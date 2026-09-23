"""Canvas 资料分类的唯一规则来源（不持久化分类结果）。"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

CATEGORY_LABELS = {
    "assignments": "课程作业",
    "courseware": "课件",
    "other": "其他",
}
CATEGORY_ORDER = tuple(CATEGORY_LABELS)
LEGACY_CATEGORY_ALIASES = {"supplementary": "other"}
LEGACY_CATEGORY_LABELS = ("补充资料",)
KEYWORDS = (
    ("assignments", ("作业", "homework", "assignment", "exercise", "实验", "lab")),
    ("courseware", ("课件", "讲义", "lecture", "slide", "ppt", "slides", "教案")),
)


def _matches(text: str, keyword: str) -> bool:
    normalized = keyword.casefold()
    if normalized.isascii():
        return normalized in re.split(r"[^a-z]+", text.casefold())
    return normalized in text.casefold()


def classify_material(
    *,
    module_names: Iterable[str] = (),
    module_item_names: Iterable[str] = (),
    folder_names: Iterable[str] = (),
    filename: str,
) -> str:
    """按模块名/模块项语义、Canvas 文件夹、文件名的固定优先级分类。"""
    module_signals = [*module_names, *module_item_names]
    for values in (module_signals, folder_names, (filename,)):
        for value in values:
            text = (value or "").strip()
            for category, keywords in KEYWORDS:
                if any(_matches(text, keyword) for keyword in keywords):
                    return category
    return "other"


def normalize_category(category: str | None) -> str:
    """Map persisted legacy values into the current category set."""
    normalized = LEGACY_CATEGORY_ALIASES.get(category, category)
    return normalized if normalized in CATEGORY_LABELS else "other"


def is_known_category(category: str | None) -> bool:
    return category in CATEGORY_LABELS or category in LEGACY_CATEGORY_ALIASES


def archive_folder_names(
    category: str | None, folder_names: Iterable[str]
) -> tuple[str, ...]:
    """Only assignment archives retain their Canvas folder hierarchy."""
    return tuple(folder_names) if normalize_category(category) == "assignments" else ()


def category_label(category: str) -> str:
    return CATEGORY_LABELS[normalize_category(category)]


def safe_folder_chain(folder_id: int | None, folder_by_id: dict[int, Any]) -> tuple[Any, ...]:
    """Return the finite Canvas root-to-leaf chain used by UI and archive paths."""
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
    if chain and chain[0].parent_folder_id is None:
        chain = chain[1:]
    return tuple(chain)


def load_module_signals(
    session: Any, *, course_ids: set[int] | None = None
) -> dict[int, tuple[tuple[str, ...], tuple[str, ...]]]:
    """从 CourseModuleItem 读取每个文件的模块名与模块项标题。"""
    from sqlalchemy import select

    from sjtu_learning_assistant.models import CourseModule, CourseModuleItem

    statement = (
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
            CourseModuleItem.id.asc(),
        )
    )
    if course_ids is not None:
        if not course_ids:
            return {}
        statement = statement.where(CourseModuleItem.course_id.in_(course_ids))

    names: dict[int, list[str]] = defaultdict(list)
    titles: dict[int, list[str]] = defaultdict(list)
    for item, module in session.execute(statement).all():
        file_id = item.content_file_id
        if file_id is None:
            continue
        if module.name and module.name not in names[file_id]:
            names[file_id].append(module.name)
        if item.title and item.title not in titles[file_id]:
            titles[file_id].append(item.title)
    return {
        file_id: (tuple(names[file_id]), tuple(titles[file_id]))
        for file_id in names.keys() | titles.keys()
    }
