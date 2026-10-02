"""Persistent registry for local course-compiler projects."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sjtu_learning_assistant.knowledge_compiler import KnowledgeCompilerService


class LocalProjectError(RuntimeError):
    """A bounded project-registry error safe to show in the desktop UI."""


REGISTRY_VERSION = 1
MAX_PROJECTS = 100


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class LocalProjectService:
    """Tracks many source/Vault pairs without modifying either project tree."""

    def __init__(self, path: Path, compiler: KnowledgeCompilerService) -> None:
        self.path = Path(path).expanduser()
        self.compiler = compiler
        self._lock = threading.RLock()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise LocalProjectError("本地项目索引无法读取，请先保留文件后再修复。") from None
        if (
            type(payload) is not dict
            or payload.get("version") != REGISTRY_VERSION
            or type(payload.get("projects")) is not list
        ):
            raise LocalProjectError("本地项目索引格式不正确。")
        projects = payload["projects"]
        if len(projects) > MAX_PROJECTS or any(type(item) is not dict for item in projects):
            raise LocalProjectError("本地项目索引内容不正确。")
        return projects

    def _save(self, projects: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".local-projects-", suffix=".json", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            content = json.dumps(
                {"version": REGISTRY_VERSION, "projects": projects},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _project_id(source_root: str, target_root: str) -> str:
        digest = hashlib.sha256(
            f"{source_root}\0{target_root}".encode("utf-8")
        ).hexdigest()
        return digest[:24]

    @staticmethod
    def _validate_project_id(value: object) -> str:
        if type(value) is not str or len(value) != 24 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise LocalProjectError("项目标识不正确。")
        return value

    def _public_project(self, project: dict[str, Any]) -> dict[str, Any]:
        source = Path(project["source_root"])
        target = Path(project["target_root"])
        issue = None
        if not source.is_dir():
            issue = "素材目录已移动或不可访问"
        elif not target.is_dir():
            issue = "输出目录已移动或不可访问"
        status: dict[str, Any] = {
            "status": "idle",
            "phase_index": 0,
            "total_phases": 0,
            "updated_at": None,
            "error": None,
        }
        if issue is None:
            try:
                task = self.compiler.project_status(str(target))
                status = {
                    key: task.get(key)
                    for key in (
                        "status",
                        "phase_index",
                        "total_phases",
                        "updated_at",
                        "error",
                    )
                }
            except Exception:
                issue = "编译状态暂时无法读取"
        return {
            "id": project["id"],
            "name": project["name"],
            "source_root": project["source_root"],
            "target_root": project["target_root"],
            "markdown_files": project["markdown_files"],
            "total_bytes": project["total_bytes"],
            "image_references": project["image_references"],
            "courses": list(project["courses"]),
            "truncated_courses": bool(project["truncated_courses"]),
            "available": issue is None,
            "issue": issue,
            "compile_status": status["status"],
            "phase_index": status["phase_index"],
            "total_phases": status["total_phases"],
            "last_compiled_at": status["updated_at"],
            "compile_error": status["error"],
            "created_at": project["created_at"],
            "updated_at": project["updated_at"],
        }

    def list(self) -> dict[str, Any]:
        with self._lock:
            projects = self._load()
            return {"items": [self._public_project(item) for item in projects]}

    def add(self, source_root: object, target_root: object) -> dict[str, Any]:
        source, target, inspection = self.compiler.validate_project(
            source_root, target_root
        )
        source_value = str(source)
        target_value = str(target)
        project_id = self._project_id(source_value, target_value)
        now = _now()
        with self._lock:
            projects = self._load()
            existing = next(
                (item for item in projects if item.get("id") == project_id), None
            )
            if existing is None and len(projects) >= MAX_PROJECTS:
                raise LocalProjectError(
                    f"最多登记 {MAX_PROJECTS} 个本地项目，请先移除不用的索引。"
                )
            project = {
                "id": project_id,
                "name": inspection["source_name"],
                "source_root": source_value,
                "target_root": target_value,
                "markdown_files": inspection["markdown_files"],
                "total_bytes": inspection["total_bytes"],
                "image_references": inspection["image_references"],
                "courses": inspection["courses"],
                "truncated_courses": inspection["truncated_courses"],
                "created_at": existing.get("created_at", now) if existing else now,
                "updated_at": now,
            }
            projects = [item for item in projects if item.get("id") != project_id]
            projects.insert(0, project)
            self._save(projects)
            return self._public_project(project)

    def refresh(self, project_id: object) -> dict[str, Any]:
        normalized = self._validate_project_id(project_id)
        with self._lock:
            projects = self._load()
            project = next(
                (item for item in projects if item.get("id") == normalized), None
            )
            if project is None:
                raise LocalProjectError("本地项目不存在或已移除。")
            source, target, inspection = self.compiler.validate_project(
                project["source_root"], project["target_root"]
            )
            project.update(
                {
                    "name": inspection["source_name"],
                    "source_root": str(source),
                    "target_root": str(target),
                    "markdown_files": inspection["markdown_files"],
                    "total_bytes": inspection["total_bytes"],
                    "image_references": inspection["image_references"],
                    "courses": inspection["courses"],
                    "truncated_courses": inspection["truncated_courses"],
                    "updated_at": _now(),
                }
            )
            self._save(projects)
            return self._public_project(project)

    def remove(self, project_id: object) -> dict[str, Any]:
        normalized = self._validate_project_id(project_id)
        with self._lock:
            projects = self._load()
            remaining = [item for item in projects if item.get("id") != normalized]
            if len(remaining) == len(projects):
                raise LocalProjectError("本地项目不存在或已移除。")
            self._save(remaining)
            return {"project_id": normalized, "removed": True}
