"""本地课件逐页多模态编译；仅写入自身生成且未被人工修改的文件。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from sjtu_learning_assistant.document_text import (
    DocumentTextError, DocumentUnsupportedError, extract_document,
    _open_ooxml, _xml, _Budget,
)
from sjtu_learning_assistant.knowledge_compiler import (
    KnowledgeCompilerError, KnowledgeAPIError, _atomic_write, _safe_name,
)
from sjtu_learning_assistant.knowledge_prompts import PAGE_PROMPT, LECTURE_PROMPT, lecture_markdown, page_markdown

VISION_MODEL = "qwen3.8-27b"
CONTRACT = "course-lecture-v2"
MAX_PAGES = 500
MAX_COMPILED_TEXT_CHARS = MAX_PAGES * 12_000
MAX_SOURCE_BYTES = 100 * 1024**2
MAX_MANIFEST_BYTES = 2 * 1024**2


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_destination(root: Path, path: Path) -> Path:
    if not path.is_relative_to(root):
        raise KnowledgeCompilerError("编译输出路径越界。")
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise KnowledgeCompilerError("编译输出不能包含符号链接。")
    if not path.resolve().is_relative_to(root.resolve()):
        raise KnowledgeCompilerError("编译输出路径越界。")
    return path


@contextmanager
def target_lock(target: Path) -> Iterator[None]:
    """跨进程锁在崩溃后由 OS 释放；不删除锁文件，避免 inode 竞争。"""
    path = safe_destination(target, target / "99_System" / "Document-Compiler.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise KnowledgeCompilerError("另一个进程正在编译此输出目录。") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _office() -> str:
    executable = shutil.which("soffice")
    if not executable and os.name != "nt":
        candidate = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        if candidate.is_file():
            executable = str(candidate)
    if not executable and os.name == "nt":
        for directory in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if directory:
                candidate = Path(directory) / "LibreOffice" / "program" / "soffice.exe"
                if candidate.is_file():
                    executable = str(candidate)
                    break
    if not executable:
        raise DocumentUnsupportedError("PPTX 整页渲染需要 LibreOffice；请安装或先导出 PDF。")
    return executable


def _validate_renderable_pptx(path: Path) -> None:
    """不打开宏、OLE、嵌入包或外部资源；超链接不随渲染访问。"""
    archive, names = _open_ooxml(path)
    try:
        budget = _Budget(400_000)
        for name in names:
            if name.casefold().startswith("ppt/embeddings/"):
                raise DocumentUnsupportedError("含嵌入对象的 PPTX 请先安全导出 PDF。")
            if name.endswith(".rels"):
                root = _xml(archive, name, budget)
                for relationship in root:
                    if relationship.get("TargetMode", "").casefold() == "external" and not relationship.get("Type", "").endswith("/hyperlink"):
                        raise DocumentUnsupportedError("含外部资源的 PPTX 请先安全导出 PDF。")
    finally:
        archive.close()


@contextmanager
def rendered_pdf(path: Path, cancel: threading.Event) -> Iterator[Path]:
    if path.suffix.casefold() == ".pdf":
        yield path
        return
    _validate_renderable_pptx(path)
    with tempfile.TemporaryDirectory(prefix="sjtu-slide-render-") as temporary:
        directory = Path(temporary)
        profile = directory / "profile"
        profile.mkdir()
        # 禁止宏和自动更新外链，隔离用户 Office profile 与同时运行的实例。
        (profile / "user").mkdir()
        (profile / "user" / "registrymodifications.xcu").write_text(
            '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
            '<item oor:path="/org.openoffice.Office.Common/Load"><prop oor:name="UpdateDocMode" oor:op="fuse"><value>0</value></prop></item>'
            '</oor:items>', encoding="utf-8",
        )
        process = subprocess.Popen(
            [_office(), "-env:UserInstallation=" + profile.as_uri(), "--headless", "--nologo", "--nodefault", "--norestore", "--convert-to", 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}', "--outdir", str(directory), str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            import time
            deadline = time.monotonic() + 120
            while process.poll() is None:
                if cancel.wait(0.1) or time.monotonic() >= deadline:
                    raise DocumentTextError("课件渲染已取消或超过 120 秒。")
            pdf = directory / (path.stem + ".pdf")
            if process.returncode or not pdf.is_file() or pdf.stat().st_size > MAX_SOURCE_BYTES:
                raise DocumentTextError("课件整页渲染失败或超过大小限制。")
            yield pdf
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def page_images(pdf: Path) -> Iterator[tuple[int, bytes]]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise DocumentUnsupportedError("缺少 pypdfium2 整页渲染依赖。") from None
    with pdfium.PdfDocument(pdf) as document:
        if not 1 <= len(document) <= MAX_PAGES:
            raise DocumentTextError("课件页数必须在 1—500 页内。")
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
                if not 0 < width <= 100_000 or not 0 < height <= 100_000:
                    raise DocumentTextError("课件页面尺寸无效。")
                bitmap = page.render(scale=min(2, 2048 / max(width, height)))
                try:
                    image = bitmap.to_pil().convert("RGB")
                    output = io.BytesIO()
                    image.save(output, "JPEG", quality=88, optimize=True)
                    data = output.getvalue()
                    image.close()
                    if len(data) > 4 * 1024**2:
                        raise DocumentTextError("课件页面图片超过 4 MiB。")
                    yield index + 1, data
                finally:
                    bitmap.close()
            finally:
                page.close()


def vision_page_url(jpeg: bytes) -> str:
    """向视觉接口发送规范 PNG，避免个别 JPEG 页图在上游解码/推理阶段停滞。"""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(jpeg)) as image:
            if image.width > 2048 or image.height > 2048 or image.width < 1 or image.height < 1:
                raise DocumentTextError("课件页图尺寸超出视觉输入上限。")
            with image.convert("RGB") as normalized:
                buffer = io.BytesIO()
                normalized.save(buffer, format="PNG")
                png = buffer.getvalue()
        if len(png) > 4 * 1024**2:
            raise DocumentTextError("规范化课件页图超过 4 MiB，未发送到接口。")
    except DocumentTextError:
        raise
    except Exception:
        raise DocumentTextError("课件页图无法规范化，未发送到接口。") from None
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class DocumentCompiler:
    def __init__(self, runner: Callable[[str, str, Any], str], cancel: threading.Event, *, max_calls: int = 100, renderer: Callable = page_images):
        if type(max_calls) is not int or not 1 <= max_calls <= 10_000:
            raise ValueError("max_calls 必须在 1—10000 内")
        self.runner, self.cancel, self.renderer = runner, cancel, renderer
        self.max_calls, self.calls, self.cached_pages = max_calls, 0, 0

    def _request(self, model: str, system: str, content: Any) -> str:
        from sjtu_learning_assistant.ai_classifier import AIClassificationError
        for attempt in range(2):
            if self.cancel.is_set():
                raise DocumentTextError("课件编译已取消。")
            if self.calls >= self.max_calls:
                raise DocumentTextError("已达到本次 API 调用上限；已完成页面保留，下次增量续编。")
            self.calls += 1
            try:
                return self.runner(model, system, content)
            except (AIClassificationError, KnowledgeAPIError) as exc:
                if attempt or exc.category not in {"timeout", "network", "upstream_service", "rate_limit_or_quota"}:
                    raise KnowledgeCompilerError(str(exc)) from None
                if self.cancel.wait(12):
                    raise DocumentTextError("课件编译已取消。")
        raise DocumentTextError("课件接口不可用；已完成页面保留。")

    def compile(self, source: Path, path: Path, target: Path) -> Path:
        relative = path.relative_to(source).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(source) or path.stat().st_size > MAX_SOURCE_BYTES:
            raise DocumentTextError("课件路径无效或超过 100 MiB。")
        data = path.read_bytes()
        source_hash = digest(data)
        # 同一次编译的文本和页图必须来自同一只读快照；不执行原文件宏。
        with tempfile.TemporaryDirectory(prefix="sjtu-course-snapshot-") as temporary:
            snapshot = Path(temporary) / path.name
            snapshot.write_bytes(data)
            try:
                return self._compile_snapshot(source, path, snapshot, target, source_hash)
            except (DocumentTextError, KnowledgeCompilerError):
                raise
            except Exception as exc:
                raise DocumentTextError(f"课件处理失败（{type(exc).__name__}）；已完成页面保留。") from None

    def _compile_snapshot(self, source: Path, original: Path, path: Path, target: Path, source_hash: str) -> Path:
        relative = original.relative_to(source).as_posix()
        source_id = digest(relative.encode())[:16]
        revision = digest((source_hash + CONTRACT + VISION_MODEL).encode())[:16]
        directory = safe_destination(target, target / "90_Inbox" / "Documents" / source_id / revision)
        manifest_path = directory / "manifest.json"
        state: dict[str, Any] = {"version": 1, "contract": CONTRACT, "model": VISION_MODEL, "source": relative, "source_hash": source_hash, "pages": {}, "outputs": {}}
        if manifest_path.exists():
            safe_destination(target, manifest_path)
            if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
                raise DocumentTextError("课件编译清单超过上限。")
            try:
                previous = json.loads(manifest_path.read_text(encoding="utf-8"))
                if any(previous.get(key) != state[key] for key in ("version", "contract", "model", "source", "source_hash")) or type(previous.get("pages")) is not dict or type(previous.get("outputs")) is not dict:
                    raise ValueError()
                state = previous
            except (ValueError, AttributeError, RecursionError):
                raise DocumentTextError("课件编译清单损坏，未覆盖现有输出。") from None
        directory.mkdir(parents=True, exist_ok=True)

        def persist():
            _atomic_write(safe_destination(target, manifest_path), json.dumps(state, ensure_ascii=False, indent=2) + "\n")

        def recover():
            pending = state.get("pending")
            if pending is None:
                return
            if not isinstance(pending, dict) or not isinstance(pending.get("files"), list) or not 1 <= len(pending["files"]) <= 2:
                raise DocumentTextError("待提交记录损坏，未覆盖输出。")
            for item in pending["files"]:
                if not isinstance(item, dict):
                    raise DocumentTextError("待提交记录损坏。")
                name, stage, expected = item.get("name"), item.get("stage"), item.get("sha256")
                if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts or not isinstance(stage, str) or not stage.startswith(".stage-") or Path(stage).name != stage or not isinstance(expected, str):
                    raise DocumentTextError("待提交路径无效，未覆盖输出。")
                destination = safe_destination(target, directory / name)
                temporary = safe_destination(target, directory / stage)
                actual = digest(destination.read_bytes()) if destination.is_file() and destination.stat().st_size <= MAX_SOURCE_BYTES else None
                if actual != expected:
                    if (destination.exists() and actual != item.get("previous")) or not temporary.is_file() or temporary.stat().st_size > MAX_SOURCE_BYTES or digest(temporary.read_bytes()) != expected:
                        raise DocumentTextError("待提交输出冲突或损坏，已保留现有文件。")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(temporary, destination)
                state["outputs"][name] = expected
                temporary.unlink(missing_ok=True)
            checkpoint = pending.get("page")
            if checkpoint is not None:
                if not isinstance(checkpoint, dict) or type(checkpoint.get("number")) is not int or not 1 <= checkpoint["number"] <= MAX_PAGES:
                    raise DocumentTextError("待提交页码无效。")
                page_name = f"pages/page-{checkpoint['number']:04d}.md"
                if page_name not in state["outputs"]:
                    raise DocumentTextError("待提交页面缺失。")
                state["pages"][str(checkpoint["number"])] = page_name
            del state["pending"]
            persist()

        # 先恢复已登记的事务，再检查历史输出哈希，避免原子替换窗口阻塞续编。
        recover()
        # 检查所有历史输出，人工编辑过的文件不能被自动同步覆盖。
        if len(state["outputs"]) > MAX_PAGES * 3 + 1 or len(state["pages"]) > MAX_PAGES:
            raise DocumentTextError("课件编译清单超过上限。")
        for name, expected in state["outputs"].items():
            if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts or not isinstance(expected, str):
                raise DocumentTextError("课件编译清单路径无效。")
            candidate = safe_destination(target, directory / name)
            if not candidate.is_file() or candidate.stat().st_size > MAX_SOURCE_BYTES or digest(candidate.read_bytes()) != expected:
                raise DocumentTextError("生成文件已被修改或丢失，已保留并停止此课件自动更新。")
        def commit(contents: dict[str, str | bytes], number: int | None = None):
            items = []
            try:
                for name, content in contents.items():
                    destination = safe_destination(target, directory / name)
                    payload = content.encode("utf-8") if isinstance(content, str) else content
                    expected = state["outputs"].get(name)
                    if destination.exists() and not expected:
                        raise DocumentTextError("输出已有未登记文件，未覆盖。")
                    if expected and (not destination.is_file() or digest(destination.read_bytes()) != expected):
                        raise DocumentTextError("生成文件已被修改，未覆盖。")
                    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".stage-")
                    items.append({"name": name, "stage": Path(temporary).name, "sha256": digest(payload), "previous": expected})
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                state["pending"] = {"files": items, "page": {"number": number} if number else None}
                try:
                    persist()  # 写前日志先落盘，崩溃后可离线提交，无需再调用模型。
                except Exception:
                    del state["pending"]
                    raise
                recover()
            finally:
                if "pending" not in state:
                    for item in items:
                        (directory / item["stage"]).unlink(missing_ok=True)

        # 编译器逐页有 12000 字符输入闸门；总量按 500 页独立限额，
        # 不沿用聊天预览的 400k 上限截断完整教材。
        document = extract_document(path, path.name, max_chars=MAX_COMPILED_TEXT_CHARS)
        if document.truncated:
            raise DocumentTextError("正文提取达到上限，未将不完整课件标为完成。")
        # 导出明确包含隐藏页，最终仍逐页验证数量，不跳过隐藏知识或错配页码。
        persist()
        notes = []
        with rendered_pdf(path, self.cancel) as pdf:
            for number, jpeg in self.renderer(pdf):
                if self.cancel.is_set():
                    raise DocumentTextError("课件编译已取消。")
                if number > len(document.units):
                    raise DocumentTextError("渲染页数与原文页数不一致。")
                key, page_name = str(number), f"pages/page-{number:04d}.md"
                if key in state["pages"]:
                    if state["pages"][key] != page_name or page_name not in state["outputs"]:
                        raise DocumentTextError("课件页面检查点无效。")
                    self.cached_pages += 1
                    note = (directory / page_name).read_text(encoding="utf-8")
                else:
                    if self.calls >= self.max_calls:
                        raise DocumentTextError("已达到本次 API 调用上限；已完成页面保留，下次增量续编。")
                    native = document.units[number - 1]
                    if len(native) > 12_000:
                        raise DocumentTextError("单页正文过长，请拆分课件。")
                    response = self._request(VISION_MODEL, PAGE_PROMPT,
                        [{"type": "text", "text": f"第 {number} 页；请结合整页图像与原始文字进行知识编译。\n<source>\n{native}\n</source>"},
                         {"type": "image_url", "image_url": {"url": vision_page_url(jpeg)}}])
                    if self.cancel.is_set():
                        raise DocumentTextError("课件编译已取消。")
                    try:
                        response = page_markdown(response)
                    except ValueError as exc:
                        raise DocumentTextError(str(exc)) from None
                    if self.cancel.is_set():
                        raise DocumentTextError("课件编译已取消。")
                    note = ("---\n" + f"type: slide\nsource: {json.dumps(relative, ensure_ascii=False)}\nsource_sha256: {source_hash}\npage: {number}\nmodel: {VISION_MODEL}\ncontract: {CONTRACT}\n" + "---\n\n"
                            + f"# 第 {number} 页\n\n![原始页面](../assets/page-{number:04d}.jpg)\n\n## 原始提取\n\n"
                            + native + "\n\n## 逐页知识解读\n\n" + response.strip() + "\n")
                    commit({f"assets/page-{number:04d}.jpg": jpeg, page_name: note}, number)
                notes.append(note.replace("(../assets/", "(assets/"))
        if len(notes) != document.unit_count:
            raise DocumentTextError("渲染页数与原文页数不一致，未生成完成标记。")
        # 知识稿与原文层分离；逐组覆盖所有页，不把首段摘要冒充完整课件。
        groups: list[list[dict[str, Any]]] = []
        group: list[dict[str, Any]] = []
        for number, note in enumerate(notes, 1):
            interpreted = note.split("## 逐页知识解读\n\n", 1)[-1]
            entry = {"page": number, "text": interpreted}
            if group and (len(group) >= 4 or len(json.dumps(group + [entry], ensure_ascii=False)) > 12_000):
                groups.append(group)
                group = []
            group.append(entry)
        if group:
            groups.append(group)
        lectures = []
        for group in groups:
            if self.cancel.is_set():
                raise DocumentTextError("课件编译已取消。")
            pages = [entry["page"] for entry in group]
            name = f"learning/pages-{pages[0]:04d}-{pages[-1]:04d}.md"
            if name in state["outputs"]:
                lecture = (directory / name).read_text(encoding="utf-8")
            else:
                if self.calls >= self.max_calls:
                    raise DocumentTextError("已达到本次 API 调用上限；已完成页面保留，下次增量续编。")
                response = self._request("deepseek-chat", LECTURE_PROMPT, json.dumps(group, ensure_ascii=False))
                if self.cancel.is_set():
                    raise DocumentTextError("课件编译已取消。")
                try:
                    lecture = lecture_markdown(response, pages)
                except (ValueError, TypeError, AttributeError) as exc:
                    raise DocumentTextError(str(exc) if type(exc) is ValueError else "讲义响应无效；已保留页面。") from None
                commit({name: lecture})
            lectures.append((pages, lecture))
        if digest(original.read_bytes()) != source_hash:
            raise DocumentTextError("编译期间素材已变化；已完成旧版本页面保留，请重试。")
        topics = []
        for pages, lecture in lectures:
            page_note = notes[pages[0] - 1].split("## 逐页知识解读\n\n", 1)[-1]
            theme = page_note.split("## 页面主题", 1)[-1].split("## 知识点", 1)[0].strip()
            title = _safe_name(theme.splitlines()[0] if theme else "知识段落")
            thread = lecture.split("## 讲义脉络", 1)[-1].split("## 知识点", 1)[0].strip()
            topics.append((pages, title, thread))
        body = ("---\n" + f"type: course-document\nsource: {json.dumps(relative, ensure_ascii=False)}\nsource_type: {path.suffix.casefold()[1:]}\nsource_id: {source_id}\nsource_sha256: {source_hash}\nconverted_by: libreoffice-pdfium-qwen\nmodel: {VISION_MODEL}\ncontract: {CONTRACT}\n" + "---\n\n"
                + f"# {_safe_name(path.stem)}\n\n来源：`{relative}`\n\nSHA-256：`{source_hash}`\n\n"
                + f"编译覆盖：{len(notes)}/{document.unit_count} 页 · 图文解读与讲义结构检查通过。\n"
                + "AI 整理不是教师原稿；具体待确认项列在对应知识段落中。\n\n## 本讲导航\n\n"
                + "\n".join(f"- [{title} · 第 {pages[0]}—{pages[-1]} 页](#讲义-{pages[0]}-{pages[-1]})" for pages, title, _ in topics)
                + "\n\n## 全讲脉络\n\n" + "\n\n".join(f"### {title}（第 {pages[0]}—{pages[-1]} 页）\n\n{thread}" for pages, title, thread in topics)
                + "\n\n" + "\n\n".join(f"# 讲义 {pages[0]}–{pages[-1]}\n\n{lecture}" for pages, lecture in lectures)
                + "\n\n# 原文与逐页证据\n\n"
                + "\n\n".join("<details>\n<summary>第 " + str(number) + " 页原文与图文解读</summary>\n\n" + note.split("---\n", 2)[-1] + "\n</details>" for number, note in enumerate(notes, 1)))
        commit({"document.md": body})
        state["status"] = "completed"
        state["page_count"] = len(notes)
        persist()
        return directory / "document.md"
