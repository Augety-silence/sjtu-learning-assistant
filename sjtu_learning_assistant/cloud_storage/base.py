"""Provider contract for cloud-backed file storage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from .models import CloudDirectoryPage, CloudItem, UploadSession

RemotePath = str | tuple[str, ...] | list[str]


class CloudStorageProvider(ABC):
    @abstractmethod
    def list_directory(self, path: RemotePath = (), *, page: int = 1, page_size: int = 100) -> CloudDirectoryPage:
        raise NotImplementedError

    @abstractmethod
    def create_directory(self, path: RemotePath) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_info(self, path: RemotePath) -> CloudItem:
        raise NotImplementedError

    @abstractmethod
    def exists(self, path: RemotePath) -> bool:
        raise NotImplementedError

    @abstractmethod
    def download_stream(self, path: RemotePath, *, chunk_size: int = 64 * 1024, start: int | None = None, end: int | None = None) -> Iterator[bytes]:
        raise NotImplementedError

    @abstractmethod
    def download_temp(self, path: RemotePath, *, directory: Path | None = None) -> "TemporaryDownload":
        raise NotImplementedError

    @abstractmethod
    def delete(self, path: RemotePath, *, permanent: bool = False, missing_ok: bool = True) -> None:
        raise NotImplementedError

    @abstractmethod
    def move(self, source: RemotePath, destination: RemotePath, *, overwrite: bool = False) -> CloudItem:
        raise NotImplementedError

    @abstractmethod
    def copy(self, source: RemotePath, destination: RemotePath, *, overwrite: bool = False) -> CloudItem:
        raise NotImplementedError

    @abstractmethod
    def simple_upload(self, path: RemotePath, source: bytes | BinaryIO, *, overwrite: bool = False) -> CloudItem:
        raise NotImplementedError

    @abstractmethod
    def multipart_upload(self, path: RemotePath, source: bytes | BinaryIO, *, overwrite: bool = False, chunk_size: int = 4 * 1024 * 1024) -> CloudItem:
        raise NotImplementedError


class TemporaryDownload:
    """Context manager owning a downloaded temporary file."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> Path:
        return self.path

    def cleanup(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.cleanup()
