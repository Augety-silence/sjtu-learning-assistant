"""App 生命周期内增量同步已下载课件；来源文件和人工笔记永不覆盖。"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from sjtu_learning_assistant.document_text import DocumentTextError, extract_document
from sjtu_learning_assistant.knowledge_compiler import KnowledgeCompilerError, _atomic_write
from sjtu_learning_assistant.knowledge_documents import (
    CONTRACT, MAX_COMPILED_TEXT_CHARS, MAX_MANIFEST_BYTES, MAX_SOURCE_BYTES, VISION_MODEL,
    DocumentCompiler, digest, safe_destination, target_lock,
)
from sjtu_learning_assistant.local_settings import LocalSettings, validate_archive_root

OUTPUT_FOLDER = "Knowledge Markdown"


class CourseMarkdownSync:
    def __init__(self, settings: Callable[[], LocalSettings], paths: Callable[[], list[Path]],
                 runner: Callable, *, interval: float = 60,
                 compiler_factory: Callable = DocumentCompiler,
                 today: Callable = lambda: datetime.now().date().isoformat()):
        self.settings, self.paths, self.runner = settings, paths, runner
        self.interval, self.compiler_factory, self.today = interval, compiler_factory, today
        self._stop, self._wake, self._cancel = threading.Event(), threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._guard = threading.RLock()
        self._status: dict[str, Any] = {"status": "disabled", "total": 0, "completed": 0,
                                      "failed": 0, "pending": 0, "calls_today": 0, "issues": []}

    def start(self) -> None:
        with self._guard:
            if self._stop.is_set() or self._thread is not None:
                return
            self._thread = threading.Thread(target=self._loop, name="sjtu-course-markdown", daemon=True)
            self._thread.start()

    def notify(self, *, settings_changed: bool = False) -> None:
        if settings_changed:
            self._cancel.set()
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._cancel.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def status(self) -> dict[str, Any]:
        with self._guard:
            return {**self._status, "issues": list(self._status["issues"])}

    def _set(self, **values) -> None:
        with self._guard:
            self._status.update(values)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            self.run_once()
            self._wake.wait(self.interval)

    def run_once(self) -> None:
        """只由单一 worker 调用；测试可同步调用，不需要启动 UI。"""
        try:
            settings = self.settings()
            if self._stop.is_set() or not settings.course_markdown_enabled:
                self._set(status="disabled")
                return
            if not settings.ai_enabled or not settings.ai_key_saved:
                self._set(status="needs_ai")
                return
            source = Path(validate_archive_root(settings.archive_root))
            if not source.is_dir():
                raise KnowledgeCompilerError("课件归档目录不可用。")
            target = safe_destination(source, source / OUTPUT_FOLDER)
            marker = safe_destination(source, target / "99_System" / "Course-Sync.json")
            if target.exists() and not marker.is_file() and any(target.iterdir()):
                system = safe_destination(source, target / "99_System")
                lock = safe_destination(source, system / "Document-Compiler.lock")
                # 首次加锁后、清单落盘前中断只会留下这个确定的脚手架。
                skeleton = set(target.iterdir()) == {system} and system.is_dir() and set(system.iterdir()) == {lock} and lock.is_file() and lock.read_bytes() == b"0"
                if not skeleton:
                    raise KnowledgeCompilerError("自动输出目录已有非本功能文件，未写入。")
            target.mkdir(exist_ok=True)
            with target_lock(target):
                self._cancel = threading.Event()
                if self._stop.is_set():
                    self._cancel.set()
                    return
                self._cycle(source, target, marker, settings)
        except (KnowledgeCompilerError, DocumentTextError) as exc:
            self._set(status="blocked", issues=[str(exc)])
        except Exception:
            self._set(status="blocked", issues=["自动课件同步暂不可用；请检查配置、磁盘或数据库。"])

    def _cycle(self, source: Path, target: Path, marker: Path, settings: LocalSettings) -> None:
        state: dict[str, Any] = {"version": 1, "source_root": str(source), "items": {},
                                "quota": {"day": self.today(), "used": 0}}
        if marker.exists():
            if marker.stat().st_size > MAX_MANIFEST_BYTES:
                raise KnowledgeCompilerError("课件同步清单超过上限，未覆盖。")
            try:
                previous = json.loads(marker.read_text(encoding="utf-8"))
                quota = previous["quota"]
                if previous["version"] != 1 or previous["source_root"] != str(source) or type(previous["items"]) is not dict or type(quota["used"]) is not int or quota["used"] < 0 or type(quota["day"]) is not str or any(type(item) is not dict for item in previous["items"].values()):
                    raise ValueError()
                state = previous
            except (ValueError, KeyError, TypeError, RecursionError):
                raise KnowledgeCompilerError("课件同步清单损坏，未覆盖输出。") from None
        quota = state["quota"]
        if quota["day"] != self.today():
            quota.update(day=self.today(), used=0)

        def persist():
            payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
            if len(payload.encode()) > MAX_MANIFEST_BYTES:
                raise KnowledgeCompilerError("课件同步清单超过上限。")
            _atomic_write(safe_destination(target, marker), payload)

        def call(model, system, content):
            current = self.settings()
            if self._stop.is_set() or self._cancel.is_set() or not current.course_markdown_enabled or not current.ai_enabled or not current.ai_key_saved or current.archive_root != settings.archive_root or current.ai_base_url != settings.ai_base_url:
                raise DocumentTextError("课件同步已暂停或配置已变更。")
            if quota["day"] != self.today():
                quota.update(day=self.today(), used=0)
            if quota["used"] >= current.course_markdown_daily_calls:
                raise DocumentTextError("今日调用额度已用完，次日自动续编。")
            # 先保留额度再请求；崩溃/失败也消耗额度，不以重启绕过上限。
            quota["used"] += 1
            persist()
            self._set(calls_today=quota["used"])
            return self.runner(model, system, content)

        def publish(item, mirror, body):
            allowed = {item.get("mirror_hash"), item.get("pending_hash")} - {None}
            payload_hash = digest(body.encode())
            item["pending_hash"] = payload_hash
            persist()
            mirror.parent.mkdir(parents=True, exist_ok=True)
            if mirror.exists() and (not mirror.is_file() or digest(mirror.read_bytes()) not in allowed | {payload_hash}):
                raise DocumentTextError("生成期间对应 MD 已变化，未覆盖。")
            _atomic_write(safe_destination(target, mirror), body)
            item.update(mirror_hash=payload_hash)
            item.pop("pending_hash", None)
            persist()

        persist()
        compiler = self.compiler_factory(call, self._cancel, max_calls=settings.course_markdown_daily_calls)
        candidates = sorted(set(self.paths()), key=lambda path: str(path).casefold())
        candidates = [path for path in candidates if path.suffix.casefold() in {".pptx", ".pdf", ".ppt"}]
        completed, failed, pending, text_ready = 0, 0, 0, 0
        issues = []
        self._set(status="running", total=len(candidates), completed=0, failed=0, pending=0,
                  calls_today=quota["used"], output_root=str(target), text_ready=0, issues=[])
        prepared = []
        # 第一阶段对所有课件先落地本地全文 MD；视觉额度不会使后面的文件完全无 MD。
        for path in candidates:
            if self._stop.is_set() or self._cancel.is_set():
                break
            relative = None
            try:
                safe_destination(source, path)
                if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
                    raise DocumentTextError("来源不存在或超过单文件 100 MiB 上限。")
                relative = path.relative_to(source).as_posix()
                if path.is_relative_to(target):
                    raise DocumentTextError("自动输出不能再次作为来源。")
                source_hash = digest(path.read_bytes())
                fingerprint = digest((source_hash + CONTRACT + VISION_MODEL).encode())
                item = state["items"].setdefault(relative, {})
                mirror = safe_destination(target, target / "Courses" / Path(relative + ".md"))
                allowed = {item.get("mirror_hash"), item.get("pending_hash")} - {None}
                if mirror.exists() and (not mirror.is_file() or digest(mirror.read_bytes()) not in allowed):
                    raise DocumentTextError("对应 MD 为未登记文件或已被人工修改，未覆盖。")
                if item.get("mirror_hash") and not mirror.exists():
                    raise DocumentTextError("对应 MD 已被删除，自动更新暂停。")
                if item.get("fingerprint") != fingerprint or not item.get("text_ready"):
                    extracted = extract_document(path, path.name, max_chars=MAX_COMPILED_TEXT_CHARS)
                    if extracted.truncated:
                        raise DocumentTextError("正文提取达到上限，未把不完整课件标为完成。")
                    if digest(path.read_bytes()) != source_hash:
                        raise DocumentTextError("提取期间课件已变化，请等待下次同步。")
                    body = ("---\n" + f"type: course-document\nsource: {json.dumps(relative, ensure_ascii=False)}\nsource_type: {path.suffix.casefold()[1:]}\nsource_sha256: {source_hash}\nconverted_by: local-text\nmultimodal_status: pending\n" + "---\n\n"
                            + f"# {path.stem}\n\n> 已提取本地正文；图像、图表与公式的多模态复核待自动续编，不视为视觉编译完成。\n\n"
                            + "\n\n".join(f"## 第 {number} 页\n\n{unit}" for number, unit in enumerate(extracted.units, 1)) + "\n")
                    item.update(fingerprint=fingerprint, attempts=0, status="pending")
                    publish(item, mirror, body)
                    item["text_ready"] = True
                    persist()
                text_ready += 1
                prepared.append((path, relative, fingerprint, item, mirror))
            except (DocumentTextError, KnowledgeCompilerError) as exc:
                failed += 1
                issues.append((relative + "：" if relative else "") + str(exc))
                if relative is not None:
                    state["items"].setdefault(relative, {}).update(status="failed", error=str(exc))
                    persist()
            except Exception:
                failed += 1
                issues.append((relative + "：" if relative else "") + "本地正文提取失败；现有文件保留。")
            self._set(text_ready=text_ready, failed=failed, issues=issues[:20])
        for index, (path, relative, fingerprint, item, mirror) in enumerate(prepared):
            if self._stop.is_set() or self._cancel.is_set():
                pending += len(prepared) - index
                break
            try:
                if item.get("fingerprint") == fingerprint and item.get("status") == "completed":
                    if not mirror.is_file() or digest(mirror.read_bytes()) != item.get("mirror_hash"):
                        raise DocumentTextError("对应 MD 已被人工修改或删除，已保留；自动更新暂停。")
                    completed += 1
                    continue
                # 保存失败重试次数，单一坏文件不会无限循环消耗 API。
                attempts = item.get("attempts", 0) if item.get("fingerprint") == fingerprint and item.get("attempt_day") == self.today() else 0
                if attempts >= 3:
                    failed += 1
                    issues.append(relative + "：今日已重试 3 次；次日重试。")
                    continue
                if quota["used"] >= settings.course_markdown_daily_calls:
                    pending += 1
                    continue
                # 已编辑镜像即使源更新也不能调用模型/覆盖。
                allowed_hashes = {item.get("mirror_hash"), item.get("pending_hash")} - {None}
                if mirror.exists() and (not mirror.is_file() or digest(mirror.read_bytes()) not in allowed_hashes):
                    raise DocumentTextError("对应 MD 为未登记文件或已被人工修改，未覆盖。")
                item.update(fingerprint=fingerprint, attempts=attempts + 1, attempt_day=self.today(), status="pending")
                state["items"][relative] = item
                persist()
                note = compiler.compile(source, path, target)
                # 同一文件映射保留扩展名，避免 lecture.pdf / lecture.pptx 重名。
                body = note.read_text(encoding="utf-8")
                asset_prefix = Path(os.path.relpath(note.parent / "assets", mirror.parent)).as_posix()
                for number in range(1, 501):
                    body = body.replace(f"(assets/page-{number:04d}.jpg)", "(" + quote(asset_prefix + f"/page-{number:04d}.jpg", safe="/") + ")")
                publish(item, mirror, body)
                item.update(status="completed", document=note.relative_to(target).as_posix())
                item.pop("error", None)
                persist()
                completed += 1
            except (DocumentTextError, KnowledgeCompilerError) as exc:
                if quota["used"] >= settings.course_markdown_daily_calls:
                    pending += 1
                else:
                    failed += 1
                message = str(exc)
                if relative is not None:
                    state["items"].setdefault(relative, {}).update(status="pending" if quota["used"] >= settings.course_markdown_daily_calls else "failed", error=message)
                issues.append((relative + "：" if relative else "") + message)
                persist()
            except Exception:
                failed += 1
                issues.append((relative + "：" if relative else "") + "处理失败；已有文件保留。")
            finally:
                self._set(completed=completed, failed=failed, pending=pending, issues=issues[:20])
        self._set(status="paused" if self._cancel.is_set() else "budget_paused" if pending and quota["used"] >= settings.course_markdown_daily_calls else "completed_with_warnings" if failed else "completed",
                  completed=completed, failed=failed, pending=pending, calls_today=quota["used"], issues=issues[:20])
