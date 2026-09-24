"""安全摄取、派生与读取 AI 附件的应用受控副本。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, selectinload

from sjtu_learning_assistant.models import AIFileDerivative, AIManagedFile

MANAGED_DIRECTORY_NAME = ".ai_attachments"
MAX_SOURCE_SIZE = 2 * 1024**3
MAX_EXTRACT_BYTES = 4 * 1024**2
MAX_EXTRACT_CHARS = 1_000_000
TEXT_EXTENSIONS = frozenset(
    {
        ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv",
        ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".c",
        ".h", ".cc", ".cpp", ".hpp", ".go", ".rs", ".rb", ".php",
        ".swift", ".kt", ".kts", ".scala", ".sh", ".bash", ".zsh",
        ".fish", ".sql", ".html", ".htm", ".css", ".scss", ".less",
        ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
        ".log", ".rst", ".tex",
    }
)
_TAG_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,8}")
_SAFE_NAME = re.compile(r"[^\w.() -]+", re.UNICODE)
_STOP_WORDS = {
    "the", "and", "for", "with", "from", "this", "that", "true", "false",
    "none", "null", "import", "return", "class", "def", "const", "function",
    "一个", "以及", "这个", "可以", "进行", "文件", "内容", "使用", "中的",
}


class AIFileError(RuntimeError):
    """可安全展示且不包含本地绝对路径的附件错误。"""


SummaryEnhancer = Callable[[str, str, str, tuple[str, ...]], tuple[str, Iterable[str]]]
ProviderFactory = Callable[[], Any]


def _safe_display_name(value: str) -> str:
    leaf = Path(value.replace("\x00", "")).name.strip()
    leaf = _SAFE_NAME.sub("_", leaf).strip(" .")
    return (leaf[:255].rstrip(" .") or "attachment")


def _ensure_managed_root(archive_root: Path) -> Path:
    archive = Path(os.path.abspath(os.fspath(archive_root.expanduser())))
    try:
        if archive.exists() and (archive.is_symlink() or not archive.is_dir()):
            raise AIFileError("归档目录不安全。")
        archive.mkdir(parents=True, exist_ok=True, mode=0o700)
        root = archive / MANAGED_DIRECTORY_NAME
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            raise AIFileError("AI 附件受控目录不安全。")
        root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
        objects = root / "objects"
        if objects.exists() and (objects.is_symlink() or not objects.is_dir()):
            raise AIFileError("AI 附件受控目录不安全。")
        objects.mkdir(mode=0o700, exist_ok=True)
        return root
    except AIFileError:
        raise
    except OSError:
        raise AIFileError("无法创建 AI 附件受控目录。") from None


def _controlled_path(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    if not relative or "\x00" in relative:
        raise AIFileError("附件受控路径无效。")
    rel = Path(relative)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise AIFileError("附件受控路径越界。")
    current = root
    try:
        if current.is_symlink() or not current.is_dir():
            raise AIFileError("AI 附件受控目录不安全。")
        for part in rel.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise AIFileError("附件受控路径包含符号链接。")
        lexical = Path(os.path.abspath(os.fspath(current)))
        lexical.relative_to(Path(os.path.abspath(os.fspath(root))))
        if must_exist:
            mode = lexical.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise AIFileError("附件受控副本不存在。")
        return lexical
    except AIFileError:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError):
        raise AIFileError("附件受控副本不存在或不安全。") from None


def _decode_text(payload: bytes) -> str:
    if b"\x00" in payload[:4096] and not (
        payload.startswith(b"\xff\xfe") or payload.startswith(b"\xfe\xff")
    ):
        raise UnicodeError("binary")
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeError:
            continue
    raise UnicodeError("encoding")


def extract_text(path: Path, name: str) -> tuple[str, str]:
    """Extract supported text formats with stdlib only and hard input/output bounds."""
    suffix = Path(name).suffix.casefold()
    if suffix not in TEXT_EXTENSIONS:
        raise AIFileError("unsupported")
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_EXTRACT_BYTES + 1)
    except OSError:
        raise AIFileError("无法读取受控副本。") from None
    if len(payload) > MAX_EXTRACT_BYTES:
        payload = payload[:MAX_EXTRACT_BYTES]
    try:
        text = _decode_text(payload)
    except UnicodeError:
        raise AIFileError("unsupported") from None
    extractor = "stdlib-text"
    if suffix == ".json":
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            extractor = "stdlib-json"
        except (json.JSONDecodeError, TypeError, ValueError):
            extractor = "stdlib-text"
    return text[:MAX_EXTRACT_CHARS], extractor


def heuristic_summary_tags(name: str, text: str) -> tuple[str, tuple[str, ...]]:
    normalized = " ".join(text.split())
    summary = normalized[:500] + ("…" if len(normalized) > 500 else "")
    suffix = Path(name).suffix.casefold().lstrip(".")
    words = [word.casefold() for word in _TAG_WORD.findall(text[:40_000])]
    counts = Counter(word for word in words if word not in _STOP_WORDS and len(word) <= 32)
    tags: list[str] = []
    if suffix:
        tags.append(suffix)
    for word, _count in counts.most_common(12):
        if word not in tags:
            tags.append(word)
        if len(tags) >= 8:
            break
    return summary or f"{name}（无可提取文本）", tuple(tags)


def attachment_dto(row: AIManagedFile) -> dict[str, Any]:
    derivative = row.derivative
    return {
        "id": row.id,
        "name": row.name,
        "size": row.size,
        "sha256": row.sha256,
        "status": row.status,
        "cloud_ready": bool(row.cloud_path and row.cloud_size is not None),
        "summary": derivative.summary if derivative else None,
        "tags": list(derivative.tags or []) if derivative else [],
        "text_status": derivative.text_status if derivative else "pending",
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
        "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else None,
    }


class AIManagedFileService:
    def __init__(
        self,
        engine: Engine,
        *,
        archive_root: Path,
        summary_enhancer: SummaryEnhancer | None = None,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        self.engine = engine
        self.archive_root = Path(archive_root)
        self.summary_enhancer = summary_enhancer
        self.provider_factory = provider_factory

    @property
    def managed_root(self) -> Path:
        return _ensure_managed_root(self.archive_root)

    def _copy_source(self, source: Path) -> tuple[str, int, str, Path]:
        if "\x00" in os.fspath(source):
            raise AIFileError("所选文件无效。")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        temporary: Path | None = None
        try:
            descriptor = os.open(source.expanduser(), flags)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SOURCE_SIZE:
                raise AIFileError("所选文件无效或超过 2 GiB。")
            display_name = _safe_display_name(source.name)
            objects = self.managed_root / "objects"
            temp_fd, temp_name = tempfile.mkstemp(prefix=".ingest-", dir=objects)
            temporary = Path(temp_name)
            digest = hashlib.sha256()
            copied = 0
            with os.fdopen(temp_fd, "wb") as output, os.fdopen(os.dup(descriptor), "rb") as input_stream:
                while True:
                    chunk = input_stream.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            after = os.fstat(descriptor)
            identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            if identity_before != identity_after or copied != before.st_size:
                raise AIFileError("所选文件在复制时发生变化，请重试。")
            sha256 = digest.hexdigest()
            directory = objects / sha256[:2]
            if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
                raise AIFileError("AI 附件受控目录不安全。")
            directory.mkdir(mode=0o700, exist_ok=True)
            target = directory / sha256
            relative = target.relative_to(self.managed_root).as_posix()
            if target.exists():
                existing = _controlled_path(self.managed_root, relative)
                if existing.stat().st_size != copied or hashlib.sha256(existing.read_bytes()).hexdigest() != sha256:
                    raise AIFileError("受控副本哈希冲突，已拒绝覆盖。")
                temporary.unlink(missing_ok=True)
            else:
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            return display_name, copied, sha256, target
        except AIFileError:
            raise
        except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
            raise AIFileError("无法安全读取所选文件。") from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def ingest(self, source_path: str | Path) -> dict[str, Any]:
        source = Path(source_path)
        name, size, sha256, target = self._copy_source(source)
        relative = target.relative_to(self.managed_root).as_posix()
        text: str | None = None
        extractor: str | None = None
        error: str | None = None
        try:
            text, extractor = extract_text(target, name)
            text_status = "ready"
        except AIFileError as exc:
            if str(exc) == "unsupported":
                text_status = "unsupported"
            else:
                text_status = "failed"
                error = str(exc)[:240]
        summary, tags = heuristic_summary_tags(name, text or "")
        if text is not None and self.summary_enhancer is not None:
            try:
                enhanced_summary, enhanced_tags = self.summary_enhancer(name, text[:12_000], summary, tags)
                if isinstance(enhanced_summary, str) and enhanced_summary.strip():
                    summary = enhanced_summary.strip()[:1000]
                clean_tags = [
                    str(tag).strip()[:32]
                    for tag in enhanced_tags
                    if str(tag).strip() and "\x00" not in str(tag)
                ][:12]
                if clean_tags:
                    tags = tuple(dict.fromkeys(clean_tags))
            except Exception:
                pass
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            row = session.scalar(
                select(AIManagedFile)
                .where(AIManagedFile.sha256 == sha256)
                .options(selectinload(AIManagedFile.derivative))
            )
            if row is None:
                row = AIManagedFile(
                    name=name,
                    size=size,
                    sha256=sha256,
                    status="local",
                    controlled_relpath=relative,
                )
                session.add(row)
                session.flush()
            else:
                row.name = name
                row.size = size
                row.status = "local"
                row.controlled_relpath = relative
                row.updated_at = now
            derivative = row.derivative
            if derivative is None:
                derivative = AIFileDerivative(managed_file=row)
                session.add(derivative)
            derivative.summary = summary
            derivative.tags = list(tags)
            derivative.text_status = text_status
            derivative.text = text
            derivative.extractor = extractor
            derivative.last_error = error
            derivative.updated_at = now
            session.flush()
            result = attachment_dto(row)
        return result

    def list(self, *, limit: int = 100) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise AIFileError("limit 必须为 1 到 500 的整数。")
        with Session(self.engine) as session:
            rows = session.scalars(
                select(AIManagedFile)
                .options(selectinload(AIManagedFile.derivative))
                .order_by(AIManagedFile.updated_at.desc(), AIManagedFile.id.desc())
                .limit(limit)
            ).all()
            return {"items": [attachment_dto(row) for row in rows], "count": len(rows)}

    def get(self, attachment_id: int) -> dict[str, Any]:
        row = self._get_row(attachment_id)
        return attachment_dto(row)

    def _get_row(self, attachment_id: int) -> AIManagedFile:
        if type(attachment_id) is not int or attachment_id <= 0:
            raise AIFileError("附件标识无效。")
        with Session(self.engine) as session:
            row = session.scalar(
                select(AIManagedFile)
                .where(AIManagedFile.id == attachment_id)
                .options(selectinload(AIManagedFile.derivative))
            )
            if row is None:
                raise AIFileError("附件不存在。")
            session.expunge(row)
            return row

    def _cloud_segments(self, cloud_path: str | None) -> tuple[str, ...]:
        if not cloud_path or "\x00" in cloud_path or "\\" in cloud_path:
            raise AIFileError("云端附件路径无效。")
        parts = tuple(cloud_path.split("/"))
        if any(not part or part in {".", ".."} for part in parts):
            raise AIFileError("云端附件路径无效。")
        return parts

    @staticmethod
    def _hash_path(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
        except OSError:
            raise AIFileError("无法校验附件内容。") from None
        return size, digest.hexdigest()

    def _download_cloud_text(self, row: AIManagedFile) -> tuple[str, str]:
        if not row.cloud_path or self.provider_factory is None:
            raise AIFileError("云端附件正文暂不可用。")
        provider = None
        try:
            provider = self.provider_factory()
            remote_path = self._cloud_segments(row.cloud_path)
            info = provider.get_info(remote_path)
            if info.is_directory or info.size != row.size:
                raise AIFileError("云端附件校验失败，正文不可用。")
            with provider.download_temp(remote_path) as temporary:
                path = Path(temporary)
                payload_size, payload_sha = self._hash_path(path)
                if payload_size != row.size or payload_sha != row.sha256:
                    raise AIFileError("云端附件校验失败，正文不可用。")
                return extract_text(path, row.name)
        except AIFileError:
            raise
        except Exception:
            raise AIFileError("云端附件正文暂不可用。") from None
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def read_text(self, attachment_id: int, *, max_chars: int = 4000) -> dict[str, Any]:
        if type(max_chars) is not int or not 1 <= max_chars <= 12_000:
            raise AIFileError("max_chars 必须为 1 到 12000 的整数。")
        row = self._get_row(attachment_id)
        derivative = row.derivative
        if row.status == "cloud_only":
            try:
                text, _extractor = self._download_cloud_text(row)
            except AIFileError:
                return {
                    "id": row.id,
                    "name": row.name,
                    "status": "unavailable",
                    "reason": "附件仅在云端且当前无法安全获取正文。",
                    "text": "",
                    "truncated": False,
                }
        elif derivative is not None and derivative.text_status == "ready" and derivative.text is not None:
            text = derivative.text
        else:
            return {
                "id": row.id,
                "name": row.name,
                "status": derivative.text_status if derivative else "unavailable",
                "reason": "该附件没有可用的文本正文。",
                "text": "",
                "truncated": False,
            }
        return {
            "id": row.id,
            "name": row.name,
            "status": "ready",
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
            "returned_chars": min(len(text), max_chars),
        }

    def context(self, attachment_ids: Iterable[int], *, max_chars: int = 10_000) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(attachment_ids))
        if len(ids) > 20 or any(type(value) is not int or value <= 0 for value in ids):
            raise AIFileError("附件标识列表无效。")
        remaining = max_chars
        result: list[dict[str, Any]] = []
        for attachment_id in ids:
            row = self._get_row(attachment_id)
            item = attachment_dto(row)
            excerpt = self.read_text(attachment_id, max_chars=max(1, min(remaining, 4000)))
            text = excerpt.get("text", "") if remaining > 0 else ""
            remaining -= len(text)
            result.append(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "summary": item["summary"],
                    "tags": item["tags"],
                    "text_status": excerpt["status"],
                    "text_excerpt": text,
                    "text_truncated": bool(excerpt.get("truncated")),
                }
            )
        return result

    def resolve_controlled_copy(self, attachment_id: int) -> Path:
        """Resolve only an existing regular copy under ``.ai_attachments``."""
        row = self._get_row(attachment_id)
        if not row.controlled_relpath:
            raise AIFileError("附件受控副本当前不在本地。")
        return _controlled_path(self.managed_root, row.controlled_relpath)

    def search(
        self,
        query: str,
        *,
        attachment_ids: Iterable[int] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if type(query) is not str or len(query) > 160 or "\x00" in query:
            raise AIFileError("附件搜索关键词无效。")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise AIFileError("limit 必须为 1 到 50 的整数。")
        ids = None
        if attachment_ids is not None:
            ids = list(dict.fromkeys(attachment_ids))
            if len(ids) > 20 or any(type(value) is not int or value <= 0 for value in ids):
                raise AIFileError("附件标识列表无效。")
        needle = query.strip().casefold()
        with Session(self.engine) as session:
            statement = (
                select(AIManagedFile)
                .options(selectinload(AIManagedFile.derivative))
                .order_by(AIManagedFile.updated_at.desc(), AIManagedFile.id.desc())
            )
            if ids is not None:
                statement = statement.where(AIManagedFile.id.in_(ids))
            rows = session.scalars(statement.limit(100 if needle else limit)).all()
            matches: list[dict[str, Any]] = []
            for row in rows:
                derivative = row.derivative
                haystack = " ".join(
                    (
                        row.name,
                        derivative.summary or "" if derivative else "",
                        " ".join(derivative.tags or []) if derivative else "",
                    )
                ).casefold()
                if needle and needle not in haystack:
                    continue
                matches.append(attachment_dto(row))
                if len(matches) >= limit:
                    break
        return {"items": matches, "count": len(matches)}

    def summary_context(self, attachment_ids: Iterable[int]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(attachment_ids))
        if len(ids) > 20 or any(type(value) is not int or value <= 0 for value in ids):
            raise AIFileError("附件标识列表无效。")
        if not ids:
            return []
        with Session(self.engine) as session:
            rows = session.scalars(
                select(AIManagedFile)
                .where(AIManagedFile.id.in_(ids))
                .options(selectinload(AIManagedFile.derivative))
            ).all()
            by_id = {row.id: row for row in rows}
            if len(by_id) != len(ids):
                raise AIFileError("附件不存在。")
            return [
                {
                    "id": attachment_id,
                    "name": by_id[attachment_id].name,
                    "summary": (
                        by_id[attachment_id].derivative.summary
                        if by_id[attachment_id].derivative
                        else None
                    ),
                    "tags": list(by_id[attachment_id].derivative.tags or [])
                    if by_id[attachment_id].derivative
                    else [],
                    "text_status": by_id[attachment_id].derivative.text_status
                    if by_id[attachment_id].derivative
                    else "pending",
                }
                for attachment_id in ids
            ]

    def record_cloud_backup(
        self,
        attachment_id: int,
        *,
        cloud_path: str,
        cloud_size: int,
        cloud_sha256: str,
        cloud_remote_id: str | None = None,
        cloud_provider: str = "sjtu-pan",
        remove_local: bool = False,
    ) -> dict[str, Any]:
        if type(remove_local) is not bool:
            raise AIFileError("释放本地空间选项无效。")
        if type(cloud_size) is not int or cloud_size < 0:
            raise AIFileError("云端附件大小无效。")
        if type(cloud_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", cloud_sha256):
            raise AIFileError("云端附件哈希无效。")
        self._cloud_segments(cloud_path)
        row = self._get_row(attachment_id)
        if cloud_size != row.size or cloud_sha256 != row.sha256:
            raise AIFileError("云端附件校验结果与本地记录不一致。")
        path = self.resolve_controlled_copy(attachment_id)
        local_size, local_sha = self._hash_path(path)
        if local_size != row.size or local_sha != row.sha256:
            raise AIFileError("受控副本校验失败，已保留。")
        before = path.lstat()
        with Session(self.engine) as session, session.begin():
            current = session.get(AIManagedFile, attachment_id)
            if current is None or current.controlled_relpath != row.controlled_relpath:
                raise AIFileError("附件记录已变化，已保留受控副本。")
            current.cloud_provider = cloud_provider[:64]
            current.cloud_remote_id = cloud_remote_id[:512] if cloud_remote_id else None
            current.cloud_path = cloud_path
            current.cloud_size = cloud_size
            current.cloud_sha256 = cloud_sha256
            current.cloud_uploaded_at = datetime.now(timezone.utc)
        if not remove_local:
            return self.get(attachment_id)
        try:
            current_stat = path.lstat()
            if (
                stat.S_ISLNK(current_stat.st_mode)
                or not stat.S_ISREG(current_stat.st_mode)
                or (current_stat.st_dev, current_stat.st_ino, current_stat.st_mtime_ns)
                != (before.st_dev, before.st_ino, before.st_mtime_ns)
            ):
                raise AIFileError("受控副本在删除前发生变化，已保留。")
            path.unlink()
        except AIFileError:
            raise
        except OSError:
            raise AIFileError("无法安全删除受控副本，已保留。") from None
        with Session(self.engine) as session, session.begin():
            current = session.get(AIManagedFile, attachment_id)
            if current is None:
                raise AIFileError("附件不存在。")
            current.status = "cloud_only"
            current.controlled_relpath = None
            current.updated_at = datetime.now(timezone.utc)
        return self.get(attachment_id)

    def mark_cloud_only(
        self,
        attachment_id: int,
        *,
        cloud_path: str,
        cloud_size: int,
        cloud_sha256: str,
        cloud_remote_id: str | None = None,
        cloud_provider: str = "sjtu-pan",
    ) -> dict[str, Any]:
        return self.record_cloud_backup(
            attachment_id,
            cloud_path=cloud_path,
            cloud_size=cloud_size,
            cloud_sha256=cloud_sha256,
            cloud_remote_id=cloud_remote_id,
            cloud_provider=cloud_provider,
            remove_local=True,
        )

    def restore(self, attachment_id: int) -> dict[str, Any]:
        row = self._get_row(attachment_id)
        if row.status != "cloud_only":
            return attachment_dto(row)
        if self.provider_factory is None:
            raise AIFileError("云端附件恢复服务不可用。")
        provider = None
        temporary: Path | None = None
        try:
            provider = self.provider_factory()
            remote_path = self._cloud_segments(row.cloud_path)
            info = provider.get_info(remote_path)
            if info.is_directory or info.size != row.size:
                raise AIFileError("云端附件校验失败，无法恢复。")
            with provider.download_temp(remote_path, directory=self.managed_root / "objects") as downloaded:
                source = Path(downloaded)
                size, sha256 = self._hash_path(source)
                if size != row.size or sha256 != row.sha256:
                    raise AIFileError("云端附件哈希校验失败，无法恢复。")
                directory = self.managed_root / "objects" / sha256[:2]
                if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
                    raise AIFileError("AI 附件受控目录不安全。")
                directory.mkdir(mode=0o700, exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix=".restore-", dir=directory)
                os.close(descriptor)
                temporary = Path(name)
                shutil.copyfile(source, temporary)
                os.chmod(temporary, 0o600)
                target = directory / sha256
                os.replace(temporary, target)
                temporary = None
            relative = target.relative_to(self.managed_root).as_posix()
            with Session(self.engine) as session, session.begin():
                current = session.get(AIManagedFile, attachment_id)
                if current is None:
                    target.unlink(missing_ok=True)
                    raise AIFileError("附件不存在。")
                current.status = "local"
                current.controlled_relpath = relative
                current.updated_at = datetime.now(timezone.utc)
            return self.get(attachment_id)
        except AIFileError:
            raise
        except Exception:
            raise AIFileError("云端附件恢复失败，请检查网络后重试。") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def reveal(self, attachment_id: int, command_runner: Callable[..., Any]) -> dict[str, Any]:
        row = self._get_row(attachment_id)
        if row.status == "cloud_only":
            row_dto = self.restore(attachment_id)
        else:
            row_dto = attachment_dto(row)
        path = self.resolve_controlled_copy(attachment_id)
        completed = command_runner(["/usr/bin/open", "-R", str(path)], check=False)
        if getattr(completed, "returncode", 0) != 0:
            raise AIFileError("无法在 Finder 中显示附件。")
        return {"id": row_dto["id"], "status": "revealed", "attachment": row_dto}
