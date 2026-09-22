"""安全、可恢复的 Canvas 课程文件本地归档服务。"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, urljoin, urlparse

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.material_classifier import (
    CATEGORY_LABELS,
    category_label,
    classify_material,
    load_module_signals,
    safe_folder_chain,
)
from sjtu_learning_assistant.models import Course, CourseFile, CourseFolder, SyncState
from test_canvas import ensure_same_origin

DEFAULT_ARCHIVE_ROOT = Path.home() / "Documents" / "SJTU Study"
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
DOWNLOAD_ATTEMPTS = 3
MAX_REDIRECTS = 5
RETRY_DELAYS_SECONDS = (0.1, 0.5)
REDIRECT_STATUS_CODES = (301, 302, 303, 307, 308)
TERM_PATTERN = re.compile(r"^(\d{4})-(\d{4})\s+(Fall|Spring)$", re.IGNORECASE)
INVALID_PATH_CHARS = re.compile(r"[\x00-\x1f\x7f/:\\]+")
SPACE_RUN = re.compile(r"\s+")


class ArchiveError(RuntimeError):
    """可展示给用户的归档错误。"""


@dataclass(frozen=True)
class ArchiveFileContext:
    source_id: str
    course_name: str
    term_name: str
    display_name: str
    expected_size: int | None
    source_updated_at: datetime | None
    local_path: str | None
    download_status: str
    download_attempts: int
    downloaded_size: int | None
    downloaded_sha256: str | None
    downloaded_source_updated_at: datetime | None
    folder_names: tuple[str, ...]
    file_id: int = 0
    course_id: int = 0
    category: str = "other"


@dataclass(frozen=True)
class DownloadResult:
    source_id: str
    status: str
    local_path: str | None = None
    size: int | None = None
    sha256: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ArchiveSummary:
    term_name: str
    downloaded: int
    unchanged: int
    failed: int
    results: tuple[DownloadResult, ...]
    skipped: bool = False
    message: str | None = None


@dataclass(frozen=True)
class OrganizeSummary:
    moved: int
    unchanged: int
    failed: int
    results: tuple[DownloadResult, ...]


def derive_current_term(on_date: date | None = None) -> str:
    """按当前日期推导 Canvas 学年学期，例如 2026-09 -> 2026-2027 Fall。"""
    value = on_date or date.today()
    if value.month >= 8:
        return f"{value.year}-{value.year + 1} Fall"
    return f"{value.year - 1}-{value.year} Spring"


def normalize_term(value: str | None) -> str:
    if value is None:
        raise ArchiveError("课程缺少学期名称，无法归档。")
    normalized = SPACE_RUN.sub(" ", value.strip())
    match = TERM_PATTERN.fullmatch(normalized)
    if match is None or int(match.group(2)) != int(match.group(1)) + 1:
        raise ArchiveError(
            "学期格式必须为 YYYY-YYYY Fall 或 YYYY-YYYY Spring。"
        )
    return f"{match.group(1)}-{match.group(2)} {match.group(3).title()}"


def sanitize_component(value: str, *, fallback: str, max_length: int = 120) -> str:
    """清洗单个路径组件；绝不允许输入产生目录层级。"""
    cleaned = INVALID_PATH_CHARS.sub("_", str(value))
    cleaned = SPACE_RUN.sub(" ", cleaned).strip(" .")
    if cleaned in {"", ".", ".."}:
        cleaned = fallback
    if len(cleaned) > max_length:
        suffix = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:10]
        cleaned = f"{cleaned[: max_length - 13].rstrip()} [{suffix}]"
    return cleaned


def add_source_id_suffix(filename: str, source_id: str) -> str:
    path = Path(filename)
    safe_id = sanitize_component(source_id, fallback="unknown", max_length=48)
    if path.suffix:
        return f"{path.stem} [{safe_id}]{path.suffix}"
    return f"{filename} [{safe_id}]"


def ensure_within_root(root: Path, candidate: Path) -> Path:
    lexical_root = root.expanduser().absolute()
    lexical_candidate = candidate.expanduser().absolute()
    resolved_root = lexical_root.resolve(strict=False)
    try:
        lexical_candidate.relative_to(lexical_root)
        if lexical_candidate != lexical_root:
            # 只解析父目录：最终文件即使是指向 root 外的 symlink，也应交给
            # lstat/unlink 安全解除，绝不能因 resolve 而读取或写入其指向内容。
            lexical_candidate.parent.resolve(strict=False).relative_to(resolved_root)
    except ValueError as exc:
        raise ArchiveError("归档路径越过了 archive root，已拒绝写入。") from exc
    return lexical_candidate


def _path_mode(path: Path) -> int | None:
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None


def _is_regular_file(path: Path) -> bool:
    mode = _path_mode(path)
    return mode is not None and stat.S_ISREG(mode)


def _ensure_safe_directory(root: Path, directory: Path) -> Path:
    """创建 root 内目录，拒绝任何现存 symlink 或非目录组件。"""
    lexical_root = root.expanduser().absolute()
    lexical_directory = directory.expanduser().absolute()
    try:
        relative = lexical_directory.relative_to(lexical_root)
    except ValueError as exc:
        raise ArchiveError("归档目录越过了 archive root，已拒绝写入。") from exc

    components = list(reversed(relative.parents))
    components.append(relative)
    for component in components:
        candidate = lexical_root if component == Path() else lexical_root / component
        mode = _path_mode(candidate)
        if mode is None:
            candidate.mkdir()
            mode = _path_mode(candidate)
        if mode is not None and stat.S_ISLNK(mode):
            raise ArchiveError("归档目录包含 symlink，已拒绝写入。")
        if mode is None or not stat.S_ISDIR(mode):
            raise ArchiveError("归档目录路径包含非目录对象，已拒绝写入。")
    return ensure_within_root(lexical_root, lexical_directory)


def _remove_target_symlink(path: Path) -> None:
    mode = _path_mode(path)
    if mode is not None and stat.S_ISLNK(mode):
        path.unlink()


def _open_regular_readonly(path: Path):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ArchiveError("归档对象不是普通文件，已拒绝读取。")
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def _copy_regular_file(source: Path, destination: Path) -> None:
    """仅通过 no-follow 文件描述符复制普通文件。"""
    if _path_mode(destination) is not None:
        raise ArchiveError("版本归档目标已存在，已拒绝覆盖。")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with _open_regular_readonly(source) as input_stream:
        source_mode = stat.S_IMODE(os.fstat(input_stream.fileno()).st_mode)
        descriptor = os.open(destination, flags, source_mode or 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ArchiveError("版本归档目标不是普通文件。")
            with os.fdopen(descriptor, "wb") as output_stream:
                descriptor = -1
                while True:
                    chunk = input_stream.read(65536)
                    if not chunk:
                        break
                    output_stream.write(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            destination.unlink(missing_ok=True)
            raise


def _replace_with_regular_file(root: Path, source: Path, target: Path) -> None:
    """原子替换目标，且不跟随现存目标或父目录 symlink。"""
    _ensure_safe_directory(root, target.parent)
    if not _is_regular_file(source):
        raise ArchiveError("下载临时对象不是普通文件，已拒绝替换。")
    _remove_target_symlink(target)
    if _path_mode(target) is not None and not _is_regular_file(target):
        raise ArchiveError("目标路径已存在且不是普通文件。")
    os.replace(source, target)
    if not _is_regular_file(target):
        _remove_target_symlink(target)
        raise ArchiveError("原子替换结果不是普通文件，已安全移除。")


def _safe_error(exc: Exception) -> str:
    text = SPACE_RUN.sub(" ", str(exc)).strip()
    return text[:1000] or exc.__class__.__name__


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


class ArchiveService:
    """将数据库中的 Canvas 文件归档到受约束的本地目录。"""

    def __init__(
        self,
        engine: Engine,
        canvas_client: httpx.Client,
        *,
        archive_root: Path = DEFAULT_ARCHIVE_ROOT,
        current_term: str | None = None,
        organize_by_category: bool = True,
        active_course_source_ids: Iterable[str] | None = None,
        use_recent_active_courses: bool = False,
        download_client_factory: Callable[[], httpx.Client] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.engine = engine
        self.canvas_client = canvas_client
        self.archive_root = archive_root.expanduser()
        self.current_term = normalize_term(current_term) if current_term is not None else None
        self.requested_term = current_term is not None
        self.organize_by_category = organize_by_category
        self.active_course_source_ids = (
            {str(value) for value in active_course_source_ids}
            if active_course_source_ids is not None
            else None
        )
        self.use_recent_active_courses = use_recent_active_courses
        self.download_client_factory = download_client_factory or self._default_download_client
        self.sleeper = sleeper

    @staticmethod
    def _default_download_client() -> httpx.Client:
        # 此 client 故意不含 Canvas Authorization；重定向由本服务逐跳校验。
        return httpx.Client(
            headers={"User-Agent": "SJTU-Learning-Assistant/Phase-4A"},
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=False,
        )

    def archive_current_term(self) -> ArchiveSummary:
        """下载本轮 Canvas active 课程；无显式 active 集合时兼容学期匹配。"""
        if self.active_course_source_ids is not None or self.use_recent_active_courses:
            course_ids = self._active_course_ids()
            if not course_ids:
                return ArchiveSummary(
                    term_name=self.current_term or derive_current_term(),
                    downloaded=0,
                    unchanged=0,
                    failed=0,
                    results=(),
                    skipped=True,
                    message="本轮 Canvas 同步没有 active 课程，已安全跳过文件归档。",
                )
            contexts = self._load_contexts(course_ids=course_ids)
            # IDs come directly from Canvas fetch_active_courses and deliberately
            # take precedence over localised or imprecise term names.
            results = self._download_contexts(contexts)
            terms = sorted({item.term_name for item in contexts})
            label = terms[0] if len(terms) == 1 else "Canvas active courses"
            return ArchiveSummary(
                term_name=label,
                downloaded=sum(item.status == "downloaded" for item in results),
                unchanged=sum(item.status == "unchanged" for item in results),
                failed=sum(item.status == "failed" for item in results),
                results=tuple(results),
            )

        expected_term = self.current_term or derive_current_term()
        matches = self._matching_term_names(expected_term)
        if len(matches) != 1:
            if self.requested_term:
                if not matches:
                    raise ArchiveError("指定学期不存在：%s。未下载任何文件。" % expected_term)
                raise ArchiveError("指定学期无法唯一匹配：%s。未下载任何文件。" % expected_term)
            reason = (
                "未找到推导出的当前学期 %s" % expected_term
                if not matches
                else "推导出的当前学期 %s 匹配到多个名称" % expected_term
            )
            return ArchiveSummary(
                term_name=expected_term,
                downloaded=0,
                unchanged=0,
                failed=0,
                results=(),
                skipped=True,
                message="%s，已安全跳过文件归档。" % reason,
            )
        contexts = self._load_contexts(term_name=matches[0])
        results = self._download_contexts(contexts)
        return ArchiveSummary(
            term_name=expected_term,
            downloaded=sum(item.status == "downloaded" for item in results),
            unchanged=sum(item.status == "unchanged" for item in results),
            failed=sum(item.status == "failed" for item in results),
            results=tuple(results),
        )

    def _active_course_ids(self) -> set[int]:
        with Session(self.engine) as session:
            if self.active_course_source_ids is not None:
                return set(
                    session.scalars(
                        select(Course.id).where(
                            Course.source_id.in_(self.active_course_source_ids)
                        )
                    ).all()
                )
            latest = session.scalar(
                select(SyncState.last_success_at).where(
                    SyncState.source == "canvas",
                    SyncState.resource == "courses",
                    SyncState.status == "success",
                )
            )
            if latest is None:
                return set()
            return set(
                session.scalars(
                    select(Course.id).where(Course.updated_at == latest)
                ).all()
            )

    def _matching_term_names(self, expected_term: str) -> list[str]:
        with Session(self.engine) as session:
            names = session.scalars(
                select(Course.term_name)
                .where(Course.term_name.is_not(None))
                .distinct()
                .order_by(Course.term_name)
            ).all()
        matches = list()
        for name in names:
            try:
                if normalize_term(name) == expected_term:
                    matches.append(name)
            except ArchiveError:
                continue
        return matches

    def download_file_by_source_id(self, source_id: str) -> DownloadResult:
        """供未来 UI 调用：显式下载任意学期的一项文件。"""
        clean_source_id = str(source_id).strip()
        if not clean_source_id:
            raise ArchiveError("source_id 不能为空。")
        contexts = self._load_contexts(source_id=clean_source_id)
        if not contexts:
            raise ArchiveError(f"未找到 Canvas 文件 source_id={clean_source_id}。")
        # 对同课程同文件夹的文件整体规划名称，确保单文件下载也稳定处理重名。
        target_context = contexts[0]
        peers = self._load_contexts(course_ids={target_context.course_id})
        planned = self._planned_paths(peers)
        return self._download_one(target_context, planned[target_context.source_id])

    def _load_contexts(
        self,
        *,
        term_name: str | None = None,
        source_id: str | None = None,
        course_name: str | None = None,
        course_ids: set[int] | None = None,
    ) -> list[ArchiveFileContext]:
        with Session(self.engine) as session:
            statement = (
                select(CourseFile, Course)
                .join(Course, Course.id == CourseFile.course_id)
                .where(CourseFile.is_active.is_(True))
                .order_by(Course.id, CourseFile.id)
            )
            if term_name is not None:
                statement = statement.where(Course.term_name == term_name)
            if source_id is not None:
                statement = statement.where(CourseFile.source_id == source_id)
            if course_name is not None:
                statement = statement.where(Course.name == course_name)
            if course_ids is not None:
                if not course_ids:
                    return []
                statement = statement.where(Course.id.in_(course_ids))
            rows = session.execute(statement).all()
            course_ids = {file.course_id for file, _course in rows}
            folders = (
                session.scalars(
                    select(CourseFolder).where(
                        CourseFolder.course_id.in_(course_ids),
                        CourseFolder.is_active.is_(True),
                    )
                ).all()
                if course_ids
                else []
            )
            folder_by_id = {folder.id: folder for folder in folders}
            module_signals = load_module_signals(session, course_ids=course_ids)
            contexts: list[ArchiveFileContext] = []
            for file, course in rows:
                folder_names = self._folder_chain(file.folder_id, folder_by_id)
                module_names, module_item_names = module_signals.get(file.id, ((), ()))
                contexts.append(
                    ArchiveFileContext(
                        file_id=file.id,
                        course_id=file.course_id,
                        source_id=file.source_id,
                        course_name=course.name,
                        term_name=course.term_name or "Unknown Term",
                        display_name=file.display_name or file.filename or "unnamed-file",
                        expected_size=file.size,
                        source_updated_at=file.source_updated_at,
                        local_path=file.local_path,
                        download_status=file.download_status,
                        download_attempts=file.download_attempts,
                        downloaded_size=file.downloaded_size,
                        downloaded_sha256=file.download_sha256,
                        downloaded_source_updated_at=file.downloaded_source_updated_at,
                        folder_names=folder_names,
                        category=classify_material(
                            module_names=module_names,
                            module_item_names=module_item_names,
                            folder_names=reversed(folder_names),
                            filename=file.display_name or file.filename or "unnamed-file",
                        ),
                    )
                )
            return contexts

    @staticmethod
    def _folder_chain(
        folder_id: int | None, folder_by_id: dict[int, CourseFolder]
    ) -> tuple[str, ...]:
        return tuple(
            folder.name for folder in safe_folder_chain(folder_id, folder_by_id)
        )

    def _planned_paths(
        self, contexts: Iterable[ArchiveFileContext]
    ) -> dict[str, Path]:
        values = list(contexts)
        base_paths: dict[str, Path] = {}
        buckets: dict[str, list[str]] = {}
        root = self.archive_root.expanduser()
        for context in values:
            components = [
                sanitize_component(context.term_name, fallback="Unknown Term"),
                sanitize_component(context.course_name, fallback="Unnamed Course"),
                *(
                    [sanitize_component(category_label(context.category), fallback="其他")]
                    if self.organize_by_category
                    else []
                ),
                *(
                    sanitize_component(name, fallback="Unnamed Folder")
                    for name in context.folder_names
                ),
            ]
            filename = sanitize_component(
                context.display_name,
                fallback=f"file-{context.source_id}",
                max_length=180,
            )
            candidate = root.joinpath(*components, filename)
            candidate = ensure_within_root(root, candidate)
            base_paths[context.source_id] = candidate
            buckets.setdefault(os.path.normcase(str(candidate)).casefold(), []).append(context.source_id)

        planned = dict(base_paths)
        for source_ids in buckets.values():
            if len(source_ids) < 2:
                continue
            for source_id in source_ids:
                original = base_paths[source_id]
                planned[source_id] = ensure_within_root(
                    root,
                    original.with_name(add_source_id_suffix(original.name, source_id)),
                )
        return planned

    def organize_current_term(self) -> OrganizeSummary:
        """将最近 active 课程的已下载文件安全、幂等地整理到当前规划路径。"""
        if not self.organize_by_category:
            raise ArchiveError("按类别整理已关闭，未移动任何文件。")
        all_contexts = self._load_contexts(course_ids=self._active_course_ids())
        contexts = [
            item
            for item in all_contexts
            if item.download_status == "downloaded" and item.local_path
        ]
        planned = self._planned_paths(all_contexts)
        results: list[DownloadResult] = []
        for context in contexts:
            try:
                results.append(self._organize_one(context, planned[context.source_id]))
            except Exception as exc:
                results.append(
                    DownloadResult(
                        source_id=context.source_id,
                        status="failed",
                        error=_safe_error(exc),
                    )
                )
        return OrganizeSummary(
            moved=sum(item.status == "moved" for item in results),
            unchanged=sum(item.status == "unchanged" for item in results),
            failed=sum(item.status == "failed" for item in results),
            results=tuple(results),
        )

    @staticmethod
    def _file_digest(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with _open_regular_readonly(path) as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    @staticmethod
    def _is_allowed_system_alias(path: Path) -> bool:
        aliases = {
            Path("/var"): Path("/private/var"),
            Path("/tmp"): Path("/private/tmp"),
        }
        return path in aliases and path.resolve() == aliases[path]

    def _safe_source(self, path: Path) -> Path:
        source = path.expanduser().absolute()
        current = Path(source.anchor)
        for index, part in enumerate(source.parts[1:]):
            current /= part
            mode = _path_mode(current)
            if mode is None:
                raise ArchiveError("已下载文件不存在。")
            if stat.S_ISLNK(mode) and not self._is_allowed_system_alias(current):
                raise ArchiveError("已下载文件路径包含 symlink，已拒绝迁移。")
            if index < len(source.parts) - 2 and not (
                stat.S_ISDIR(mode) or self._is_allowed_system_alias(current)
            ):
                raise ArchiveError("已下载文件父路径包含非目录对象。")
        if not _is_regular_file(source):
            raise ArchiveError("已下载对象不是普通文件，已拒绝迁移。")
        return source

    def _source_archive_root(
        self, context: ArchiveFileContext, source: Path
    ) -> Path:
        """Infer the app-created old root from the deterministic stored path."""
        configured_root = self.archive_root.expanduser().absolute()
        try:
            source.relative_to(configured_root)
        except ValueError:
            pass
        else:
            return configured_root

        term = sanitize_component(context.term_name, fallback="Unknown Term")
        course = sanitize_component(context.course_name, fallback="Unnamed Course")
        folders = tuple(
            sanitize_component(name, fallback="Unnamed Folder")
            for name in context.folder_names
        )
        layouts = [(term, course, *folders)]
        layouts.extend(
            (term, course, label, *folders)
            for label in CATEGORY_LABELS.values()
        )
        parent_parts = source.parent.parts
        for layout in layouts:
            if tuple(parent_parts[-len(layout) :]) != layout:
                continue
            root_parts = parent_parts[: -len(layout)]
            root = Path(*root_parts)
            if root != Path(root.anchor):
                return root
        raise ArchiveError("已下载文件不符合归档目录结构，已拒绝迁移。")

    def _verified_content(
        self, context: ArchiveFileContext, path: Path
    ) -> tuple[int, str]:
        size, digest = self._file_digest(path)
        expected_size = (
            context.downloaded_size
            if context.downloaded_size is not None
            else context.expected_size
        )
        if expected_size is not None and size != expected_size:
            raise ArchiveError("已下载文件大小与数据库记录不一致。")
        if context.downloaded_sha256 is not None and digest != context.downloaded_sha256:
            raise ArchiveError("已下载文件 SHA-256 与数据库记录不一致。")
        return size, digest

    def _conflict_target(self, target: Path, context: ArchiveFileContext) -> Path:
        candidate = ensure_within_root(
            self.archive_root,
            target.with_name(add_source_id_suffix(target.name, context.source_id)),
        )
        if _path_mode(candidate) is None:
            return candidate
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return ensure_within_root(
            self.archive_root,
            candidate.with_name(
                f"{candidate.stem} (version {timestamp}){candidate.suffix}"
            ),
        )

    def _recovery_target(
        self, context: ArchiveFileContext, target: Path
    ) -> tuple[Path, int, str] | None:
        """Recover when a previous move completed before its DB transaction."""
        suffixed = target.with_name(add_source_id_suffix(target.name, context.source_id))
        candidates = [target, suffixed]
        if target.parent.is_dir():
            prefix = f"{suffixed.stem} (version "
            candidates.extend(
                child
                for child in target.parent.iterdir()
                if child.name.startswith(prefix) and child.suffix == suffixed.suffix
            )
        seen: set[Path] = set()
        for candidate in candidates:
            candidate = ensure_within_root(self.archive_root, candidate)
            if candidate in seen or not _is_regular_file(candidate):
                continue
            seen.add(candidate)
            try:
                size, digest = self._verified_content(context, candidate)
            except ArchiveError:
                continue
            return candidate, size, digest
        return None

    def _copy_across_volume(self, source: Path, target: Path) -> None:
        fd, name = tempfile.mkstemp(prefix=".sjtu-move-", dir=target.parent)
        os.close(fd)
        temporary = Path(name)
        temporary.unlink()
        try:
            _copy_regular_file(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _update_local_path(self, source_id: str, target: Path) -> None:
        with Session(self.engine) as session, session.begin():
            record = session.scalar(
                select(CourseFile)
                .where(CourseFile.source_id == source_id)
                .with_for_update()
            )
            if record is None:
                raise ArchiveError("文件元数据已不存在。")
            record.local_path = str(target)

    @staticmethod
    def _cleanup_empty_legacy_dirs(directory: Path, root: Path) -> None:
        root = root.expanduser().absolute()
        try:
            directory.absolute().relative_to(root)
        except ValueError:
            return
        current = directory.absolute()
        while current != root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _organize_one(self, context: ArchiveFileContext, target: Path) -> DownloadResult:
        source_path = Path(context.local_path or "")
        _ensure_safe_directory(self.archive_root, target.parent)
        target = ensure_within_root(self.archive_root, target)

        if _path_mode(source_path.expanduser().absolute()) is None:
            recovered = self._recovery_target(context, target)
            if recovered is None:
                raise ArchiveError("已下载文件不存在，且未找到可恢复的目标文件。")
            recovered_path, size, digest = recovered
            self._update_local_path(context.source_id, recovered_path)
            return DownloadResult(
                context.source_id, "unchanged", str(recovered_path), size, digest
            )

        source = self._safe_source(source_path)
        source_root = self._source_archive_root(context, source)
        size, digest = self._verified_content(context, source)
        if source == target:
            self._update_local_path(context.source_id, target)
            return DownloadResult(
                context.source_id, "unchanged", str(target), size, digest
            )

        final_target = target
        while _path_mode(final_target) is not None:
            if not _is_regular_file(final_target):
                raise ArchiveError("目标路径已存在且不是普通文件。")
            target_size, target_digest = self._file_digest(final_target)
            if target_size == size and target_digest == digest:
                self._update_local_path(context.source_id, final_target)
                try:
                    source.unlink()
                except OSError:
                    # The DB must not claim the duplicate target while the source
                    # could not be removed. Restore the old path when possible.
                    try:
                        self._update_local_path(context.source_id, source)
                    except Exception:
                        pass
                    raise
                self._cleanup_empty_legacy_dirs(source.parent, source_root)
                return DownloadResult(
                    context.source_id, "moved", str(final_target), size, digest
                )
            next_target = self._conflict_target(target, context)
            if next_target == final_target:
                raise ArchiveError("无法生成安全的冲突文件名。")
            final_target = next_target
            _ensure_safe_directory(self.archive_root, final_target.parent)

        renamed = False
        database_updated = False
        try:
            try:
                os.replace(source, final_target)
                renamed = True
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                self._copy_across_volume(source, final_target)
            try:
                self._update_local_path(context.source_id, final_target)
                database_updated = True
            except Exception:
                if renamed:
                    try:
                        os.replace(final_target, source)
                    except OSError:
                        pass
                raise
            if not renamed:
                try:
                    source.unlink()
                except OSError:
                    restored = False
                    try:
                        self._update_local_path(context.source_id, source)
                        restored = True
                    except Exception:
                        pass
                    if restored:
                        final_target.unlink(missing_ok=True)
                    raise
            self._cleanup_empty_legacy_dirs(source.parent, source_root)
            return DownloadResult(
                context.source_id, "moved", str(final_target), size, digest
            )
        except Exception:
            if (
                not renamed
                and not database_updated
                and _is_regular_file(source)
                and _is_regular_file(final_target)
            ):
                try:
                    target_size, target_digest = self._file_digest(final_target)
                    if target_size == size and target_digest == digest:
                        final_target.unlink()
                except Exception:
                    pass
            raise

    def _download_contexts(
        self, contexts: Iterable[ArchiveFileContext]
    ) -> list[DownloadResult]:
        values = list(contexts)
        planned = self._planned_paths(values)
        results: list[DownloadResult] = []
        for context in values:
            try:
                result = self._download_one(context, planned[context.source_id])
            except Exception as exc:  # 单文件失败必须与其他文件隔离。
                message = _safe_error(exc)
                try:
                    self._write_status(
                        context.source_id,
                        status="failed",
                        error=message,
                    )
                except Exception as status_exc:
                    message = f"{message}；状态写回失败：{_safe_error(status_exc)}"
                result = DownloadResult(
                    source_id=context.source_id,
                    status="failed",
                    error=message,
                )
            results.append(result)
        return results

    def _download_one(
        self, context: ArchiveFileContext, target: Path
    ) -> DownloadResult:
        _ensure_safe_directory(self.archive_root, target.parent)
        if self._is_unchanged(context, target):
            if context.local_path != str(target):
                self._write_status(
                    context.source_id,
                    status="downloaded",
                    local_path=str(target),
                    size=context.downloaded_size,
                    sha256=context.downloaded_sha256,
                    source_updated_at=context.source_updated_at,
                )
            return DownloadResult(
                source_id=context.source_id,
                status="unchanged",
                local_path=str(target),
                size=context.downloaded_size,
            )
        if context.expected_size is not None and context.expected_size > MAX_DOWNLOAD_BYTES:
            raise ArchiveError("文件超过 500 MiB 安全上限。")

        _remove_target_symlink(target)
        if _path_mode(target) is not None and not _is_regular_file(target):
            raise ArchiveError("目标路径已存在且不是普通文件。")
        last_error: Exception | None = None
        downloaded: tuple[Path, int, str] | None = None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            temporary_path: Path | None = None
            try:
                self._record_attempt(context.source_id)
                download_url = self._fetch_download_url(context.source_id)
                temporary_path, size, digest = self._stream_to_temporary(
                    download_url, target.parent
                )
                if context.expected_size is not None and size != context.expected_size:
                    raise ArchiveError(
                        f"下载大小不一致：期望 {context.expected_size}，实际 {size}。"
                    )
                downloaded = (temporary_path, size, digest)
                break
            except Exception as exc:
                last_error = exc
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
                if attempt < DOWNLOAD_ATTEMPTS:
                    self.sleeper(RETRY_DELAYS_SECONDS[attempt - 1])
        if downloaded is None:
            assert last_error is not None
            raise ArchiveError(
                f"下载重试 {DOWNLOAD_ATTEMPTS} 次后失败：{_safe_error(last_error)}"
            )

        temporary_path, size, digest = downloaded
        try:
            previous_path = target
            if context.local_path:
                try:
                    previous_path = ensure_within_root(
                        self.archive_root, Path(context.local_path)
                    )
                except ArchiveError:
                    # 归档根变化时绝不读取、移动或删除旧 root 之外的路径。
                    previous_path = target
            _remove_target_symlink(target)
            if _path_mode(target) is not None and not _is_regular_file(target):
                raise ArchiveError("目标路径已存在且不是普通文件。")
            if previous_path != target and _path_mode(previous_path) is not None:
                _ensure_safe_directory(self.archive_root, previous_path.parent)
            if _is_regular_file(previous_path):
                self._preserve_old_version(previous_path, context.source_id)
            elif _path_mode(previous_path) is not None:
                if previous_path.is_symlink():
                    previous_path.unlink()
                else:
                    raise ArchiveError("旧归档路径不是普通文件，已拒绝处理。")
            _replace_with_regular_file(self.archive_root, temporary_path, target)
            if previous_path != target and _is_regular_file(previous_path):
                previous_path.unlink()
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

        self._write_status(
            context.source_id,
            status="downloaded",
            local_path=str(target),
            size=size,
            sha256=digest,
            source_updated_at=context.source_updated_at,
            error=None,
        )
        return DownloadResult(
            source_id=context.source_id,
            status="downloaded",
            local_path=str(target),
            size=size,
            sha256=digest,
        )

    @staticmethod
    def _is_unchanged(context: ArchiveFileContext, target: Path) -> bool:
        if context.download_status != "downloaded" or not _is_regular_file(target):
            return False
        if context.downloaded_size is None or target.lstat().st_size != context.downloaded_size:
            return False
        if context.downloaded_source_updated_at != context.source_updated_at:
            return False
        if context.downloaded_sha256 is None:
            return False
        digest = hashlib.sha256()
        with _open_regular_readonly(target) as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest() == context.downloaded_sha256

    def _fetch_download_url(self, source_id: str) -> str:
        api_path = f"/api/v1/files/{quote(source_id, safe="")}"
        ensure_same_origin(self.canvas_client, api_path)
        try:
            response = self.canvas_client.get(api_path)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ArchiveError(f"无法获取 Canvas 文件下载地址：{exc}") from exc
        if not isinstance(payload, dict):
            raise ArchiveError("Canvas 文件详情返回格式无效。")
        download_url = payload.get("url")
        if not isinstance(download_url, str) or not _is_https_url(download_url):
            raise ArchiveError("Canvas 文件详情未提供有效 HTTPS 下载地址。")
        return download_url

    def _stream_to_temporary(
        self, download_url: str, directory: Path
    ) -> tuple[Path, int, str]:
        fd, temporary_name = tempfile.mkstemp(prefix=".sjtu-download-", dir=directory)
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as output, self.download_client_factory() as client:
                # 强制移除凭据；每个重定向目标均在发请求之前重新校验。
                client.headers.pop("Authorization", None)
                current_url = download_url
                for redirect_count in range(MAX_REDIRECTS + 1):
                    if not _is_https_url(current_url):
                        raise ArchiveError("下载或重定向目标不是有效 HTTPS URL。")
                    with client.stream(
                        "GET", current_url, follow_redirects=False
                    ) as response:
                        if response.status_code in REDIRECT_STATUS_CODES:
                            location = response.headers.get("location")
                            if not location:
                                raise ArchiveError("下载重定向缺少 Location。")
                            next_url = urljoin(str(response.request.url), location)
                            if not _is_https_url(next_url):
                                raise ArchiveError("下载重定向目标不是有效 HTTPS URL。")
                            if redirect_count >= MAX_REDIRECTS:
                                raise ArchiveError("下载重定向次数超过安全上限。")
                            current_url = next_url
                            continue

                        response.raise_for_status()
                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                announced_size = int(content_length)
                            except ValueError as exc:
                                raise ArchiveError(
                                    "下载响应 Content-Length 无效。"
                                ) from exc
                            if announced_size > MAX_DOWNLOAD_BYTES:
                                raise ArchiveError("文件超过 500 MiB 安全上限。")
                        for chunk in response.iter_bytes(chunk_size=65536):
                            size += len(chunk)
                            if size > MAX_DOWNLOAD_BYTES:
                                raise ArchiveError("文件超过 500 MiB 安全上限。")
                            digest.update(chunk)
                            output.write(chunk)
                        break
                output.flush()
                os.fsync(output.fileno())
            return temporary_path, size, digest.hexdigest()
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def _preserve_old_version(self, target: Path, source_id: str) -> None:
        if not _is_regular_file(target):
            raise ArchiveError("旧归档对象不是普通文件，已拒绝保留版本。")
        versions_dir = ensure_within_root(self.archive_root, target.parent / ".versions")
        _ensure_safe_directory(self.archive_root, versions_dir)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        version_name = add_source_id_suffix(
            "%s (version %s)%s" % (target.stem, timestamp, target.suffix), source_id
        )
        version_path = ensure_within_root(
            self.archive_root, versions_dir / version_name
        )
        if _path_mode(version_path) is not None:
            raise ArchiveError("版本归档目标已存在，已拒绝覆盖。")
        _copy_regular_file(target, version_path)

    def _record_attempt(self, source_id: str) -> None:
        """每次实际下载尝试前累计计数；成功后保留历史累计值。"""
        with Session(self.engine) as session, session.begin():
            record = session.scalar(
                select(CourseFile)
                .where(CourseFile.source_id == source_id)
                .with_for_update()
            )
            if record is None:
                raise ArchiveError("文件元数据已不存在：%s" % source_id)
            record.download_attempts += 1
            record.download_status = "pending"
            record.download_error = None

    def _write_status(
        self,
        source_id: str,
        *,
        status: str,
        local_path: str | None = None,
        size: int | None = None,
        sha256: str | None = None,
        source_updated_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            record = session.scalar(
                select(CourseFile).where(CourseFile.source_id == source_id).with_for_update()
            )
            if record is None:
                raise ArchiveError(f"文件元数据已不存在：{source_id}")
            record.download_status = status
            record.download_error = error
            if status == "downloaded":
                record.local_path = local_path
                record.downloaded_size = size
                record.download_sha256 = sha256
                record.downloaded_at = now
                record.downloaded_source_updated_at = source_updated_at
