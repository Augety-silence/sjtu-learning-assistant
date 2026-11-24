"""编译结果的只读目录、关键词检索和分页读取；不调用模型或改写知识文件。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Callable, Any

from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.path_safety import is_macos_system_alias
from sjtu_learning_assistant.video_service import parse_remote_source_id, SJTUVideoError

MAX_INDEX_BYTES = 2 * 1024**2
MAX_DOCUMENT_BYTES = 24 * 1024**2
MAX_ENTRIES = 5000
MAX_SEARCH_BYTES = 64 * 1024**2
MAX_MATCH_PASSAGES = 6


def _match_passages(text: str, terms: tuple[str, ...]) -> tuple[list[dict], int]:
    """同时保留分散的前部与后部命中，避免旧页稿的目录遮住实际内容。

    偏移始终对应原始 Unicode 正文；重复内容与页码索引均采用有界内存。
    """
    first, tail = [], deque(maxlen=MAX_MATCH_PASSAGES // 2)
    count, last = 0, -1000
    pages = re.finditer(r"^#\s+第\s*([0-9]{1,5})\s*页\s*$", text, re.MULTILINE)
    next_page, source_page = next(pages, None), None
    for match in re.finditer("|".join(re.escape(term) for term in terms), text, re.IGNORECASE):
        count += 1
        hit = match.start()
        if hit - last < 800:
            continue
        last = hit
        passage = {"match_offset": hit, "snippet": text[max(0, hit - 80):hit + 220]}
        while next_page is not None and next_page.start() <= hit:
            source_page = int(next_page.group(1))
            next_page = next(pages, None)
        if source_page is not None:
            passage["source_page"] = source_page
        if len(first) < MAX_MATCH_PASSAGES // 2:
            first.append(passage)
        else:
            tail.append(passage)
    return first + list(tail), count


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
    # 仅允许固定 macOS 系统别名；不把用户链接解析后的目录当作授权根。
    if any(path.is_symlink() and not is_macos_system_alias(path) for path in (target, *target.parents)):
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
                 projects_path: Path = APP_SUPPORT_DIR / "local-projects.json", metadata: Callable | None = None, engine=None,
                 transcript_root: Path | None = None, transcript_metadata: Callable | None = None, recording_asr_root: Path | None = None):
        self.settings = settings
        self.projects_path = projects_path
        if metadata is None and engine is not None:
            from sjtu_learning_assistant.course_scope import knowledge_metadata
            metadata = lambda: knowledge_metadata(engine, self.settings().archive_root)
        self.metadata = metadata or (lambda: {})
        self.transcript_root = Path(transcript_root) if transcript_root is not None else projects_path.parent / "transcripts/v1"
        if transcript_metadata is None and engine is not None:
            from sjtu_learning_assistant.course_scope import transcript_course_metadata
            transcript_metadata = lambda: transcript_course_metadata(engine)
        self.transcript_metadata = transcript_metadata or (lambda: dict())
        self.recording_asr_root = Path(recording_asr_root) if recording_asr_root is not None else projects_path.parent / "recording-asr" / "v1"

    def _catalog(self) -> tuple[list[dict], list[dict], list[str], bool]:
        sources, documents, issues = [], [], []
        truncated = False
        archive = Path(self.settings().archive_root)
        metadata = self.metadata()
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
                            entry = self._entry(source, relative + ".md", item.get("status", "pending"))
                            entry.update(error=item.get("error") if type(item.get("error")) is str else None,
                                         contract=item.get("contract"), original_path=relative)
                            entry.update(metadata.get(relative, {}))
                            documents.append(entry)
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
                        course_documents: dict[str, tuple[tuple[bool, float], dict]] = {}
                        for folder, directories, files in os.walk(root, followlinks=False):
                            visited += 1 + len(directories) + len(files)
                            if visited > MAX_ENTRIES or len(documents) >= MAX_ENTRIES:
                                truncated = True
                                break
                            directories[:] = sorted(name for name in directories if not name.startswith(".") and name not in {"99_System", "assets", "attachments", "pages", "learning", "Previous", "history"} and not (Path(folder) / name).is_symlink())
                            for name in sorted(files):
                                if name.startswith(".") or Path(name).suffix.casefold() != ".md":
                                    continue
                                path = Path(folder) / name
                                relative = path.relative_to(root).as_posix()
                                try:
                                    _safe(root, relative)
                                    if path.is_file():
                                        if relative.startswith("90_Inbox/Documents/"):
                                            if name != "document.md":
                                                continue
                                            manifest = _json(_safe(root, (path.parent / "manifest.json").relative_to(root).as_posix()))
                                            original = manifest.get("source")
                                            if manifest.get("status") != "completed" or type(original) is not str or not original:
                                                continue
                                            _safe(root, original)
                                            entry = self._entry(source, relative, "compiled")
                                            contract = manifest.get("study_contract") or manifest.get("contract")
                                            entry.update(title=PurePosixPath(original).name + ".md", folder=PurePosixPath(original).parent.as_posix(), contract=contract)
                                            # 同一来源只展示最新知识契约/版本，页稿和旧版本仍留在磁盘。
                                            rank = ({"course-summary-v1": 2, "course-study-v3": 1}.get(contract, 0), path.stat().st_mtime)
                                            if original not in course_documents or rank > course_documents[original][0]:
                                                course_documents[original] = (rank, entry)
                                        else:
                                            documents.append(self._entry(source, relative, "compiled"))
                                except CompiledKnowledgeError:
                                    issues.append("部分本地项目路径不可访问，已跳过。")
                        documents.extend(entry for _, entry in course_documents.values())
                    except (CompiledKnowledgeError, OSError):
                        issues.append("部分本地项目目录不可访问，已跳过。")
        except CompiledKnowledgeError as exc:
            issues.append(str(exc))
        # 既有检索/读取入口接入完整课堂MD；展示按真实学期/课程分组，存储保留稳定视频ID。
        try:
            root = _safe(self.transcript_root, "videos")
            if root.is_dir():
                course_metadata = self.transcript_metadata()
                source = {"id": "automatic-transcripts", "name": "课堂字幕与总结", "kind": "automatic", "root": str(root)}
                for directory in sorted(root.iterdir())[:MAX_ENTRIES]:
                    if len(documents) >= MAX_ENTRIES:
                        truncated = True
                        break
                    if not re.fullmatch(r"[0-9a-f]{32}", directory.name) or directory.is_symlink() or not directory.is_dir():
                        continue
                    try:
                        manifest = _json(_safe(root, directory.name + "/manifest.json"))
                        identity = manifest.get("source", {}).get("source_id")
                        course_id, _ = parse_remote_source_id(identity)
                        if hashlib.sha256(identity.encode()).hexdigest()[:32] != directory.name:
                            continue
                        path = _safe(root, directory.name + "/lesson.md")
                        if not path.is_file() or not _safe(root, directory.name + "/raw.vtt").is_file():
                            continue
                        notes = manifest.get("lesson_notes", {})
                        record = manifest.get("artifacts", {}).get("lesson", {})
                        unchanged = hashlib.sha256(_read_bytes(path, MAX_DOCUMENT_BYTES)).hexdigest() == record.get("sha256")
                        entry = self._entry(source, directory.name + "/lesson.md", "completed" if notes.get("status") == "completed" and unchanged else "pending")
                        stored = manifest.get("course", {})
                        course = {key: stored[key] for key in ("term_id", "term_name") if type(stored.get(key)) is str}
                        course.update(course_metadata.get(course_id, {}))
                        term_name = course.get("term_name") or "未识别学期"
                        course_name = course.get("course_name") or manifest.get("course", {}).get("name") or course_id
                        from sjtu_learning_assistant.knowledge_compiler import _safe_name
                        entry.update(course, course_id=course_id, contract=notes.get("contract"),
                                     video_source_id=identity, title=str(manifest.get("video", {}).get("title") or "课堂资料") + ".md",
                                     folder=f"{_safe_name(term_name)}/{_safe_name(course_name)}/课堂字幕")
                        entry["related_course_file_ids"] = sorted({item["course_file_source_id"] for item in metadata.values()
                            if item.get("course_id") == course_id and item.get("course_file_source_id")})
                        documents.append(entry)
                    except (CompiledKnowledgeError, ValueError, TypeError, AttributeError):
                        issues.append("部分课堂资料索引无效，已跳过。")
                if any(doc["source_id"] == source["id"] for doc in documents):
                    sources.append(source)
        except (CompiledKnowledgeError, OSError):
            issues.append("课堂资料目录不可访问。")
        # Untimed native recording-ASR notes are independently generated raw MD,
        # never transcript VTT/Phase1, summaries or approved knowledge counters.
        try:
            recording_root = _safe(self.recording_asr_root)
            marker = _safe(recording_root, "manifest.json")
            if marker.is_file():
                manifest = _json(marker)
                if manifest.get("version") != 1:
                    raise CompiledKnowledgeError("录播ASR清单版本不支持。")
                jobs_root = _safe(recording_root, "jobs")
                course_metadata = self.transcript_metadata()
                recording_sources = dict()
                for directory in sorted(jobs_root.iterdir()):
                    if len(documents) >= MAX_ENTRIES:
                        truncated = True
                        break
                    try:
                        if directory.is_symlink() or not directory.is_dir():
                            raise CompiledKnowledgeError("录播ASR检查点目录无效。")
                        job = _json(_safe(jobs_root, directory.name + "/job.json"))
                        identity = job.get("source_id")
                        course_id, _ = parse_remote_source_id(identity)
                        course_id = str(course_id)
                        expected_directory = hashlib.sha256(identity.encode()).hexdigest().__getitem__(slice(None, 16))
                        if directory.name != expected_directory or str(job.get("course_id")) != course_id:
                            raise CompiledKnowledgeError("录播ASR身份不一致。")
                        if job.get("state") not in ("completed", "user_edit_protected") or not job.get("markdown_relative"):
                            continue
                        relative = job.get("markdown_relative")
                        if PurePosixPath(relative).parts.__getitem__(0) != "Recordings ASR" or not relative.endswith(".md"):
                            raise CompiledKnowledgeError("录播ASR文档路径无效。")
                        # The backend may pin an earlier authorized output root.
                        # Reading is confined to that dedicated Recording ASR subtree.
                        output_root = Path(job.get("markdown_root") or automatic)
                        authorized_roots = manifest.get("authorized_markdown_roots", list())
                        if (type(authorized_roots) is not list
                                or any(type(value) is not str for value in authorized_roots)):
                            raise CompiledKnowledgeError("录播ASR授权目录清单无效。")
                        # Only the configured root is a legacy fallback. Earlier
                        # roots must be authorized by the native worker manifest.
                        allowed_roots = set(authorized_roots)
                        allowed_roots.add(str(Path(automatic).absolute()))
                        if (not output_root.is_absolute() or output_root.name != "Knowledge Markdown"
                                or str(output_root.absolute()) not in allowed_roots):
                            raise CompiledKnowledgeError("录播ASR输出目录未经授权。")
                        path = _safe(output_root, relative)
                        raw = _read_bytes(path, MAX_DOCUMENT_BYTES)
                        root_key = str(output_root)
                        source = recording_sources.get(root_key)
                        if source is None:
                            source_id = "automatic-recording-asr"
                            if output_root != automatic:
                                source_id += ":" + hashlib.sha256(root_key.encode()).hexdigest().__getitem__(slice(None, 16))
                            source = dict(id=source_id, name="录播原始转写（人工未校对）", kind="automatic", root=str(output_root))
                            recording_sources.update(((root_key, source),))
                        entry = self._entry(source, relative, "completed")
                        course = dict(course_metadata.get(course_id, dict()))
                        from sjtu_learning_assistant.knowledge_compiler import _safe_name
                        term_name = course.get("term_name") or job.get("term_name") or "未识别学期"
                        course_name = course.get("course_name") or job.get("course_name") or course_id
                        entry.update(course, course_id=course_id, course_name=course_name,
                            term_name=term_name, video_source_id=identity, title=str(job.get("title") or "录播原文") + ".md",
                            folder=_safe_name(term_name) + "/" + _safe_name(course_name) + "/录播原始转写",
                            contract="recording-asr-raw-v1", source_type="machine_transcript",
                            review_status="unreviewed", status_label="MD完成/人工未校对",
                            timed=False, timestamps="none", asr_model=job.get("model"),
                            text_sha256=job.get("text_sha256"), text_chars=job.get("text_chars"),
                            content_sha256=hashlib.sha256(raw).hexdigest(),
                            user_modified=hashlib.sha256(raw).hexdigest() != job.get("markdown_sha256"),
                            provenance=dict(source_id=identity, model=job.get("model"), timestamps="none", reviewed=False))
                        documents.append(entry)
                    except (CompiledKnowledgeError, ValueError, TypeError, AttributeError, IndexError, SJTUVideoError):
                        issues.append("部分录播ASR检查点或MD不可访问，已保留原件并跳过。")
                sources.extend(recording_sources.values())
        except (CompiledKnowledgeError, OSError):
            issues.append("录播原始转写目录不可访问，未将未知范围记作零。")
        for source in sources:
            source.update(count=sum(doc.get("source_id") == source.get("id") for doc in documents))
        return sources, documents, list(dict.fromkeys(issues)), truncated

    @staticmethod
    def _entry(source: dict, relative: str, status: str) -> dict:
        key = hashlib.sha256((source["id"] + "\0" + relative).encode()).hexdigest()[:32]
        return {"id": key, "source_id": source["id"], "source_name": source["name"], "relative_path": relative,
                "title": Path(relative).name, "folder": PurePosixPath(relative).parent.as_posix(),
                "status": status if status in {"completed", "pending", "failed", "compiled"} else "pending"}

    def sources(self) -> dict[str, Any]:
        sources, documents, issues, truncated = self._catalog()
        return {"items": sources, "count": len(documents), "issues": issues, "truncated": truncated}

    def wiki(self, **arguments) -> dict[str, Any]:
        from sjtu_learning_assistant.knowledge_wiki import CourseWiki
        return CourseWiki(self).browse(**arguments)

    def _text(self, source: dict, document: dict) -> str:
        try:
            return _read_bytes(_safe(Path(source["root"]), document["relative_path"]), MAX_DOCUMENT_BYTES).decode("utf-8-sig")
        except UnicodeError:
            raise CompiledKnowledgeError("知识文件不是 UTF-8 Markdown。") from None

    def search(self, query: object = "", source_id: object = "", offset: object = 0, limit: object = 20, folder: object = "", term_id: object = "", course_id: object = "") -> dict:
        from sjtu_learning_assistant.course_scope import validate_scope
        try:
            validate_scope(term_id, [course_id] if course_id else None)
            if type(course_id) is not str:
                raise ValueError()
        except ValueError:
            raise CompiledKnowledgeError("知识课程范围无效。") from None
        if type(query) is not str or len(query) > 160 or "\x00" in query or type(source_id) is not str or len(source_id) > 128:
            raise CompiledKnowledgeError("知识搜索参数无效。")
        if type(folder) is not str or len(folder) > 1024 or "\\" in folder or "\x00" in folder or PurePosixPath(folder).is_absolute() or ".." in PurePosixPath(folder).parts:
            raise CompiledKnowledgeError("知识文件夹参数无效。")
        offset = _integer(offset, "offset", 0, MAX_ENTRIES)
        limit = _integer(limit, "limit", 1, 50)
        sources, documents, issues, truncated = self._catalog()
        by_id = {source["id"]: source for source in sources}
        from sjtu_learning_assistant.knowledge_navigation import navigation_scopes
        navigation = navigation_scopes(documents)
        virtual_scope = source_id in set(navigation.values())
        if source_id and source_id not in by_id and not virtual_scope:
            raise CompiledKnowledgeError("知识来源已失效，请刷新结果。")
        matches, read_bytes = [], 0
        needle = query.strip().lower()
        terms = tuple(dict.fromkeys(term for term in re.split(r"[\s,，、;；|]+", query.strip()) if term))
        if needle and not terms:
            raise CompiledKnowledgeError("搜索关键词不能为空或只有分隔符。")
        if len(terms) > 10:
            raise CompiledKnowledgeError("单次最多检索10个关键词，请缩小范围。")
        for doc in documents:
            if source_id and (navigation.get(doc["id"]) != source_id if virtual_scope else doc["source_id"] != source_id):
                continue
            if (term_id and doc.get("term_id") != term_id) or (course_id and doc.get("course_id") != course_id):
                continue
            hit, snippet, passages, match_count = 0, "", [], 0
            if needle:
                try:
                    text = self._text(by_id[doc["source_id"]], doc)
                    read_bytes += len(text.encode())
                    if read_bytes > MAX_SEARCH_BYTES:
                        truncated = True
                        issues.append("搜索读取达到上限，请按知识来源缩小范围。")
                        break
                    passages, match_count = _match_passages(text, terms)
                    hit = passages[0]["match_offset"] if passages else -1
                    if hit < 0 and not any(term.lower() in (doc["relative_path"] + doc["source_name"]).lower() for term in terms):
                        continue
                    hit = max(0, hit)
                    snippet = text[max(0, hit - 80):hit + 220]
                except CompiledKnowledgeError as exc:
                    issues.append(str(exc))
                    continue
            matches.append({**doc, "match_offset": hit, "snippet": snippet,
                            "match_kind": "body" if passages else ("metadata" if needle else "catalog"),
                            "match_count": match_count, "passages": passages})
        end = offset + limit
        # 文件夹目录来自全部匹配结果，不能只按当前 20 份分页构造课程树。
        folders: dict[tuple[str, str], dict] = {}
        for doc in matches:
            parts = PurePosixPath(doc["folder"]).parts
            for index in range(1, len(parts) + 1):
                prefix = PurePosixPath(*parts[:index]).as_posix()
                # 显式物理来源过滤仍只浏览该来源；全库／统一目录才跨来源。
                scope = navigation.get(doc["id"], doc["source_id"]) if not source_id or virtual_scope else doc["source_id"]
                key = (scope, prefix)
                summary = folders.setdefault(key, {"source_id": scope, "path": prefix, "name": parts[index - 1], "count": 0})
                summary["count"] += 1
        if folder:
            matches = [doc for doc in matches if doc["folder"] == folder or doc["folder"].startswith(folder + "/")]
        return {"items": matches[offset:end], "count": len(matches), "next_offset": end if end < len(matches) else None,
                "folders": sorted(folders.values(), key=lambda entry: (entry["source_id"], entry["path"])),
                "issues": list(dict.fromkeys(issues)), "truncated": truncated,
                "search_scope": "markdown_body_and_metadata" if needle else "catalog",
                "query_terms": list(terms), "query_mode": "any_literal_term",
                "coverage_note": "仅检索本地可读 MD；未编译、未下载或超出预算的原课件不在本次正文搜索范围内。"}

    def _resolve(self, document_id: object) -> tuple[dict, dict]:
        if type(document_id) is not str or re.fullmatch(r"[0-9a-f]{32}", document_id) is None:
            raise CompiledKnowledgeError("知识文档标识无效。")
        sources, documents, _, _ = self._catalog()
        document = next((item for item in documents if item["id"] == document_id), None)
        if document is None:
            raise CompiledKnowledgeError("知识文档已失效，请刷新结果。")
        return next(item for item in sources if item["id"] == document["source_id"]), document

    def compilation_status(self) -> dict:
        """只读实际清单计数，未进入可读目录的失败/待处理课件也计入。"""
        sources, documents, issues, truncated = self._catalog()
        result = {"catalog_documents": len(documents), "catalog_complete": not issues and not truncated,
                  "sources": len(sources), "issues": sorted(set(issues)), "truncated": truncated,
                  "course_files": None,
                  "classroom_documents": sum(doc["source_id"] == "automatic-transcripts" for doc in documents)}
        try:
            archive = Path(self.settings().archive_root)
            state = _json(_safe(archive / "Knowledge Markdown", "99_System/Course-Sync.json"))
            if state.get("version") != 1 or state.get("source_root") != str(archive) or type(state.get("items")) is not dict:
                raise CompiledKnowledgeError("自动课件索引格式无效。")
            counts = {"total": len(state["items"]), "text_ready": 0, "summarized": 0, "failed": 0, "pending": 0, "other": 0}
            for item in state["items"].values():
                if type(item) is not dict:
                    counts["other"] += 1
                    continue
                counts["text_ready"] += item.get("text_ready") is True
                complete = item.get("status") == "completed" and item.get("contract") == "course-summary-v1"
                counts["summarized"] += complete
                if not complete:
                    counts["failed" if item.get("status") == "failed" else "pending"] += 1
            result["course_files"] = counts
        except CompiledKnowledgeError:
            result["catalog_complete"] = False
            result["issues"] = sorted(set([*result["issues"], "课件进度清单不可用，未将未知进度记作零。"]))
        return result

    def read(self, document_id: object, offset: object = 0, max_chars: object = 12000) -> dict:
        offset = _integer(offset, "offset", 0, MAX_DOCUMENT_BYTES)
        max_chars = _integer(max_chars, "max_chars", 500, 16000)
        source, document = self._resolve(document_id)
        text = self._text(source, document)
        end = min(len(text), offset + max_chars)
        # 保留正文的原始换行与字符偏移，仅从展示标题中去除 CRLF 的回车。
        headings = re.findall(r"^#{1,6} [^\r\n]+", text[:offset], flags=re.MULTILINE)
        return {**document, "text": text[offset:end], "offset": offset, "next_offset": end if end < len(text) else None,
                "read_status": "ready", "compilation_status": document["status"],
                "read_note": "本次正文已成功读取。编译状态failed/pending不等于读取失败；next_offset仅表示后面还有正文，局部问题不必从头通读。旧稿数值以原课件对应页为准。",
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "total_chars": len(text), "section": headings[-1][:200] if headings else "文档开头",
                "citation": document["source_name"] + "/" + document["relative_path"], "truncated": end < len(text)}

    def open_path(self, source_id: object, document_id: object = "") -> Path:
        if document_id:
            source, document = self._resolve(document_id)
            if source.get("id") != source_id:
                raise CompiledKnowledgeError("文档不属于此知识来源。")
            return _safe(Path(source.get("root")), document.get("relative_path"))
        sources, _, _, _ = self._catalog()
        source = next((item for item in sources if item.get("id") == source_id), None)
        if source is None:
            raise CompiledKnowledgeError("知识来源已失效。")
        if str(source.get("id") or "").startswith("automatic-recording-asr"):
            return _safe(Path(source.get("root")), "Recordings ASR")
        return _safe(Path(source.get("root")))
