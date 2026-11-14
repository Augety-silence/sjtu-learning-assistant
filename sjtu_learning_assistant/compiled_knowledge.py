"""编译结果的只读目录、关键词检索和分页读取；不调用模型或改写知识文件。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Callable, Any

from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.local_settings import SettingsStore

MAX_INDEX_BYTES = 2 * 1024**2
MAX_DOCUMENT_BYTES = 24 * 1024**2
MAX_ENTRIES = 5000
MAX_SEARCH_BYTES = 64 * 1024**2


class CompiledKnowledgeError(RuntimeError):
    """可展示、无凭据的知识读取错误。"""


def _safe(root: Path, relative: str = "") -> Path:
    if type(relative) is not str or len(relative) > 1024 or "\\" in relative or "\x00" in relative:
        raise CompiledKnowledgeError("知识路径无效。")
    parts = PurePosixPath(relative).parts
    if PurePosixPath(relative).is_absolute() or any(part in {"..", "."} for part in parts):
        raise CompiledKnowledgeError("知识路径越界。")
    if not root.is_absolute():
        raise CompiledKnowledgeError("知识目录无效。")
    target = root.joinpath(*parts)
    # 根目录及父链也不能是链接；不把解析后的越界目录当作新的授权根。
    if any(path.is_symlink() for path in (target, *target.parents)):
        raise CompiledKnowledgeError("知识目录不能包含符号链接。")
    if not target.resolve().is_relative_to(root.resolve()):
        raise CompiledKnowledgeError("知识路径越界。")
    return target


def _read_bytes(path: Path, maximum: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise CompiledKnowledgeError("知识文件不是普通文件或超过读取上限。")
            payload = stream.read(maximum + 1)
        if len(payload) > maximum:
            raise CompiledKnowledgeError("知识文件超过读取上限。")
        return payload
    except OSError:
        raise CompiledKnowledgeError("知识文件不可访问，请刷新结果。") from None


def _json(path: Path) -> dict:
    try:
        value = json.loads(_read_bytes(path, MAX_INDEX_BYTES).decode("utf-8"))
        if type(value) is not dict:
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise CompiledKnowledgeError("知识索引损坏；原文件未修改。") from None


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CompiledKnowledgeError(f"{name}超出范围。")
    return value


class CompiledKnowledge:
    def __init__(self, settings: Callable = lambda: SettingsStore().load(),
                 projects_path: Path = APP_SUPPORT_DIR / "local-projects.json"):
        self.settings = settings
        self.projects_path = projects_path

    def _catalog(self) -> tuple[list[dict], list[dict], list[str], bool]:
        sources, documents, issues = [], [], []
        truncated = False
        archive = Path(self.settings().archive_root)
        automatic = archive / "Knowledge Markdown"
        try:
            marker = _safe(automatic, "99_System/Course-Sync.json")
            if marker.is_file():
                manifest = _json(marker)
                if manifest.get("version") != 1 or manifest.get("source_root") != str(archive) or type(manifest.get("items")) is not dict:
                    raise CompiledKnowledgeError("自动课件索引格式无效。")
                source = {"id": "automatic-courses", "name": "自动课件 Markdown", "kind": "automatic", "root": str(automatic / "Courses")}
                sources.append(source)
                for relative, item in sorted(manifest["items"].items()):
                    if len(documents) >= MAX_ENTRIES:
                        truncated = True
                        break
                    if type(item) is not dict or not item.get("text_ready"):
                        continue
                    try:
                        path = _safe(automatic / "Courses", relative + ".md")
                        # 已失效或被移出归档的源，不再默默作为当前课程知识。
                        original = _safe(archive, relative)
                        if path.is_file() and original.is_file():
                            documents.append(self._entry(source, relative + ".md", item.get("status", "pending")))
                    except (CompiledKnowledgeError, TypeError):
                        issues.append("部分自动课件路径不可访问，已跳过。")
        except CompiledKnowledgeError as exc:
            issues.append(str(exc))
        try:
            if self.projects_path.exists():
                registry = _json(_safe(self.projects_path.parent, self.projects_path.name))
                projects = registry.get("projects")
                if registry.get("version") != 1 or type(projects) is not list or len(projects) > 100:
                    raise CompiledKnowledgeError("本地项目索引格式无效。")
                for project in projects:
                    if type(project) is not dict or any(type(project.get(key)) is not str for key in ("id", "name", "target_root")):
                        issues.append("部分本地项目索引无效，已跳过。")
                        continue
                    if re.fullmatch(r"[0-9a-f]{24}", project["id"]) is None or len(project["name"]) > 128:
                        issues.append("部分本地项目标识或名称无效，已跳过。")
                        continue
                    root = Path(project["target_root"])
                    try:
                        _safe(root)
                        if not root.is_dir():
                            continue
                        source = {"id": "project-" + project["id"], "name": project["name"] + " · 编译输出", "kind": "project", "root": str(root)}
                        sources.append(source)
                        visited = 0
                        for folder, directories, files in os.walk(root, followlinks=False):
                            visited += 1 + len(directories) + len(files)
                            if visited > MAX_ENTRIES or len(documents) >= MAX_ENTRIES:
                                truncated = True
                                break
                            directories[:] = sorted(name for name in directories if not name.startswith(".") and name not in {"99_System", "assets", "attachments"} and not (Path(folder) / name).is_symlink())
                            for name in sorted(files):
                                if name.startswith(".") or Path(name).suffix.casefold() != ".md":
                                    continue
                                path = Path(folder) / name
                                relative = path.relative_to(root).as_posix()
                                try:
                                    _safe(root, relative)
                                    if path.is_file():
                                        documents.append(self._entry(source, relative, "compiled"))
                                except CompiledKnowledgeError:
                                    issues.append("部分本地项目路径不可访问，已跳过。")
                    except (CompiledKnowledgeError, OSError):
                        issues.append("部分本地项目目录不可访问，已跳过。")
        except CompiledKnowledgeError as exc:
            issues.append(str(exc))
        for source in sources:
            source["count"] = sum(doc["source_id"] == source["id"] for doc in documents)
        return sources, documents, list(dict.fromkeys(issues)), truncated

    @staticmethod
    def _entry(source: dict, relative: str, status: str) -> dict:
        key = hashlib.sha256((source["id"] + "\0" + relative).encode()).hexdigest()[:32]
        return {"id": key, "source_id": source["id"], "source_name": source["name"], "relative_path": relative,
                "title": Path(relative).name, "status": status if status in {"completed", "pending", "failed", "compiled"} else "pending"}

    def sources(self) -> dict[str, Any]:
        sources, documents, issues, truncated = self._catalog()
        return {"items": sources, "count": len(documents), "issues": issues, "truncated": truncated}

    def _text(self, source: dict, document: dict) -> str:
        try:
            return _read_bytes(_safe(Path(source["root"]), document["relative_path"]), MAX_DOCUMENT_BYTES).decode("utf-8-sig")
        except UnicodeError:
            raise CompiledKnowledgeError("知识文件不是 UTF-8 Markdown。") from None

    def search(self, query: object = "", source_id: object = "", offset: object = 0, limit: object = 20) -> dict:
        if type(query) is not str or len(query) > 160 or "\x00" in query or type(source_id) is not str or len(source_id) > 128:
            raise CompiledKnowledgeError("知识搜索参数无效。")
        offset = _integer(offset, "offset", 0, MAX_ENTRIES)
        limit = _integer(limit, "limit", 1, 50)
        sources, documents, issues, truncated = self._catalog()
        by_id = {source["id"]: source for source in sources}
        if source_id and source_id not in by_id:
            raise CompiledKnowledgeError("知识来源已失效，请刷新结果。")
        matches, read_bytes = [], 0
        needle = query.strip().lower()
        for doc in documents:
            if source_id and doc["source_id"] != source_id:
                continue
            hit, snippet = 0, ""
            if needle:
                try:
                    text = self._text(by_id[doc["source_id"]], doc)
                    read_bytes += len(text.encode())
                    if read_bytes > MAX_SEARCH_BYTES:
                        truncated = True
                        issues.append("搜索读取达到上限，请按知识来源缩小范围。")
                        break
                    matched = re.search(re.escape(query.strip()), text, re.IGNORECASE)
                    hit = matched.start() if matched else -1
                    if hit < 0 and needle not in (doc["relative_path"] + doc["source_name"]).lower():
                        continue
                    hit = max(0, hit)
                    snippet = text[max(0, hit - 80):hit + 220]
                except CompiledKnowledgeError as exc:
                    issues.append(str(exc))
                    continue
            matches.append({**doc, "match_offset": hit, "snippet": snippet})
        end = offset + limit
        return {"items": matches[offset:end], "count": len(matches), "next_offset": end if end < len(matches) else None,
                "issues": list(dict.fromkeys(issues)), "truncated": truncated}

    def _resolve(self, document_id: object) -> tuple[dict, dict]:
        if type(document_id) is not str or re.fullmatch(r"[0-9a-f]{32}", document_id) is None:
            raise CompiledKnowledgeError("知识文档标识无效。")
        sources, documents, _, _ = self._catalog()
        document = next((item for item in documents if item["id"] == document_id), None)
        if document is None:
            raise CompiledKnowledgeError("知识文档已失效，请刷新结果。")
        return next(item for item in sources if item["id"] == document["source_id"]), document

    def read(self, document_id: object, offset: object = 0, max_chars: object = 8000) -> dict:
        offset = _integer(offset, "offset", 0, MAX_DOCUMENT_BYTES)
        max_chars = _integer(max_chars, "max_chars", 500, 8000)
        source, document = self._resolve(document_id)
        text = self._text(source, document)
        end = min(len(text), offset + max_chars)
        headings = re.findall(r"^#{1,6} .+$", text[:offset], flags=re.MULTILINE)
        return {**document, "text": text[offset:end], "offset": offset, "next_offset": end if end < len(text) else None,
                "total_chars": len(text), "section": headings[-1][:200] if headings else "文档开头",
                "citation": document["source_name"] + "/" + document["relative_path"], "truncated": end < len(text)}

    def open_path(self, source_id: object, document_id: object = "") -> Path:
        if document_id:
            source, document = self._resolve(document_id)
            if source["id"] != source_id:
                raise CompiledKnowledgeError("文档不属于此知识来源。")
            return _safe(Path(source["root"]), document["relative_path"])
        sources, _, _, _ = self._catalog()
        source = next((item for item in sources if item["id"] == source_id), None)
        if source is None:
            raise CompiledKnowledgeError("知识来源已失效。")
        return _safe(Path(source["root"]))
