"""AI 工作区：在用户指定的本地目录内只读浏览、读取，并以“先提议、后确认”的方式修改文本文件。

安全边界：
- 只在用户明确选择的工作区根目录内操作；路径必须是相对路径，任一层级都不能是符号链接或隐藏项；
- 应用数据目录、AI 附件受控目录和课程归档目录永远不可写；
- 模型只能“提议”修改；只有用户在界面上确认后才会落盘；
- 只支持新建或修改文本类文件，从不删除、移动或重命名；
- 覆盖前把原文件备份到应用数据目录，并校验文件自提议后未被改动（否则标记冲突）。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from sjtu_learning_assistant.document_text import DOCUMENT_EXTENSIONS

TEXT_EDIT_EXTENSIONS = frozenset(
    {
        ".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".xml", ".html", ".htm", ".css", ".tex",
        ".bib", ".rst", ".py", ".ipynb", ".js", ".ts", ".tsx", ".jsx",
        ".java", ".c", ".h", ".cpp", ".hpp", ".go", ".rs", ".sql", ".sh",
        ".r", ".m", ".log",
    }
)
READABLE_EXTENSIONS = TEXT_EDIT_EXTENSIONS | DOCUMENT_EXTENSIONS
MAX_EDIT_SOURCE_BYTES = 2 * 1024**2
MAX_NEW_CONTENT_CHARS = 400_000
MAX_REPLACEMENTS = 30
MAX_LIST_ENTRIES = 500
MAX_LIST_DEPTH = 6
MAX_DIFF_LINES = 600
MAX_PENDING_CHANGES = 200
CHANGE_ID_LENGTH = 32


class AIWorkspaceError(RuntimeError):
    """可直接展示、不含本机绝对路径的工作区错误。"""


_SYSTEM_ALIASES = {
    Path("/var"): Path("/private/var"),
    Path("/tmp"): Path("/private/tmp"),
    Path("/etc"): Path("/private/etc"),
}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_workspace_root(value: object, *, protected: Iterable[Path] = ()) -> Path:
    """校验用户选择的工作区目录。"""
    if type(value) is not str or not value.strip() or "\x00" in value:
        raise AIWorkspaceError("工作区目录无效。")
    path = Path(os.path.abspath(Path(value.strip()).expanduser()))
    if path == Path(path.anchor) or path == Path.home():
        raise AIWorkspaceError("不能把磁盘根目录或用户主目录整体设为 AI 工作区，请选择具体的项目文件夹。")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError:
            raise AIWorkspaceError("工作区目录不存在或无法访问。") from None
        if stat.S_ISLNK(mode):
            # macOS 的 /var、/tmp、/etc 是指向 /private/... 的固定系统别名（临时目录都在其下）；
            # 只放行这些别名，用户自建的符号链接仍然拒绝。
            alias = _SYSTEM_ALIASES.get(current)
            if alias is not None and os.path.realpath(current) == str(alias):
                continue
            raise AIWorkspaceError("工作区目录不能包含符号链接。")
    if not path.is_dir():
        raise AIWorkspaceError("工作区目录不存在或不是文件夹。")
    for blocked in protected:
        blocked = Path(os.path.abspath(blocked))
        if _is_relative_to(path, blocked) or _is_relative_to(blocked, path):
            raise AIWorkspaceError("工作区不能与应用数据目录或课程归档目录重叠，请选择单独的文件夹。")
    return path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class _Target:
    relative: str
    path: Path


class AIWorkspace:
    def __init__(
        self,
        root: Path,
        *,
        changes_dir: Path,
        protected: Iterable[Path] = (),
        now: Callable[[], str] = _now_iso,
    ) -> None:
        self.protected = tuple(Path(os.path.abspath(item)) for item in protected)
        self.root = validate_workspace_root(str(root), protected=self.protected)
        self.changes_dir = changes_dir
        self.now = now

    # ----- 路径 -----
    def _resolve(self, relative: object, *, allow_missing: bool = False) -> _Target:
        if type(relative) is not str or not relative.strip() or len(relative) > 512 or "\x00" in relative:
            raise AIWorkspaceError("文件路径无效，请使用工作区内的相对路径。")
        text = relative.strip().replace("\\", "/")
        candidate = Path(text)
        if candidate.is_absolute() or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
            raise AIWorkspaceError("只接受工作区内的相对路径。")
        parts = [part for part in text.split("/") if part not in {"", "."}]
        if not parts or any(part == ".." for part in parts):
            raise AIWorkspaceError("文件路径不能越出工作区。")
        if any(part.startswith(".") for part in parts):
            raise AIWorkspaceError("不支持访问隐藏文件或隐藏文件夹。")
        if len(parts) > MAX_LIST_DEPTH + 2:
            raise AIWorkspaceError("文件路径层级过深。")
        current = self.root
        for index, part in enumerate(parts):
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                if allow_missing:
                    break
                raise AIWorkspaceError("文件不存在。") from None
            except OSError:
                raise AIWorkspaceError("无法访问该路径。") from None
            if stat.S_ISLNK(mode):
                raise AIWorkspaceError("路径中包含符号链接，已拒绝访问。")
            if index < len(parts) - 1 and not stat.S_ISDIR(mode):
                raise AIWorkspaceError("路径中的上级不是文件夹。")
        path = self.root.joinpath(*parts)
        absolute = Path(os.path.abspath(path))
        if not _is_relative_to(absolute, self.root):
            raise AIWorkspaceError("文件路径不能越出工作区。")
        for blocked in self.protected:
            if _is_relative_to(absolute, blocked):
                raise AIWorkspaceError("该位置受保护，AI 不能访问。")
        return _Target("/".join(parts), absolute)

    # ----- 浏览与读取 -----
    def list(self, subdir: object = "", *, limit: int = 200) -> dict[str, Any]:
        base = self.root if subdir in (None, "", ".") else self._resolve(subdir).path
        if not base.is_dir():
            raise AIWorkspaceError("该路径不是文件夹。")
        limit = max(1, min(int(limit), MAX_LIST_ENTRIES))
        items: list[dict[str, Any]] = []
        truncated = False
        stack: list[tuple[Path, int]] = [(base, 0)]
        while stack:
            folder, depth = stack.pop()
            try:
                entries = sorted(os.scandir(folder), key=lambda entry: entry.name.casefold())
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                path = Path(entry.path)
                if any(_is_relative_to(path, blocked) for blocked in self.protected):
                    continue
                relative = path.relative_to(self.root).as_posix()
                if entry.is_dir(follow_symlinks=False):
                    items.append({"path": relative + "/", "type": "folder"})
                    if depth + 1 < MAX_LIST_DEPTH:
                        stack.append((path, depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    suffix = path.suffix.casefold()
                    items.append(
                        {
                            "path": relative,
                            "type": "file",
                            "size": info.st_size,
                            "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                            "readable": suffix in READABLE_EXTENSIONS,
                            "editable": suffix in TEXT_EDIT_EXTENSIONS,
                        }
                    )
                if len(items) >= limit:
                    truncated = True
                    break
            if truncated:
                break
        return {
            "workspace": self.root.name,
            "items": items,
            "count": len(items),
            "truncated": truncated,
        }

    def resolve_readable(self, relative: object) -> tuple[str, Path]:
        target = self._resolve(relative)
        if not target.path.is_file():
            raise AIWorkspaceError("该路径不是文件。")
        if target.path.suffix.casefold() not in READABLE_EXTENSIONS:
            raise AIWorkspaceError("该文件类型暂不支持读取（支持文本、Markdown、代码、PDF、PPTX、DOCX、XLSX）。")
        return target.relative, target.path

    # ----- 修改提议 -----
    def _read_current(self, target: _Target) -> tuple[bytes | None, str | None]:
        try:
            mode = target.path.lstat().st_mode
        except FileNotFoundError:
            return None, None
        except OSError:
            raise AIWorkspaceError("无法读取现有文件。") from None
        if not stat.S_ISREG(mode):
            raise AIWorkspaceError("目标不是普通文件。")
        size = target.path.stat().st_size
        if size > MAX_EDIT_SOURCE_BYTES:
            raise AIWorkspaceError("现有文件超过 2 MiB，暂不支持由 AI 修改。")
        data = target.path.read_bytes()
        try:
            return data, data.decode("utf-8")
        except UnicodeDecodeError:
            raise AIWorkspaceError("现有文件不是 UTF-8 文本，暂不支持由 AI 修改。") from None

    def propose(
        self,
        relative: object,
        *,
        mode: object,
        content: object = None,
        edits: object = None,
        summary: object = None,
    ) -> dict[str, Any]:
        if mode not in {"create", "overwrite", "replace", "append"}:
            raise AIWorkspaceError("mode 必须是 create、overwrite、replace 或 append。")
        target = self._resolve(relative, allow_missing=True)
        if target.path.suffix.casefold() not in TEXT_EDIT_EXTENSIONS:
            raise AIWorkspaceError("只能新建或修改文本类文件（Markdown、TXT、CSV、JSON、代码等）。")
        current_bytes, current_text = self._read_current(target)
        if mode == "create":
            if current_bytes is not None:
                raise AIWorkspaceError("文件已存在；如需修改请使用 overwrite、replace 或 append。")
            if type(content) is not str:
                raise AIWorkspaceError("新建文件需要提供 content。")
            new_text = content
        else:
            if current_text is None:
                raise AIWorkspaceError("文件不存在；如需新建请使用 create。")
            if mode in {"overwrite", "append"}:
                if type(content) is not str or (mode == "append" and not content):
                    raise AIWorkspaceError(f"{mode} 需要提供 content。")
                new_text = content if mode == "overwrite" else current_text + content
            else:
                new_text = _apply_replacements(current_text, edits)
        return self._store(target, current_bytes, current_text, new_text, summary)

    def extend(self, change_id: object, content: object) -> dict[str, Any]:
        """把同一轮内对同一文件的后续 append 合并进尚未确认的修改提议（用于分段写长文档）。"""
        if type(content) is not str or not content:
            raise AIWorkspaceError("append 需要提供 content。")
        path = self._change_path(change_id)
        try:
            change = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise AIWorkspaceError("要续写的修改提议不存在。") from None
        if change.get("status") != "pending" or change.get("workspace_root") != str(self.root):
            raise AIWorkspaceError("要续写的修改提议已处理或不属于当前工作区。")
        target = self._resolve(change["path"], allow_missing=True)
        current_bytes, current_text = self._read_current(target)
        base = change.get("base_sha256")
        if (current_bytes is None) != (base is None) or (
            current_bytes is not None and _sha256(current_bytes) != base
        ):
            raise AIWorkspaceError("文件在提议之后被修改过，无法继续追加。")
        return self._store(
            target,
            current_bytes,
            current_text,
            change["new_content"] + content,
            change.get("summary"),
            change_id=change["id"],
            created_at=change.get("created_at"),
        )

    def _store(
        self,
        target: _Target,
        current_bytes: bytes | None,
        current_text: str | None,
        new_text: str,
        summary: object,
        *,
        change_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        if len(new_text) > MAX_NEW_CONTENT_CHARS or "\x00" in new_text:
            raise AIWorkspaceError("新内容过长或包含无效字符。")
        if current_text is not None and new_text == current_text:
            raise AIWorkspaceError("修改后的内容与原文件相同。")
        diff_lines = list(
            difflib.unified_diff(
                (current_text or "").splitlines(),
                new_text.splitlines(),
                fromfile=f"a/{target.relative}" if current_text is not None else "/dev/null",
                tofile=f"b/{target.relative}",
                lineterm="",
                n=3,
            )
        )
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        change = {
            "id": change_id or uuid.uuid4().hex,
            "path": target.relative,
            "mode": "create" if current_text is None else "modify",
            "workspace_root": str(self.root),
            "workspace": self.root.name,
            "base_sha256": _sha256(current_bytes) if current_bytes is not None else None,
            "new_content": new_text,
            "diff": "\n".join(diff_lines[:MAX_DIFF_LINES]),
            "diff_truncated": len(diff_lines) > MAX_DIFF_LINES,
            "added": added,
            "removed": removed,
            "summary": (summary.strip()[:300] if type(summary) is str else ""),
            "status": "pending",
            "created_at": created_at or self.now(),
            "resolved_at": None,
            "error": None,
        }
        self._save_change(change)
        return public_change(change)

    # ----- 修改记录 -----
    def _change_path(self, change_id: object) -> Path:
        if type(change_id) is not str or len(change_id) != CHANGE_ID_LENGTH or not change_id.isalnum():
            raise AIWorkspaceError("修改记录标识无效。")
        return self.changes_dir / f"{change_id}.json"

    def _save_change(self, change: dict[str, Any]) -> None:
        self.changes_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.changes_dir.glob("*.json"), key=lambda item: item.stat().st_mtime)
        for stale in existing[: max(0, len(existing) - MAX_PENDING_CHANGES + 1)]:
            stale.unlink(missing_ok=True)
        _atomic_write(self._change_path(change["id"]), json.dumps(change, ensure_ascii=False).encode("utf-8"))


def _apply_replacements(text: str, edits: object) -> str:
    if type(edits) is not list or not 1 <= len(edits) <= MAX_REPLACEMENTS:
        raise AIWorkspaceError(f"replace 需要 1 到 {MAX_REPLACEMENTS} 处 find/replace 修改。")
    for index, edit in enumerate(edits, 1):
        if type(edit) is not dict or set(edit) - {"find", "replace"}:
            raise AIWorkspaceError("每处修改必须只包含 find 与 replace。")
        find, replacement = edit.get("find"), edit.get("replace")
        if type(find) is not str or not find or type(replacement) is not str:
            raise AIWorkspaceError("find 不能为空，replace 必须是文本。")
        occurrences = text.count(find)
        if occurrences != 1:
            raise AIWorkspaceError(
                f"第 {index} 处修改的 find 文本在文件中出现了 {occurrences} 次，必须恰好出现一次；请提供更长、更唯一的片段。"
            )
        text = text.replace(find, replacement, 1)
    return text


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".ai-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def public_change(change: dict[str, Any]) -> dict[str, Any]:
    """界面与模型可见的修改记录（不含工作区绝对路径和完整新内容）。"""
    return {
        "change_id": change["id"],
        "path": change["path"],
        "workspace": change.get("workspace"),
        "mode": change["mode"],
        "summary": change.get("summary") or "",
        "added": change["added"],
        "removed": change["removed"],
        "diff": change["diff"],
        "diff_truncated": change.get("diff_truncated", False),
        "status": change["status"],
        "created_at": change.get("created_at"),
        "resolved_at": change.get("resolved_at"),
        "error": change.get("error"),
    }


class AIFileChangeStore:
    """读取并处理已提议的修改；应用时重新校验工作区与原文件。"""

    def __init__(self, changes_dir: Path, *, protected: Iterable[Path] = (), now: Callable[[], str] = _now_iso) -> None:
        self.changes_dir = changes_dir
        self.protected = tuple(protected)
        self.now = now

    def _load(self, change_id: object) -> tuple[Path, dict[str, Any]]:
        if type(change_id) is not str or len(change_id) != CHANGE_ID_LENGTH or not change_id.isalnum():
            raise AIWorkspaceError("修改记录标识无效。")
        path = self.changes_dir / f"{change_id}.json"
        try:
            change = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise AIWorkspaceError("修改记录不存在或已过期。") from None
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise AIWorkspaceError("修改记录已损坏。") from None
        if type(change) is not dict or change.get("id") != change_id:
            raise AIWorkspaceError("修改记录已损坏。")
        return path, change

    def get(self, change_id: object) -> dict[str, Any]:
        return public_change(self._load(change_id)[1])

    def _finish(self, path: Path, change: dict[str, Any], status: str, error: str | None = None) -> dict[str, Any]:
        change["status"] = status
        change["resolved_at"] = self.now()
        change["error"] = error
        _atomic_write(path, json.dumps(change, ensure_ascii=False).encode("utf-8"))
        return public_change(change)

    def reject(self, change_id: object) -> dict[str, Any]:
        path, change = self._load(change_id)
        if change["status"] != "pending":
            return public_change(change)
        return self._finish(path, change, "rejected")

    def apply(self, change_id: object, *, current_root: str | None) -> dict[str, Any]:
        path, change = self._load(change_id)
        if change["status"] != "pending":
            return public_change(change)
        if not current_root or os.path.abspath(current_root) != change["workspace_root"]:
            return self._finish(path, change, "conflict", "AI 工作区已更换，此修改不再适用。")
        try:
            workspace = AIWorkspace(Path(current_root), changes_dir=self.changes_dir, protected=self.protected)
            target = workspace._resolve(change["path"], allow_missing=True)
            current_bytes, _text = workspace._read_current(target)
        except AIWorkspaceError as exc:
            return self._finish(path, change, "conflict", str(exc))
        base = change.get("base_sha256")
        if (current_bytes is None) != (base is None) or (
            current_bytes is not None and _sha256(current_bytes) != base
        ):
            return self._finish(path, change, "conflict", "文件在提议之后被修改过，为避免覆盖你的改动已停止应用。")
        try:
            if current_bytes is not None:
                backup_dir = self.changes_dir / "backups" / change["id"]
                backup_dir.mkdir(parents=True, exist_ok=True)
                _atomic_write(backup_dir / target.path.name, current_bytes)
                change["backup"] = str(backup_dir / target.path.name)
            parent = target.path.parent
            relative_parent = parent.relative_to(workspace.root)
            current = workspace.root
            for part in relative_parent.parts:
                current = current / part
                if current.is_symlink():
                    raise AIWorkspaceError("路径中包含符号链接，已拒绝写入。")
                if not current.exists():
                    current.mkdir()
            _atomic_write(target.path, change["new_content"].encode("utf-8"))
        except (OSError, AIWorkspaceError) as exc:
            message = str(exc) if isinstance(exc, AIWorkspaceError) else "写入文件失败，请检查文件夹权限。"
            return self._finish(path, change, "failed", message)
        return self._finish(path, change, "applied")
