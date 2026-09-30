"""Incremental, path-safe backup of local Canvas and mail files to SJTU Pan."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator, Literal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.ai_attachments import AIManagedFileService, MANAGED_DIRECTORY_NAME
from sjtu_learning_assistant.cloud_storage import (
    CloudConflictError,
    CloudStorageProvider,
    SJTUCloudPanProvider,
    user_token_saved,
)
from sjtu_learning_assistant.material_classifier import (
    archive_folder_names,
    category_label,
    load_module_signals,
)
from sjtu_learning_assistant.material_tree import material_placement
from sjtu_learning_assistant.models import (
    AIManagedFile,
    Course,
    CourseFile,
    CourseFolder,
    Email,
    EmailAttachment,
)
from sjtu_learning_assistant.transcript_service import (
    ARTIFACT_FILES as TRANSCRIPT_V1_ARTIFACT_FILES,
    DEFAULT_TRANSCRIPT_ROOT,
    MANIFEST_SCHEMA_VERSION as TRANSCRIPT_MANIFEST_SCHEMA_VERSION,
    V2_ARTIFACT_FILES as TRANSCRIPT_V2_ARTIFACT_FILES,
    V2_MANIFEST_SCHEMA_VERSION as TRANSCRIPT_V2_MANIFEST_SCHEMA_VERSION,
)
from sjtu_learning_assistant.video_service import SJTUVideoError, parse_remote_source_id

BACKUP_ROOT = "SJTU Learning Assistant"
DEFAULT_MULTIPART_THRESHOLD = 8 * 1024 * 1024
MAX_COMPONENT_LENGTH = 120
MAX_FILENAME_LENGTH = 200
MAX_FAILURE_DETAILS = 100
MAX_TRANSCRIPT_MANIFEST_SIZE = 2 * 1024 * 1024
TRANSCRIPT_DIRECTORY = "Transcripts"
_HEX_DIGITS = frozenset("0123456789abcdef")


ProgressCallback = Callable[[dict[str, Any]], None]


class BackupError(RuntimeError):
    """A bounded, user-displayable backup error without local or remote secrets."""


@dataclass(frozen=True)
class BackupCandidate:
    key: str
    source: Literal["canvas", "mail", "ai", "transcript"]
    remote_path: tuple[str, ...]
    local_path: str | None = field(repr=False)
    local_root: Path = field(repr=False)
    ready: bool
    record_id: int | None = None
    cloud_path: str | None = None
    cloud_size: int | None = None
    sha256: str | None = field(default=None, repr=False)
    expected_size: int | None = field(default=None, repr=False)
    transcript_group: str | None = field(default=None, repr=False)
    transcript_manifest: bool = field(default=False, repr=False)

    @property
    def cloud_only(self) -> bool:
        return not self.ready and bool(self.cloud_path) and self.cloud_size is not None

    def dto(self) -> dict[str, Any]:
        return {
            "id": self.key,
            "source": self.source,
            "remote_path": list(self.remote_path),
            "ready": self.ready,
        }


def safe_path_component(
    value: object,
    *,
    fallback: str = "未命名",
    limit: int = MAX_COMPONENT_LENGTH,
) -> str:
    """Create one bounded remote component; separators and controls never survive."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    cleaned = "".join(
        "_" if character in {"/", "\\"} or ord(character) < 32 or ord(character) == 127 else character
        for character in text
    )
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = fallback
    cleaned = cleaned[:limit].rstrip(" .") or fallback
    if cleaned in {".", ".."}:
        cleaned = fallback
    return cleaned


def stable_filename(value: object, identity: str) -> str:
    """Return a readable filename with a stable identity suffix for collision safety."""
    clean = safe_path_component(value, fallback="文件", limit=MAX_FILENAME_LENGTH)
    suffix = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:12]
    path = Path(clean)
    extension = path.suffix[:32] if path.suffix and path.name != path.suffix else ""
    stem_limit = MAX_FILENAME_LENGTH - len(extension) - len(suffix) - 2
    stem = (path.stem if extension else clean)[: max(stem_limit, 1)].rstrip(" .") or "文件"
    return f"{stem}--{suffix}{extension}"


def stable_directory(value: object, identity: str, *, fallback: str) -> str:
    """Return a readable, collision-safe remote directory component."""
    suffix = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:12]
    clean = safe_path_component(
        value,
        fallback=fallback,
        limit=MAX_COMPONENT_LENGTH - len(suffix) - 2,
    )
    return f"{clean}--{suffix}"


def _valid_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in _HEX_DIGITS for character in value)
    )


def _valid_opaque_id(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 32
        and all(character in _HEX_DIGITS for character in value)
    )


def _controlled_digest(
    root: Path,
    raw_path: str,
    *,
    maximum_size: int | None = None,
) -> tuple[str, int, bytes | None]:
    digest = hashlib.sha256()
    payload = bytearray() if maximum_size is not None else None
    with open_controlled_file(root, raw_path) as (stream, size):
        if maximum_size is not None and size > maximum_size:
            raise BackupError("字幕清单过大或格式无效。")
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            if payload is not None:
                payload.extend(chunk)
    return digest.hexdigest(), size, bytes(payload) if payload is not None else None


def _controlled_json(root: Path, raw_path: str) -> tuple[dict[str, Any], str, int]:
    digest, size, payload = _controlled_digest(
        root,
        raw_path,
        maximum_size=MAX_TRANSCRIPT_MANIFEST_SIZE,
    )
    try:
        value = json.loads((payload or b"").decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise BackupError("字幕清单过大或格式无效。") from None
    if type(value) is not dict:
        raise BackupError("字幕清单过大或格式无效。")
    return value, digest, size


def canvas_remote_path(
    *,
    term_name: str | None,
    course_name: str | None,
    category: str,
    folder_names: tuple[str, ...],
    filename: str,
    identity: str,
) -> tuple[str, ...]:
    """Build the Canvas cloud path from the same normalized tree placement."""
    return (
        BACKUP_ROOT,
        "Canvas",
        safe_path_component(term_name, fallback="未分学期"),
        safe_path_component(course_name, fallback="未命名课程"),
        safe_path_component(category_label(category), fallback="其他"),
        *(
            safe_path_component(name, fallback="未命名目录")
            for name in archive_folder_names(category, folder_names)
        ),
        stable_filename(filename, identity),
    )


def _candidate_key(source: str, identity: str) -> str:
    digest = hashlib.sha256(f"{source}:{identity}".encode("utf-8", errors="replace")).hexdigest()
    return digest[:20]


def _month(value: datetime | None) -> str:
    if value is None:
        return "unknown-month"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m")


def _lexical_root(root: Path) -> Path:
    return Path(os.path.abspath(os.fspath(root.expanduser())))


def _relative_local_path(root: Path, raw_path: str | None) -> tuple[Path, tuple[str, ...]]:
    if not raw_path or "\x00" in raw_path:
        raise BackupError("本地备份文件缺失或不安全。")
    lexical_root = _lexical_root(root)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = lexical_root / candidate
    lexical_candidate = Path(os.path.abspath(os.fspath(candidate)))
    try:
        relative = lexical_candidate.relative_to(lexical_root)
    except ValueError:
        raise BackupError("本地备份文件缺失或不安全。") from None
    if not relative.parts:
        raise BackupError("本地备份文件缺失或不安全。")
    return lexical_root, relative.parts


@contextmanager
def open_controlled_file(root: Path, raw_path: str | None) -> Iterator[tuple[BinaryIO, int]]:
    """Open a regular file beneath root using no-follow directory descriptors."""
    lexical_root, parts = _relative_local_path(root, raw_path)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    descriptors: list[int] = []
    stream: BinaryIO | None = None
    try:
        root_mode = lexical_root.lstat().st_mode
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            raise BackupError("本地备份文件缺失或不安全。")
        current_fd = os.open(lexical_root, directory_flags)
        descriptors.append(current_fd)
        for component in parts[:-1]:
            current_fd = os.open(component, directory_flags, dir_fd=current_fd)
            descriptors.append(current_fd)
        file_fd = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=current_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(file_fd)
            raise BackupError("本地备份文件缺失或不安全。")
        stream = os.fdopen(file_fd, "rb")
        yield stream, int(info.st_size)
    except BackupError:
        raise
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, ValueError):
        raise BackupError("本地备份文件缺失或不安全。") from None
    finally:
        if stream is not None:
            stream.close()
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _is_ready(root: Path, raw_path: str | None) -> bool:
    try:
        with open_controlled_file(root, raw_path):
            return True
    except BackupError:
        return False


def remove_controlled_file(
    root: Path,
    raw_path: str | None,
    expected_size: int,
    *,
    expected_identity: tuple[int, int, int] | None = None,
) -> None:
    """Unlink only the same regular non-symlink file verified beneath ``root``."""
    lexical_root, parts = _relative_local_path(root, raw_path)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    descriptors: list[int] = []
    file_fd: int | None = None
    try:
        root_mode = lexical_root.lstat().st_mode
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            raise BackupError("本地备份文件缺失或不安全。")
        current_fd = os.open(lexical_root, directory_flags)
        descriptors.append(current_fd)
        for component in parts[:-1]:
            current_fd = os.open(component, directory_flags, dir_fd=current_fd)
            descriptors.append(current_fd)
        file_fd = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=current_fd)
        opened = os.fstat(file_fd)
        current = os.stat(parts[-1], dir_fd=current_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or opened.st_size != expected_size
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            or (
                expected_identity is not None
                and (opened.st_dev, opened.st_ino, opened.st_mtime_ns)
                != expected_identity
            )
        ):
            raise BackupError("本地备份文件在删除前发生变化，已保留。")
        os.unlink(parts[-1], dir_fd=current_fd)
    except BackupError:
        raise
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, ValueError):
        raise BackupError("无法安全删除本地备份文件，已保留记录。") from None
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def preview_counts(candidates: tuple[BackupCandidate, ...]) -> dict[str, int]:
    canvas = sum(candidate.source == "canvas" for candidate in candidates)
    mail = sum(candidate.source == "mail" for candidate in candidates)
    ready = sum(candidate.ready for candidate in candidates)
    cloud_only = sum(candidate.cloud_only for candidate in candidates)
    return {
        "canvas": canvas,
        "mail": mail,
        "ready": ready,
        "cloud_only": cloud_only,
        "missing_local": len(candidates) - ready - cloud_only,
        "total": len(candidates),
    }


class BackupService:
    """Scans with short-lived sessions and uploads each candidate independently."""

    def __init__(
        self,
        engine: Engine,
        provider: CloudStorageProvider | None,
        *,
        archive_root: Path,
        mail_attachments_root: Path,
        mail_account: str = "",
        multipart_threshold: int = DEFAULT_MULTIPART_THRESHOLD,
        ai_file_service: AIManagedFileService | None = None,
        transcript_root: Path | None = None,
    ) -> None:
        if multipart_threshold <= 0:
            raise ValueError("分片上传阈值必须为正数。")
        self.engine = engine
        self.provider = provider
        self.archive_root = Path(archive_root)
        self.mail_attachments_root = Path(mail_attachments_root)
        self.mail_account = safe_path_component(mail_account, fallback="default")
        self.multipart_threshold = multipart_threshold
        self.transcript_root = None if transcript_root is None else Path(transcript_root)
        self.ai_file_service = ai_file_service or AIManagedFileService(
            engine, archive_root=self.archive_root
        )

    def _transcript_candidate(
        self,
        *,
        video_directory: str,
        version: str,
        filename: str,
        remote_directory: tuple[str, ...],
        expected_sha256: str,
        expected_size: int,
        manifest: bool = False,
    ) -> BackupCandidate:
        relative = "/".join(
            ("videos", video_directory, *(('v2',) if version == "v2" else ()), filename)
        )
        group = f"{video_directory}:{version}"
        return BackupCandidate(
            key=_candidate_key("transcript", f"{group}:{filename}"),
            source="transcript",
            remote_path=(*remote_directory, version, filename),
            local_path=relative,
            local_root=self.transcript_root,
            ready=True,
            sha256=expected_sha256,
            expected_size=expected_size,
            transcript_group=group,
            transcript_manifest=manifest,
        )

    def _validated_transcript_artifact(
        self,
        *,
        video_directory: str,
        version: str,
        filename: str,
        expected_sha256: object,
        expected_size: object = None,
    ) -> tuple[str, int] | None:
        if not _valid_sha256(expected_sha256):
            return None
        relative = "/".join(
            ("videos", video_directory, *(('v2',) if version == "v2" else ()), filename)
        )
        try:
            actual_sha256, actual_size, _payload = _controlled_digest(
                self.transcript_root, relative
            )
        except BackupError:
            return None
        if actual_sha256 != expected_sha256:
            return None
        if expected_size is not None and (
            type(expected_size) is not int
            or expected_size < 0
            or actual_size != expected_size
        ):
            return None
        return actual_sha256, actual_size

    def _scan_transcript_video(self, video_directory: str) -> tuple[BackupCandidate, ...]:
        manifest_relative = f"videos/{video_directory}/manifest.json"
        try:
            manifest, manifest_sha256, manifest_size = _controlled_json(
                self.transcript_root, manifest_relative
            )
        except BackupError:
            return ()
        if manifest.get("schema_version") != TRANSCRIPT_MANIFEST_SCHEMA_VERSION:
            return ()
        source = manifest.get("source")
        course = manifest.get("course")
        video = manifest.get("video")
        records = manifest.get("artifacts")
        if not all(type(value) is dict for value in (source, course, video, records)):
            return ()
        source_id = source.get("source_id")
        course_id = course.get("id")
        if (
            type(source_id) is not str
            or not source_id
            or course_id is None
            or video_directory != hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:32]
        ):
            return ()
        try:
            source_course_id, source_video_id = parse_remote_source_id(source_id)
        except SJTUVideoError:
            return ()
        if str(course_id) != source_course_id:
            return ()
        remote_directory = (
            BACKUP_ROOT,
            TRANSCRIPT_DIRECTORY,
            stable_directory(course.get("name"), str(course_id), fallback="未命名课程"),
            stable_directory(video.get("title"), source_id, fallback="未命名录像"),
        )
        v1_candidates: list[BackupCandidate] = []
        for kind, filename in TRANSCRIPT_V1_ARTIFACT_FILES.items():
            record = records.get(kind)
            if record is None:
                continue
            if type(record) is not dict or record.get("path") != filename:
                return ()
            validated = self._validated_transcript_artifact(
                video_directory=video_directory,
                version="v1",
                filename=filename,
                expected_sha256=record.get("sha256"),
                expected_size=record.get("size"),
            )
            if validated is None:
                return ()
            actual_sha256, actual_size = validated
            v1_candidates.append(
                self._transcript_candidate(
                    video_directory=video_directory,
                    version="v1",
                    filename=filename,
                    remote_directory=remote_directory,
                    expected_sha256=actual_sha256,
                    expected_size=actual_size,
                )
            )

        v2_candidates: list[BackupCandidate] = []
        v2_manifest_relative = f"videos/{video_directory}/v2/manifest.json"
        try:
            v2_manifest, v2_manifest_sha256, v2_manifest_size = _controlled_json(
                self.transcript_root, v2_manifest_relative
            )
        except BackupError:
            v2_manifest = None
        if v2_manifest is not None:
            v2_records = v2_manifest.get("artifacts")
            v2_valid = bool(
                v2_manifest.get("schema_version")
                == TRANSCRIPT_V2_MANIFEST_SCHEMA_VERSION
                and v2_manifest.get("course_id") == source_course_id
                and v2_manifest.get("video_id") == source_video_id
                and type(v2_records) is dict
                and not (set(v2_records) - set(TRANSCRIPT_V2_ARTIFACT_FILES))
            )
            if v2_valid:
                assert type(v2_records) is dict
                for kind, filename in TRANSCRIPT_V2_ARTIFACT_FILES.items():
                    record = v2_records.get(kind)
                    if record is None:
                        continue
                    if (
                        type(record) is not dict
                        or set(record) != {"id", "path", "sha256", "size"}
                        or not _valid_opaque_id(record.get("id"))
                        or record.get("path") != filename
                    ):
                        v2_valid = False
                        break
                    validated = self._validated_transcript_artifact(
                        video_directory=video_directory,
                        version="v2",
                        filename=filename,
                        expected_sha256=record.get("sha256"),
                        expected_size=record.get("size"),
                    )
                    if validated is None:
                        v2_valid = False
                        break
                    actual_sha256, actual_size = validated
                    v2_candidates.append(
                        self._transcript_candidate(
                            video_directory=video_directory,
                            version="v2",
                            filename=filename,
                            remote_directory=remote_directory,
                            expected_sha256=actual_sha256,
                            expected_size=actual_size,
                        )
                    )
            if v2_valid:
                v2_candidates.append(
                    self._transcript_candidate(
                        video_directory=video_directory,
                        version="v2",
                        filename="manifest.json",
                        remote_directory=remote_directory,
                        expected_sha256=v2_manifest_sha256,
                        expected_size=v2_manifest_size,
                        manifest=True,
                    )
                )
            else:
                v2_candidates.clear()

        v1_candidates.append(
            self._transcript_candidate(
                video_directory=video_directory,
                version="v1",
                filename="manifest.json",
                remote_directory=remote_directory,
                expected_sha256=manifest_sha256,
                expected_size=manifest_size,
                manifest=True,
            )
        )
        return tuple((*v1_candidates[:-1], *v2_candidates, v1_candidates[-1]))

    def _scan_transcripts(self) -> tuple[BackupCandidate, ...]:
        if self.transcript_root is None:
            return ()
        videos = self.transcript_root / "videos"
        try:
            if (
                self.transcript_root.is_symlink()
                or not self.transcript_root.is_dir()
                or videos.is_symlink()
                or not videos.is_dir()
            ):
                return ()
            names = sorted(
                entry.name
                for entry in videos.iterdir()
                if len(entry.name) == 32
                and all(character in _HEX_DIGITS for character in entry.name)
                and not entry.is_symlink()
                and entry.is_dir()
            )
        except OSError:
            return ()
        candidates: list[BackupCandidate] = []
        for name in names:
            candidates.extend(self._scan_transcript_video(name))
        return tuple(candidates)

    def scan(self) -> tuple[BackupCandidate, ...]:
        """Read candidate values and material placement in one short-lived session."""
        candidates: list[BackupCandidate] = []
        with Session(self.engine) as session:
            canvas_rows = session.execute(
                select(CourseFile, Course)
                .join(Course, Course.id == CourseFile.course_id)
                .where(CourseFile.is_active.is_(True))
                .order_by(CourseFile.source_id)
            ).all()
            course_ids = {file.course_id for file, _course in canvas_rows}
            folders = (
                session.scalars(
                    select(CourseFolder).where(
                        CourseFolder.course_id.in_(course_ids),
                        CourseFolder.is_active.is_(True),
                    )
                ).all()
                if course_ids
                else ()
            )
            folder_by_id = {folder.id: folder for folder in folders}
            module_signals = load_module_signals(session, course_ids=course_ids)
            for file, course in canvas_rows:
                identity = str(file.source_id)
                category, chain, _manual = material_placement(
                    file, folder_by_id, module_signals
                )
                remote_path = canvas_remote_path(
                    term_name=course.term_name,
                    course_name=course.name,
                    category=category,
                    folder_names=tuple(folder.name for folder in chain),
                    filename=file.display_name or file.filename,
                    identity=identity,
                )
                candidates.append(
                    BackupCandidate(
                        key=_candidate_key("canvas", identity),
                        source="canvas",
                        remote_path=remote_path,
                        local_path=file.local_path,
                        local_root=self.archive_root,
                        ready=_is_ready(self.archive_root, file.local_path),
                        record_id=file.id,
                        cloud_path=file.cloud_path,
                        cloud_size=file.cloud_size,
                    )
                )

            mail_rows = session.execute(
                select(EmailAttachment, Email)
                .join(Email, Email.id == EmailAttachment.email_id)
                .order_by(Email.source_id, EmailAttachment.resource_id)
            ).all()
            for attachment, email in mail_rows:
                identity = f"{email.source_id}:{attachment.resource_id}"
                remote_path = (
                    BACKUP_ROOT,
                    "Mail",
                    self.mail_account,
                    _month(email.sent_at or email.received_at or email.created_at),
                    stable_filename(attachment.filename, identity),
                )
                candidates.append(
                    BackupCandidate(
                        key=_candidate_key("mail", identity),
                        source="mail",
                        remote_path=remote_path,
                        local_path=attachment.local_path,
                        local_root=self.mail_attachments_root,
                        ready=_is_ready(
                            self.mail_attachments_root, attachment.local_path
                        ),
                        record_id=attachment.id,
                        cloud_path=attachment.cloud_path,
                        cloud_size=attachment.cloud_size,
                    )
                )

            managed_root = self.ai_file_service.managed_root
            ai_rows = session.scalars(
                select(AIManagedFile).order_by(AIManagedFile.id)
            ).all()
            for attachment in ai_rows:
                remote_path = (
                    BACKUP_ROOT,
                    "AI Attachments",
                    attachment.sha256[:2],
                    stable_filename(attachment.name, attachment.sha256),
                )
                candidates.append(
                    BackupCandidate(
                        key=_candidate_key("ai", str(attachment.id)),
                        source="ai",
                        remote_path=remote_path,
                        local_path=attachment.controlled_relpath,
                        local_root=managed_root,
                        ready=_is_ready(managed_root, attachment.controlled_relpath),
                        record_id=attachment.id,
                        cloud_path=attachment.cloud_path,
                        cloud_size=attachment.cloud_size,
                        sha256=attachment.sha256,
                    )
                )
        candidates.extend(self._scan_transcripts())
        return tuple(candidates)

    def preview(self) -> dict[str, int]:
        return preview_counts(self.scan())

    def _ensure_directory(self, path: tuple[str, ...]) -> None:
        assert self.provider is not None
        ensure = getattr(self.provider, "ensure_directory", None)
        if callable(ensure):
            ensure(path)
            return
        current: list[str] = []
        for component in path:
            current.append(component)
            try:
                self.provider.create_directory(tuple(current))
            except CloudConflictError:
                continue

    def _persist_cloud_metadata(
        self, candidate: BackupCandidate, *, size: int, backed_up_at: datetime
    ) -> bool:
        if candidate.source in {"ai", "transcript"}:
            return False
        if candidate.record_id is None:
            return False
        model = CourseFile if candidate.source == "canvas" else EmailAttachment
        with Session(self.engine) as session, session.begin():
            record = session.scalar(
                select(model).where(model.id == candidate.record_id).with_for_update()
            )
            if record is None or record.local_path != candidate.local_path:
                raise BackupError("文件数据库记录已变化，已保留本地文件。")
            record.cloud_path = "/".join(candidate.remote_path)
            record.cloud_size = size
            record.cloud_backed_up_at = backed_up_at
        return True

    def _clear_local_reference(self, candidate: BackupCandidate) -> None:
        if candidate.source in {"ai", "transcript"}:
            return
        if candidate.record_id is None:
            return
        model = CourseFile if candidate.source == "canvas" else EmailAttachment
        with Session(self.engine) as session, session.begin():
            record = session.scalar(
                select(model).where(model.id == candidate.record_id).with_for_update()
            )
            if record is None:
                raise BackupError("文件数据库记录已变化。")
            if record.local_path == candidate.local_path:
                record.local_path = None
                if candidate.source == "canvas":
                    record.download_status = "cloud_only"

    def _remote_digest(self, candidate: BackupCandidate) -> tuple[str, int]:
        assert self.provider is not None
        digest = hashlib.sha256()
        downloaded_size = 0
        with self.provider.download_temp(candidate.remote_path) as downloaded:
            with Path(downloaded).open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    downloaded_size += len(chunk)
                    digest.update(chunk)
        return digest.hexdigest(), downloaded_size

    def _verify_remote_sha256(
        self,
        candidate: BackupCandidate,
        *,
        expected_sha256: str,
        expected_size: int,
    ) -> None:
        actual_sha256, downloaded_size = self._remote_digest(candidate)
        if downloaded_size != expected_size or actual_sha256 != expected_sha256:
            if candidate.source == "transcript":
                raise BackupError("云端字幕工件哈希校验失败，已保留本地文件。")
            raise BackupError("云端附件哈希校验失败，已保留受控副本。")

    def backup(
        self,
        candidates: tuple[BackupCandidate, ...] | None = None,
        *,
        remove_local: bool = False,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if type(remove_local) is not bool:
            raise BackupError("释放本地空间选项无效。")
        if self.provider is None:
            raise BackupError("交大云盘备份服务未配置。")
        selected = candidates if candidates is not None else self.scan()
        started_at = datetime.now(timezone.utc).isoformat()
        result: dict[str, Any] = {
            "started_at": started_at,
            "finished_at": None,
            "uploaded": 0,
            "skipped_existing": 0,
            "skipped_missing_local": 0,
            "local_removed": 0,
            "failed": 0,
            "failures": [],
        }
        total = len(selected)
        done = 0
        ensured: set[tuple[str, ...]] = set()
        failed_transcript_groups: set[str] = set()

        def report(current_name: str | None) -> None:
            if progress_callback is None:
                return
            progress_callback({"done": done, "total": total, "current_name": current_name})

        report(None)
        for candidate in selected:
            if cancel_event is not None and cancel_event.is_set():
                break
            current_name = safe_path_component(
                candidate.remote_path[-1] if candidate.remote_path else "文件",
                fallback="文件",
                limit=MAX_FILENAME_LENGTH,
            )
            report(current_name)
            try:
                if (
                    candidate.transcript_manifest
                    and candidate.transcript_group in failed_transcript_groups
                ):
                    continue
                if not candidate.ready:
                    if candidate.cloud_only:
                        continue
                    result["skipped_missing_local"] += 1
                    continue
                try:
                    with open_controlled_file(
                        candidate.local_root, candidate.local_path
                    ) as (source, size):
                        source_info = os.fstat(source.fileno())
                        source_identity = (
                            source_info.st_dev,
                            source_info.st_ino,
                            source_info.st_mtime_ns,
                        )
                        if candidate.source == "transcript":
                            if (
                                candidate.sha256 is None
                                or candidate.expected_size is None
                                or size != candidate.expected_size
                            ):
                                raise BackupError("字幕工件在扫描后发生变化，已跳过。")
                            local_digest = hashlib.sha256()
                            while chunk := source.read(1024 * 1024):
                                local_digest.update(chunk)
                            if local_digest.hexdigest() != candidate.sha256:
                                raise BackupError("字幕工件在扫描后发生变化，已跳过。")
                            source.seek(0)
                        directory = candidate.remote_path[:-1]
                        if directory not in ensured:
                            self._ensure_directory(directory)
                            ensured.add(directory)
                        exists = self.provider.exists(candidate.remote_path)
                        overwrite = False
                        uploaded = False
                        if exists:
                            remote = self.provider.get_info(candidate.remote_path)
                            if not remote.is_directory and remote.size == size:
                                if candidate.source == "transcript":
                                    remote_sha256, remote_size = self._remote_digest(candidate)
                                    if (
                                        remote_size == size
                                        and remote_sha256 == candidate.sha256
                                    ):
                                        result["skipped_existing"] += 1
                                    else:
                                        overwrite = True
                                else:
                                    result["skipped_existing"] += 1
                            else:
                                overwrite = True
                        if not exists or overwrite:
                            if size >= self.multipart_threshold:
                                self.provider.multipart_upload(
                                    candidate.remote_path,
                                    source,
                                    overwrite=overwrite,
                                )
                            else:
                                self.provider.simple_upload(
                                    candidate.remote_path,
                                    source,
                                    overwrite=overwrite,
                                )
                            uploaded = True
                except BackupError:
                    if candidate.transcript_group is not None:
                        failed_transcript_groups.add(candidate.transcript_group)
                    result["skipped_missing_local"] += 1
                    continue

                # Never trust only an upload response or a previous exists check.
                # Re-read authoritative metadata before committing or unlinking.
                verified = self.provider.get_info(candidate.remote_path)
                if verified.is_directory or verified.size != size:
                    raise BackupError("云端文件校验失败，已保留本地文件。")
                if candidate.source == "transcript":
                    if candidate.sha256 is None:
                        raise BackupError("字幕工件备份记录无效，已保留本地文件。")
                    self._verify_remote_sha256(
                        candidate,
                        expected_sha256=candidate.sha256,
                        expected_size=size,
                    )
                    persisted = False
                elif candidate.source == "ai":
                    if candidate.record_id is None or candidate.sha256 is None:
                        raise BackupError("AI 附件备份记录无效，已保留受控副本。")
                    self._verify_remote_sha256(
                        candidate,
                        expected_sha256=candidate.sha256,
                        expected_size=size,
                    )
                    self.ai_file_service.record_cloud_backup(
                        candidate.record_id,
                        cloud_path="/".join(candidate.remote_path),
                        cloud_size=size,
                        cloud_sha256=candidate.sha256,
                        cloud_remote_id=getattr(verified, "etag", None),
                        remove_local=remove_local,
                    )
                    persisted = False
                    if remove_local:
                        result["local_removed"] += 1
                else:
                    persisted = self._persist_cloud_metadata(
                        candidate, size=size, backed_up_at=datetime.now(timezone.utc)
                    )
                if uploaded:
                    result["uploaded"] += 1
                if persisted and remove_local:
                    remove_controlled_file(
                        candidate.local_root,
                        candidate.local_path,
                        size,
                        expected_identity=source_identity,
                    )
                    result["local_removed"] += 1
                    self._clear_local_reference(candidate)
            except Exception:
                # Provider exceptions may contain credentials or signed URLs. Never
                # copy their text into a bridge DTO.
                if candidate.transcript_group is not None:
                    failed_transcript_groups.add(candidate.transcript_group)
                result["failed"] += 1
                if len(result["failures"]) < MAX_FAILURE_DETAILS:
                    safe_source = safe_path_component(
                        candidate.source,
                        fallback="unknown",
                        limit=32,
                    )
                    safe_remote_path = "/".join(
                        safe_path_component(part, fallback="未命名")
                        for part in candidate.remote_path
                    )
                    result["failures"].append(
                        {
                            "source": safe_source,
                            "name": current_name,
                            "remote_path": safe_remote_path,
                            "error": "上传失败，请检查云盘凭据或网络连接。",
                        }
                    )
            finally:
                done += 1
                report(current_name)
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        return result


class BackupManager:
    """Thread-safe owner for one daemon backup worker and its provider lifecycle."""

    def __init__(
        self,
        engine: Engine,
        *,
        archive_root: Path,
        mail_attachments_root: Path,
        mail_account: str = "",
        provider_factory: Callable[[], CloudStorageProvider] = SJTUCloudPanProvider,
        token_loader: Callable[[], str | None] | None = None,
        multipart_threshold: int = DEFAULT_MULTIPART_THRESHOLD,
        ai_file_service: AIManagedFileService | None = None,
        transcript_root: Path = DEFAULT_TRANSCRIPT_ROOT,
    ) -> None:
        self._engine = engine
        self._archive_root = Path(archive_root)
        self._mail_attachments_root = Path(mail_attachments_root)
        self._mail_account = mail_account
        self._provider_factory = provider_factory
        self._credential_checker = token_loader or user_token_saved
        self._multipart_threshold = multipart_threshold
        self._transcript_root = Path(transcript_root)
        self._ai_file_service = ai_file_service or AIManagedFileService(
            engine, archive_root=self._archive_root
        )
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._state: Literal["idle", "running", "finished"] = "idle"
        self._counts = {
            "canvas": 0,
            "mail": 0,
            "ready": 0,
            "cloud_only": 0,
            "missing_local": 0,
            "total": 0,
        }
        self._progress: dict[str, Any] | None = None
        self._last_result: dict[str, Any] | None = None

    def _service(self, provider: CloudStorageProvider | None = None) -> BackupService:
        return BackupService(
            self._engine,
            provider,
            archive_root=self._archive_root,
            mail_attachments_root=self._mail_attachments_root,
            mail_account=self._mail_account,
            multipart_threshold=self._multipart_threshold,
            ai_file_service=self._ai_file_service,
            transcript_root=self._transcript_root,
        )

    def _availability(self) -> tuple[bool, str | None]:
        try:
            configured = bool(self._credential_checker())
        except Exception:
            return False, "无法查询系统 Keychain 中的交大云盘凭据状态。"
        if not configured:
            return False, "尚未在系统 Keychain 中配置交大云盘 UserToken。"
        return True, None

    def status(self) -> dict[str, Any]:
        available, availability_message = self._availability()
        with self._lock:
            state = self._state
        if state in {"idle", "finished"}:
            try:
                counts = self._service().preview()
            except Exception:
                counts = None
            if counts is not None:
                with self._lock:
                    if self._state == state:
                        self._counts = counts
        with self._lock:
            return {
                "status": self._state,
                "available": available,
                "availability_message": availability_message,
                "counts": dict(self._counts),
                "progress": copy.deepcopy(self._progress),
                "last_result": copy.deepcopy(self._last_result),
            }

    def start(self, *, remove_local: bool = False) -> dict[str, str]:
        if type(remove_local) is not bool:
            raise BackupError("释放本地空间选项无效。")
        with self._lock:
            if self._closed:
                raise BackupError("备份服务已关闭。")
            if self._thread is not None and self._thread.is_alive():
                return {"status": "already_running"}
            self._cancel.clear()
            self._state = "running"
            self._progress = {
                "done": 0,
                "total": self._counts["total"],
                "current_name": None,
            }
            self._thread = threading.Thread(
                target=self._run,
                kwargs={"remove_local": remove_local},
                name="sjtu-cloud-backup",
                daemon=True,
            )
            self._thread.start()
            return {"status": "started"}

    def _update_progress(self, progress: dict[str, Any]) -> None:
        current_name = progress.get("current_name")
        safe_name = (
            safe_path_component(current_name, fallback="文件", limit=MAX_FILENAME_LENGTH)
            if current_name is not None
            else None
        )
        with self._lock:
            self._progress = {
                "done": max(0, int(progress.get("done", 0))),
                "total": max(0, int(progress.get("total", 0))),
                "current_name": safe_name,
            }

    def _fatal_result(self, started_at: str) -> dict[str, Any]:
        with self._lock:
            failed = self._counts["ready"] or 1
        return {
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "uploaded": 0,
            "skipped_existing": 0,
            "skipped_missing_local": 0,
            "local_removed": 0,
            "failed": failed,
            "failures": [
                {
                    "source": "backup",
                    "name": "云盘备份",
                    "remote_path": BACKUP_ROOT,
                    "error": "备份失败，请检查云盘凭据、网络连接后重试。",
                }
            ],
        }

    def _run(self, *, remove_local: bool) -> None:
        provider: CloudStorageProvider | None = None
        started_at = datetime.now(timezone.utc).isoformat()
        result: dict[str, Any] | None = None
        try:
            provider = self._provider_factory()
            service = self._service(provider)
            candidates = service.scan()
            counts = preview_counts(candidates)
            with self._lock:
                self._counts = counts
                self._progress = {"done": 0, "total": len(candidates), "current_name": None}
            result = service.backup(
                candidates,
                remove_local=remove_local,
                cancel_event=self._cancel,
                progress_callback=self._update_progress,
            )
        except Exception:
            # Provider, Keychain and database exceptions can contain sensitive values.
            result = self._fatal_result(started_at)
        finally:
            if provider is not None:
                close = getattr(provider, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            with self._lock:
                if result is not None:
                    self._last_result = result
                self._state = "finished"
                self._thread = None

    def close(self, timeout: float = 10.0) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
