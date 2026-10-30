"""File-browser operations for the 交大云盘 page.

The desktop UI used to expose only the backup/archive workflow. This service turns the
existing :class:`SJTUCloudPanProvider` into a small, provider-neutral file manager API:
browse folders, sort/search the current folder, create folders, rename, move, copy,
delete to the recycle bin, upload local files and download remote files with progress.

Security notes
--------------
* Remote paths always travel as validated segment tuples; ``.``/``..``, separators and
  control characters are rejected before any request is made.
* Local paths are never accepted from the renderer directly. The bridge only passes
  paths that came from a native picker or a single-use native drop capability.
* Results never contain tokens, signed URLs or local absolute paths.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any, BinaryIO

from sjtu_learning_assistant.cloud_storage import (
    CloudAuthError,
    CloudConflictError,
    CloudItem,
    CloudNetworkError,
    CloudNotFoundError,
    CloudStorageError,
)

MAX_NAME_LENGTH = 255
MAX_LISTING_ITEMS = 10_000
LISTING_PAGE_SIZE = 500
LISTING_CACHE_SECONDS = 20.0
MAX_UPLOAD_BYTES = 2 * 1024**3
SIMPLE_UPLOAD_LIMIT = 8 * 1024 * 1024
MULTIPART_CHUNK_SIZE = 4 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE = 256 * 1024
MAX_BATCH_ITEMS = 200
MAX_UNIQUE_NAME_ATTEMPTS = 100
MAX_TRANSFER_HISTORY = 50
SORT_FIELDS = frozenset({"name", "modified", "size"})
SORT_ORDERS = frozenset({"asc", "desc"})
CONFLICT_STRATEGIES = frozenset({"ask", "overwrite", "rename"})
TRANSFER_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_WINDOWS_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_NATURAL_CHUNK = re.compile(r"(\d+)")


class CloudDriveError(CloudStorageError):
    """A user-facing drive error that is safe to display."""


class TransferCancelled(CloudDriveError):
    """The user cancelled an upload or download."""


def cloud_error_code(error: BaseException) -> str:
    """Stable bridge error code for cloud failures; the UI branches on these."""
    if isinstance(error, TransferCancelled):
        return "cancelled"
    if isinstance(error, CloudAuthError):
        return "auth_required"
    if isinstance(error, CloudNotFoundError):
        return "not_found"
    if isinstance(error, CloudConflictError):
        return "conflict"
    if isinstance(error, CloudNetworkError):
        return "network_error"
    return "operation_failed"


def validate_item_name(value: object, *, label: str = "名称") -> str:
    """Validate a single remote file/folder name typed by the user."""
    if type(value) is not str:
        raise CloudDriveError(f"{label}不正确。")
    name = unicodedata.normalize("NFC", value).strip()
    if not name:
        raise CloudDriveError(f"{label}不能为空。")
    if len(name) > MAX_NAME_LENGTH:
        raise CloudDriveError(f"{label}不能超过 {MAX_NAME_LENGTH} 个字符。")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise CloudDriveError(f"{label}不能包含 / 或 \\，也不能是 . 或 ..。")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise CloudDriveError(f"{label}不能包含控制字符。")
    return name


def safe_local_filename(name: str, *, fallback: str = "下载文件") -> str:
    """Return a file name that is valid on Windows and macOS."""
    clean = _WINDOWS_FORBIDDEN.sub("_", unicodedata.normalize("NFC", name)).strip()
    clean = clean.rstrip(". ")
    if not clean:
        clean = fallback
    stem = clean.partition(".")[0].rstrip(" ")
    if stem.upper() in _WINDOWS_RESERVED:
        clean = f"_{clean}"
    if len(clean) > 200:
        base, extension = os.path.splitext(clean)
        extension = extension[:20]
        clean = base[: 200 - len(extension)] + extension
    return clean


def numbered_name(name: str, index: int) -> str:
    """``report.pdf`` -> ``report (1).pdf``; folders and dotfiles keep their whole name."""
    base, extension = os.path.splitext(name)
    if not base or len(extension) > 16:
        base, extension = name, ""
    return f"{base} ({index}){extension}"


def _natural_key(name: str) -> tuple[Any, ...]:
    parts = _NATURAL_CHUNK.split(unicodedata.normalize("NFKC", name).casefold())
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts if part)


def _timestamp(value: str | None) -> float:
    if not value:
        return float("-inf")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def serialize_item(item: CloudItem) -> dict[str, Any]:
    return {
        "path": list(item.path),
        "name": item.name,
        "is_directory": item.is_directory,
        "size": item.size if not item.is_directory else None,
        "content_type": item.content_type,
        "created_at": item.created_at,
        "modified_at": item.modified_at,
    }


def sort_items(items: Iterable[CloudItem], *, by: str, order: str) -> list[CloudItem]:
    """Folders first (like the 交大云盘 web client), then the requested key."""
    reverse = order == "desc"

    def key(item: CloudItem) -> tuple[Any, ...]:
        if by == "size":
            primary: Any = item.size or 0
        elif by == "modified":
            primary = _timestamp(item.modified_at)
        else:
            primary = _natural_key(item.name)
        return (primary, _natural_key(item.name))

    folders = sorted((item for item in items if item.is_directory), key=key, reverse=reverse)
    files = sorted((item for item in items if not item.is_directory), key=key, reverse=reverse)
    if by == "size":
        # Folder sizes are unknown remotely; keep them alphabetical regardless of order.
        folders = sorted(folders, key=lambda item: _natural_key(item.name))
    return folders + files


@dataclass
class Transfer:
    transfer_id: str
    kind: str
    name: str
    total: int | None = None
    done: int = 0
    status: str = "running"
    message: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.transfer_id,
            "kind": self.kind,
            "name": self.name,
            "total": self.total,
            "done": self.done,
            "status": self.status,
            "message": self.message,
        }


class TransferRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Transfer] = {}

    def start(self, transfer_id: str | None, *, kind: str, name: str, total: int | None) -> Transfer:
        identifier = transfer_id or f"t{time.monotonic_ns():x}"
        if not TRANSFER_ID.fullmatch(identifier):
            raise CloudDriveError("传输任务标识不正确。")
        with self._lock:
            existing = self._items.get(identifier)
            if existing is not None and existing.status == "running":
                raise CloudDriveError("同一传输任务已在进行中。")
            transfer = Transfer(identifier, kind, name, total)
            self._items[identifier] = transfer
            finished = [item for item in self._items.values() if item.status != "running"]
            if len(finished) > MAX_TRANSFER_HISTORY:
                finished.sort(key=lambda item: item.finished_at or 0)
                for stale in finished[: len(finished) - MAX_TRANSFER_HISTORY]:
                    self._items.pop(stale.transfer_id, None)
            return transfer

    def advance(self, transfer: Transfer, amount: int) -> None:
        if transfer.cancel_event.is_set():
            raise TransferCancelled("传输已取消。")
        with self._lock:
            transfer.done += amount

    def finish(self, transfer: Transfer, status: str, message: str | None = None) -> None:
        with self._lock:
            transfer.status = status
            transfer.message = message
            transfer.finished_at = time.monotonic()

    def cancel(self, transfer_id: str) -> bool:
        with self._lock:
            transfer = self._items.get(transfer_id)
        if transfer is None or transfer.status != "running":
            return False
        transfer.cancel_event.set()
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [item.snapshot() for item in self._items.values()]


class _ProgressReader:
    """File wrapper that reports bytes read and honours cancellation."""

    def __init__(
        self,
        handle: BinaryIO,
        on_read: Callable[[int], None],
        *,
        limit: int | None = None,
    ) -> None:
        self._handle = handle
        self._on_read = on_read
        self._remaining = limit

    def read(self, size: int = -1) -> bytes:
        if self._remaining is not None:
            if self._remaining <= 0:
                return b""
            if size < 0 or size > self._remaining:
                size = self._remaining
        data = self._handle.read(size)
        if data:
            if self._remaining is not None:
                self._remaining -= len(data)
            self._on_read(len(data))
        return data

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        return self._handle.seek(offset, whence)

    def tell(self) -> int:
        return self._handle.tell()


@dataclass(frozen=True)
class _Listing:
    items: tuple[CloudItem, ...]
    truncated: bool
    fetched_at: float


class CloudDriveService:
    """Stateless façade over a cloud provider plus a short listing cache."""

    def __init__(
        self,
        provider_getter: Callable[[], Any],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider_getter = provider_getter
        self._clock = clock
        self._cache_lock = threading.Lock()
        self._listings: dict[tuple[str, ...], _Listing] = {}
        self.transfers = TransferRegistry()

    # ------------------------------------------------------------------ helpers
    def _provider(self) -> Any:
        return self._provider_getter()

    def _invalidate(self, *paths: Sequence[str]) -> None:
        with self._cache_lock:
            for path in paths:
                key = tuple(path)
                # Drop the folder itself and anything nested below it.
                for cached in tuple(self._listings):
                    if cached[: len(key)] == key:
                        self._listings.pop(cached, None)

    def _listing(self, path: tuple[str, ...], *, refresh: bool) -> _Listing:
        now = self._clock()
        with self._cache_lock:
            cached = self._listings.get(path)
        if cached is not None and not refresh and now - cached.fetched_at < LISTING_CACHE_SECONDS:
            return cached
        iterator: Iterator[CloudItem] = self._provider().iter_directory(path, page_size=LISTING_PAGE_SIZE)
        items = tuple(islice(iterator, MAX_LISTING_ITEMS + 1))
        listing = _Listing(items[:MAX_LISTING_ITEMS], len(items) > MAX_LISTING_ITEMS, now)
        with self._cache_lock:
            self._listings[path] = listing
            if len(self._listings) > 64:
                oldest = min(self._listings, key=lambda key: self._listings[key].fetched_at)
                self._listings.pop(oldest, None)
        return listing

    def _child_exists(self, parent: tuple[str, ...], name: str) -> bool:
        return bool(self._provider().exists(parent + (name,)))

    def _unique_remote_name(self, parent: tuple[str, ...], name: str) -> str:
        if not self._child_exists(parent, name):
            return name
        for index in range(1, MAX_UNIQUE_NAME_ATTEMPTS + 1):
            candidate = numbered_name(name, index)
            if not self._child_exists(parent, candidate):
                return candidate
        raise CloudConflictError("同名项目过多，请先重命名后再试。", status_code=409)

    # ------------------------------------------------------------------- reads
    def space(self) -> dict[str, Any]:
        info = self._provider().get_space_info()
        return {
            "capacity": info.capacity,
            "used": info.used,
            "available": info.available,
            "has_personal_space": info.has_personal_space,
        }

    def list(
        self,
        path: Sequence[str],
        *,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "name",
        order: str = "asc",
        query: str = "",
        refresh: bool = False,
    ) -> dict[str, Any]:
        if sort_by not in SORT_FIELDS or order not in SORT_ORDERS:
            raise CloudDriveError("排序参数不正确。")
        if page < 1 or not 1 <= page_size <= 200:
            raise CloudDriveError("分页参数不正确。")
        normalized = tuple(path)
        listing = self._listing(normalized, refresh=refresh)
        items: Iterable[CloudItem] = listing.items
        needle = unicodedata.normalize("NFKC", query).casefold().strip()
        if needle:
            items = [item for item in items if needle in unicodedata.normalize("NFKC", item.name).casefold()]
        ordered = sort_items(items, by=sort_by, order=order)
        total = len(ordered)
        pages = max(1, -(-total // page_size))
        current = min(page, pages)
        start = (current - 1) * page_size
        window = ordered[start : start + page_size]
        return {
            "path": list(normalized),
            "page": current,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "folder_count": sum(1 for item in listing.items if item.is_directory),
            "file_count": sum(1 for item in listing.items if not item.is_directory),
            "truncated": listing.truncated,
            "query": query.strip(),
            "sort": {"by": sort_by, "order": order},
            "items": [serialize_item(item) for item in window],
        }

    def info(self, path: Sequence[str]) -> dict[str, Any]:
        return serialize_item(self._provider().get_info(tuple(path)))

    # ------------------------------------------------------------------ writes
    def create_folder(self, parent: Sequence[str], name: object) -> dict[str, Any]:
        clean = validate_item_name(name, label="文件夹名称")
        parent_path = tuple(parent)
        target = parent_path + (clean,)
        provider = self._provider()
        try:
            provider.create_directory(target)
        except CloudConflictError:
            raise CloudConflictError(f"当前位置已存在名为“{clean}”的项目。", status_code=409) from None
        finally:
            self._invalidate(parent_path)
        try:
            return serialize_item(provider.get_info(target))
        except CloudStorageError:
            return serialize_item(CloudItem(name=clean, path=target, is_directory=True))

    def rename(self, path: Sequence[str], new_name: object, *, is_directory: bool = False) -> dict[str, Any]:
        source = tuple(path)
        if not source:
            raise CloudDriveError("不能重命名根目录。")
        clean = validate_item_name(new_name)
        if clean == source[-1]:
            return serialize_item(self._provider().get_info(source))
        target = source[:-1] + (clean,)
        try:
            # Case-only renames are a no-op conflict on case-insensitive backends.
            item = self._provider().move(source, target, overwrite=False, directory=is_directory)
        except CloudConflictError:
            raise CloudConflictError(f"当前位置已存在名为“{clean}”的项目。", status_code=409) from None
        finally:
            self._invalidate(source[:-1], source)
        return serialize_item(item)

    def _batch(
        self,
        entries: Sequence[tuple[Sequence[str], bool]],
        action: Callable[[tuple[str, ...], bool], Any],
    ) -> dict[str, Any]:
        if not 1 <= len(entries) <= MAX_BATCH_ITEMS:
            raise CloudDriveError(f"一次最多处理 {MAX_BATCH_ITEMS} 个项目。")
        done: list[list[str]] = []
        failed: list[dict[str, Any]] = []
        for raw, is_directory in entries:
            source = tuple(raw)
            try:
                action(source, is_directory)
            except CloudStorageError as exc:
                failed.append({
                    "path": list(source),
                    "name": source[-1] if source else "",
                    "code": cloud_error_code(exc),
                    "message": str(exc) or "操作失败。",
                })
            else:
                done.append(list(source))
        return {"done": done, "failed": failed}

    def delete(self, entries: Sequence[tuple[Sequence[str], bool]]) -> dict[str, Any]:
        """Move items to the 交大云盘 recycle bin (never a permanent delete)."""
        provider = self._provider()

        def remove(source: tuple[str, ...], is_directory: bool) -> None:
            if not source:
                raise CloudDriveError("不能删除根目录。")
            try:
                # Already-deleted items are treated as success so retries stay idempotent.
                provider.delete(source, permanent=False, missing_ok=True, directory=is_directory)
            finally:
                self._invalidate(source[:-1], source)

        return self._batch(entries, remove)

    def transfer(
        self,
        entries: Sequence[tuple[Sequence[str], bool]],
        destination: Sequence[str],
        *,
        copy: bool = False,
    ) -> dict[str, Any]:
        provider = self._provider()
        target_dir = tuple(destination)
        try:
            if target_dir and not provider.get_info(target_dir).is_directory:
                raise CloudDriveError("目标位置不是文件夹。")
        except CloudNotFoundError:
            raise CloudNotFoundError("目标文件夹不存在或已被删除。", status_code=404) from None

        def run(source: tuple[str, ...], is_directory: bool) -> None:
            if not source:
                raise CloudDriveError("不能移动根目录。")
            if source[:-1] == target_dir and not copy:
                raise CloudDriveError("项目已在目标文件夹中。")
            if target_dir[: len(source)] == source:
                raise CloudDriveError("不能把文件夹移动或复制到它自身或子文件夹中。")
            name = source[-1]
            if copy and source[:-1] == target_dir:
                name = self._unique_remote_name(target_dir, name)
            try:
                if copy:
                    provider.copy(source, target_dir + (name,), overwrite=False, directory=is_directory)
                else:
                    provider.move(source, target_dir + (name,), overwrite=False, directory=is_directory)
            except CloudConflictError:
                raise CloudConflictError(f"目标文件夹已存在“{name}”。", status_code=409) from None
            finally:
                self._invalidate(source[:-1], target_dir, source)

        return self._batch(entries, run)

    # --------------------------------------------------------------- transfers
    def upload(
        self,
        local_path: str,
        parent: Sequence[str],
        *,
        conflict: str = "ask",
        transfer_id: str | None = None,
        ensure_parent: bool = False,
    ) -> dict[str, Any]:
        if conflict not in CONFLICT_STRATEGIES:
            raise CloudDriveError("同名处理方式不正确。")
        source = Path(local_path)
        try:
            if not source.is_file():
                raise CloudDriveError("待上传的本地文件不存在。")
            handle = source.open("rb")
        except OSError:
            raise CloudDriveError("无法读取待上传的本地文件。") from None
        name = validate_item_name(source.name, label="文件名")
        parent_path = tuple(parent)
        provider = self._provider()
        with handle:
            try:
                size = os.fstat(handle.fileno()).st_size
            except OSError:
                raise CloudDriveError("无法读取待上传的本地文件。") from None
            if size > MAX_UPLOAD_BYTES:
                raise CloudDriveError("单个文件不能超过 2 GiB。")
            if ensure_parent and parent_path:
                provider.ensure_directory(parent_path)
            if conflict == "rename":
                name = self._unique_remote_name(parent_path, name)
            target = parent_path + (name,)
            transfer = self.transfers.start(transfer_id, kind="upload", name=name, total=size)
            try:
                reader = _ProgressReader(
                    handle,
                    lambda amount: self.transfers.advance(transfer, amount),
                    limit=size,
                )
                overwrite = conflict == "overwrite"
                if size <= SIMPLE_UPLOAD_LIMIT:
                    item = provider.simple_upload(target, reader, overwrite=overwrite)
                else:
                    item = provider.multipart_upload(
                        target, reader, overwrite=overwrite, chunk_size=MULTIPART_CHUNK_SIZE
                    )
            except TransferCancelled:
                self.transfers.finish(transfer, "cancelled", "已取消")
                raise
            except CloudConflictError:
                self.transfers.finish(transfer, "conflict", "存在同名文件")
                raise CloudConflictError(f"当前文件夹已存在“{name}”。", status_code=409) from None
            except OSError:
                self.transfers.finish(transfer, "failed", "读取本地文件失败")
                raise CloudDriveError("读取本地文件失败，请确认文件未被占用。") from None
            except CloudStorageError as exc:
                self.transfers.finish(transfer, "failed", str(exc) or "上传失败")
                raise
            finally:
                self._invalidate(parent_path)
        self.transfers.finish(transfer, "done")
        return {"transfer_id": transfer.transfer_id, "item": serialize_item(item)}

    def download(
        self,
        remote_path: Sequence[str],
        destination: str,
        *,
        transfer_id: str | None = None,
    ) -> dict[str, Any]:
        source = tuple(remote_path)
        if not source:
            raise CloudDriveError("请选择要下载的文件。")
        provider = self._provider()
        item = provider.get_info(source)
        if item.is_directory:
            raise CloudDriveError("暂不支持直接下载文件夹，请进入文件夹后选择文件。")
        target = Path(destination)
        if not target.parent.is_dir():
            raise CloudDriveError("下载目标文件夹不存在。")
        transfer = self.transfers.start(transfer_id, kind="download", name=item.name, total=item.size)
        descriptor: int | None = None
        temporary: Path | None = None
        written = 0
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".sjtu-pan-", suffix=".part", dir=str(target.parent)
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                for chunk in provider.download_stream(source, chunk_size=DOWNLOAD_CHUNK_SIZE):
                    self.transfers.advance(transfer, len(chunk))
                    output.write(chunk)
                    written += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if item.size is not None and written != item.size:
                raise CloudDriveError("下载的文件大小与云端不一致，请重试。")
            os.replace(temporary, target)
        except TransferCancelled:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            self.transfers.finish(transfer, "cancelled", "已取消")
            raise
        except OSError:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            self.transfers.finish(transfer, "failed", "写入本地文件失败")
            raise CloudDriveError("写入本地文件失败，请检查磁盘空间或文件是否被占用。") from None
        except BaseException as exc:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            message = str(exc) if isinstance(exc, CloudStorageError) else "下载失败"
            self.transfers.finish(transfer, "failed", message or "下载失败")
            raise
        self.transfers.finish(transfer, "done")
        return {
            "transfer_id": transfer.transfer_id,
            "name": target.name,
            "size": written,
            "status": "downloaded",
        }

    def cancel(self, transfer_id: str) -> dict[str, Any]:
        if not TRANSFER_ID.fullmatch(transfer_id):
            raise CloudDriveError("传输任务标识不正确。")
        return {"transfer_id": transfer_id, "cancelled": self.transfers.cancel(transfer_id)}


def unique_local_path(directory: Path, name: str) -> Path:
    """Pick ``name`` or ``name (n)`` inside ``directory`` without overwriting."""
    clean = safe_local_filename(name)
    candidate = directory / clean
    index = 1
    while candidate.exists():
        if index > MAX_UNIQUE_NAME_ATTEMPTS:
            raise CloudDriveError("下载目录中同名文件过多，请先清理后再试。")
        candidate = directory / numbered_name(clean, index)
        index += 1
    return candidate


def iter_folder_files(root: Path, *, max_files: int = 2000, max_depth: int = 32) -> list[tuple[Path, tuple[str, ...]]]:
    """Walk a user-picked folder for upload; skips links, hidden and OS metadata files."""
    skipped = {".ds_store", "thumbs.db", "desktop.ini"}
    result: list[tuple[Path, tuple[str, ...]]] = []
    base = (root.name,)
    stack: list[tuple[Path, tuple[str, ...], int]] = [(root, base, 0)]
    while stack:
        directory, relative, depth = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise CloudDriveError(f"无法读取文件夹“{directory.name}”。") from exc
        for entry in entries:
            if entry.name.startswith(".") or entry.name.casefold() in skipped:
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if depth + 1 >= max_depth:
                        raise CloudDriveError(f"文件夹层级不能超过 {max_depth} 层。")
                    stack.append((Path(entry.path), relative + (entry.name,), depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError as exc:
                raise CloudDriveError(f"无法读取项目“{entry.name}”。") from exc
            result.append((Path(entry.path), relative))
            if len(result) > max_files:
                raise CloudDriveError(f"文件夹内文件超过 {max_files} 个，请分批上传。")
    return result


__all__ = [
    "CloudDriveError",
    "CloudDriveService",
    "TransferCancelled",
    "cloud_error_code",
    "iter_folder_files",
    "numbered_name",
    "safe_local_filename",
    "sort_items",
    "unique_local_path",
    "validate_item_name",
]
